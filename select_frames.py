#!/usr/bin/env python3
"""Pick the few auto frames that belong in the text, and promote them to real screenshots.

Which frame matters is decided by the transcript, not the picture: what was being said while
the scene was on screen. Per scene (revisits of the same image are pooled) we compute
  deixis    "вот здесь", "как видите", "on this slide"… — the speaker points at the screen
  duration  how long it was shown
  words     how much was said meanwhile
  novelty   how different it was from the previous scene
and keep the top N. Planned next: OCR-word overlap with the speech (Level 2) and a final
pick by a text LLM given the transcript + scene list.

A promoted frame becomes screenshots/screenshot_<time>.png plus a "[screenshot: …]" line in
the transcript at its own time, so OCR / Beautify / chat treat it like a hand-made one.

  python select_frames.py <project> <sid> [-n 10] [--promote] [--ocr]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import sessions as S

GRACE_SEC = 4.0          # speech right after a scene disappears often still refers to it
MAX_ENTRY_SEC = 30.0     # a transcript line is assumed to cover at most this long

DEIXIS = [
    r"вот\s+(?:здесь|тут|это|эт[аоуи]т?|так|сюда|видно)", r"как\s+(?:вы\s+)?видите", r"как\s+видно",
    r"(?:по)?смотрите", r"видим", r"видите", r"здесь\s+у\s+нас", r"тут\s+у\s+нас", r"сюда",
    r"покажу", r"показываю", r"на\s+(?:этом|этой|экране|слайде|схеме|картинке|диаграмме)",
    r"(?:этот|эта|это)\s+(?:слайд|схема|экран|диаграмма|таблица|график|страница|окно)",
    r"here\s+(?:you\s+)?(?:can\s+)?see", r"as\s+you\s+(?:can\s+)?see", r"look\s+at",
    r"(?:this|that)\s+(?:slide|diagram|screen|page|chart|table|window)", r"over\s+here",
    r"on\s+the\s+screen", r"let\s+me\s+show", r"you\s+can\s+see",
]
DEIXIS_RE = re.compile(r"(?<!\w)(?:" + "|".join(DEIXIS) + r")(?!\w)", re.IGNORECASE)


def frames_dir(project: str, sid: str) -> Path:
    return S.project_dir(project) / "frames" / sid


def load_scenes(project: str, sid: str) -> list[dict]:
    p = frames_dir(project, sid) / "scenes.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["scenes"]


def save_scenes(project: str, sid: str, scenes: list[dict]):
    p = frames_dir(project, sid) / "scenes.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["scenes"] = scenes
    S._write_json(p, data)


def timed_entries(project: str, sid: str) -> list[dict]:
    """Transcript entries with absolute start/end datetimes."""
    entries = S.parse_transcript(S.read_transcript(project, sid))
    day = S.sid_datetime(sid) or datetime.now()
    out, prev = [], None
    for e in entries:
        h, m, s = map(int, e["time"].split(":"))
        dt = day.replace(hour=h, minute=m, second=s, microsecond=0)
        if prev and dt < prev - timedelta(hours=12):   # crossed midnight
            day += timedelta(days=1)
            dt += timedelta(days=1)
        prev = dt
        out.append({**e, "at": dt})
    for i, e in enumerate(out):
        nxt = out[i + 1]["at"] if i + 1 < len(out) else e["at"] + timedelta(seconds=MAX_ENTRY_SEC)
        e["until"] = min(nxt, e["at"] + timedelta(seconds=MAX_ENTRY_SEC))
    return out


def assign_speech(entries, scenes) -> dict[int, list[str]]:
    """Give each speech line to the one scene it overlaps most (scene intervals extended by
    GRACE_SEC), so a line is never counted for two scenes."""
    spans = [(sc["id"], datetime.fromisoformat(sc["wall_start"]),
              datetime.fromisoformat(sc["wall_end"]) + timedelta(seconds=GRACE_SEC)) for sc in scenes]
    out: dict[int, list[str]] = {sc["id"]: [] for sc in scenes}
    for e in entries:
        if e["kind"] != "speech":
            continue
        best, best_ov = None, 0.0
        for sid_, start, end in spans:
            ov = (min(end, e["until"]) - max(start, e["at"])).total_seconds()
            if ov > best_ov:
                best, best_ov = sid_, ov
        if best is not None:
            out[best].append(e["text"])
    return out


def score_scenes(project: str, sid: str) -> list[dict]:
    scenes = load_scenes(project, sid)
    speech = assign_speech(timed_entries(project, sid), scenes)
    groups: dict[int, dict] = {}
    for sc in scenes:
        key = sc.get("same_as") or sc["id"]
        g = groups.setdefault(key, {"id": key, "scene": None, "shows": [], "duration": 0.0,
                                    "deixis": [], "words": 0})
        if sc["id"] == key:
            g["scene"] = sc
        start, end = datetime.fromisoformat(sc["wall_start"]), datetime.fromisoformat(sc["wall_end"])
        text = " ".join(speech[sc["id"]])
        g["shows"].append([sc["wall_start"], sc["wall_end"]])
        g["duration"] += max(0.0, (end - start).total_seconds())
        g["deixis"] += [m.group(0) for m in DEIXIS_RE.finditer(text)]
        g["words"] += len(text.split())
    rows = []
    for g in groups.values():
        sc = g["scene"]
        if sc is None:
            continue
        f = {
            "deixis": len(g["deixis"]),
            "duration": round(g["duration"], 1),
            "words": g["words"],
            "novelty": sc.get("changed", 0),
            "edges": sc.get("edges", 0),
            "shows": len(g["shows"]),
        }
        score = (1.5 * min(f["deixis"], 3)
                 + math.log2(1 + f["duration"] / 10)
                 + min(f["words"] / 80, 1.0)
                 + (0.5 if f["novelty"] >= 0.1 else 0.0)
                 - (1.0 if f["edges"] < 0.02 else 0.0)      # little detail: likely a camera picture
                 - (0.5 if sc.get("scroll_of") else 0.0))
        if sc.get("cls") == "face":                        # Level 1, when present
            score -= 3
        rows.append({"id": sc["id"], "path": sc.get("last_path") or sc["path"], "first_path": sc["path"],
                     "wall_start": sc["wall_start"], "shows": g["shows"], "features": f,
                     "deixis": g["deixis"][:6], "score": round(score, 3)})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def select(project: str, sid: str, n: int = 10) -> dict:
    rows = score_scenes(project, sid)
    for i, r in enumerate(rows):
        r["selected"] = i < n
    result = {"sid": sid, "n": n, "created": datetime.now().isoformat(timespec="seconds"),
              "method": "heuristic-v0", "scenes": rows}
    S._write_json(frames_dir(project, sid) / "selection.json", result)
    return result


# ── Promote ──────────────────────────────────────────────────────────────────

def insert_transcript_line(path: Path, at: datetime, line_body: str):
    """Insert "[HH:MM:SS] body" before the first line that is later than *at*."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    day = S.sid_datetime(path.stem.removeprefix("transcript_")) or at
    prev = None
    pos = len(lines)
    for i, ln in enumerate(lines):
        m = S.LINE_RE.match(ln.rstrip("\n"))
        if not m:
            continue
        h, mi, s = map(int, m.group(1).split(":"))
        dt = day.replace(hour=h, minute=mi, second=s, microsecond=0)
        if prev and dt < prev - timedelta(hours=12):   # crossed midnight
            day += timedelta(days=1)
            dt += timedelta(days=1)
        prev = dt
        if dt > at:
            pos = i
            break
    stamp = at.strftime("%H:%M:%S")
    if lines and not lines[-1].endswith("\n") and pos == len(lines):
        lines[-1] += "\n"
    lines.insert(pos, f"[{stamp}] {line_body}\n")
    path.write_text("".join(lines), encoding="utf-8")


def promote(project: str, sid: str, scene_id: int, use_last: bool = True, ocr_reader=None) -> str:
    """Turn a scene into a regular screenshot in the transcript. Returns the PNG path."""
    import cv2
    scenes = load_scenes(project, sid)
    sc = next((s for s in scenes if s["id"] == scene_id), None)
    if sc is None:
        raise KeyError(f"No scene {scene_id}")
    if sc.get("extra", {}).get("promoted"):
        return sc["extra"]["promoted"]
    src = (sc.get("last_path") if use_last else None) or sc["path"]
    at = datetime.fromisoformat(sc["wall_start"])
    shots = (S.project_dir(project) / "screenshots").resolve()
    shots.mkdir(parents=True, exist_ok=True)
    dst = shots / f"screenshot_{at:%Y%m%d_%H%M%S_%f}"[:-3]
    dst = dst.with_name(dst.name + ".png")
    img = cv2.imread(src)
    if img is None:
        raise FileNotFoundError(src)
    cv2.imwrite(str(dst), img)
    if ocr_reader is not None:
        dst.with_suffix(".txt").write_text("\n".join(ocr_reader.readtext(str(dst), detail=0, paragraph=True)),
                                           encoding="utf-8")
    insert_transcript_line(S.transcript_path(project, sid), at, f"[screenshot: {dst}]")
    sc.setdefault("extra", {})["promoted"] = str(dst)
    save_scenes(project, sid, scenes)
    return str(dst)


def frames_payload(project: str, sid: str) -> dict:
    """Scenes (+ the last selection) for the web UI."""
    scenes = load_scenes(project, sid)
    sel_path = frames_dir(project, sid) / "selection.json"
    selection = json.loads(sel_path.read_text(encoding="utf-8")) if sel_path.exists() else None
    scores = {r["id"]: r for r in (selection or {}).get("scenes", [])}
    out = []
    for sc in scenes:
        r = scores.get(sc["id"])
        out.append({**sc, "url": S.file_url(sc["path"]),
                    "last_url": S.file_url(sc["last_path"]) if sc.get("last_path") else None,
                    "score": r["score"] if r else None, "selected": bool(r and r["selected"]),
                    "features": r["features"] if r else None, "deixis": r["deixis"] if r else []})
    return {"scenes": out, "selection": {k: v for k, v in (selection or {}).items() if k != "scenes"} or None}


def main():
    ap = argparse.ArgumentParser(description="Score auto frames against the transcript and pick the top N")
    ap.add_argument("project")
    ap.add_argument("sid")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--promote", action="store_true", help="Insert the selected frames into the transcript")
    ap.add_argument("--ocr", action="store_true", help="OCR promoted frames (EasyOCR)")
    ap.add_argument("--ids", default="", help="Promote these scene ids (comma-separated) instead of selecting")
    a = ap.parse_args()
    reader = None
    if a.ocr:
        import easyocr
        reader = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
    if a.ids:
        for i in a.ids.split(","):
            print("promoted:", promote(a.project, a.sid, int(i), ocr_reader=reader))
        return
    res = select(a.project, a.sid, a.n)
    if not res["scenes"]:
        sys.exit(f"No scenes in {frames_dir(a.project, a.sid)}")
    for r in res["scenes"]:
        f = r["features"]
        print(f"{'★' if r['selected'] else ' '} #{r['id']:<4} {r['wall_start'][11:19]}  score {r['score']:5.2f}  "
              f"deixis {f['deixis']}  {f['duration']:6.1f}s  {f['words']:4d} words  ×{f['shows']}  "
              f"{', '.join(r['deixis'][:3])}")
    if a.promote:
        for r in res["scenes"]:
            if r["selected"]:
                print("promoted:", promote(a.project, a.sid, r["id"], ocr_reader=reader))


if __name__ == "__main__":
    main()
