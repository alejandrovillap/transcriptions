#!/usr/bin/env python3
"""Minutes: a formal meeting minute (attendees, topics, decisions, action items with owner
and date, risks, open questions, next steps) as one self-contained HTML file, written in
the language of the meeting.

Source: the latest beautified version of the session when there is one (real speaker names,
topics already grouped), otherwise the transcript as Beautify sees it (hidden lines left out).
The project notes, the screenshot list and earlier minutes of the project are passed too.

Each run is a new version: projects/<project>/sessions/<sid>/minutes/mN.html, recorded in
minutes/manifest.json; a successful one is also copied to projects/<project>/docs/.

Used by minutes_api.py (web UI); also a console tool:
  python minutes.py <project> <sid|latest> [--model opus]
"""

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import claude_cli
import sessions as S

TEMPLATE = S.ROOT / "templates" / "minutes_template.html"

PROMPT = """\
Write the formal MINUTES of a recorded meeting as one self-contained HTML file.

## Inputs
- {source_desc}: {source}
{transcript_block}- Screenshots taken during the meeting (view them with the Read tool only when they help: a participant \
list, an agenda slide, a date or a name you need to confirm):
{shots}
- Template: {template}
  Copy its CSS (light and dark mode) and its structure and class names exactly. Never copy its example content.
{notes_block}{prev_block}
## Requirements
1. Language: write the whole minute (headings included) in the language that dominates the meeting. If the \
meeting mixes English and Spanish, use the one most of the speech is in. Keep names, product names and \
technical terms as they were said.
2. Sections, in this order (headings in the minute's language):
   meeting data (title, client/project if known, date, time range and duration, format, what it was prepared from) ·
   attendees (name, role/organization; unknown voices get a descriptive label, never an invented name) ·
   purpose of the meeting · topics discussed (numbered subsections, each a short factual summary with the key \
points, figures and arguments; not a transcript) · decisions (each one `<div class="decision">` with D1, D2…) · \
action items (a `<table class="actions">`, one `<tr class="action">` per item: #, task, owner, due date, status) · \
risks and blockers (`<div class="risk">`) · open questions (`<div class="open">`) · next steps (including the next \
meeting if one was mentioned).
   Leave a section out only if nothing in the meeting belongs to it; never pad it.
3. Be faithful: record only what was actually said or shown. Do not invent owners, dates, decisions or attendees. \
When an action item has no owner or no date, write "Por definir" / "TBD" (in the minute's language) with the \
`pill tbd` style. Distinguish a decision (agreed) from a proposal or an idea (only discussed).
4. Cite the moment for every decision, action item, risk and open question with a `<span class="ts">[HH:MM:SS]</span>` \
taken from the transcript or the beautified version.
5. Be concise and professional: a reader who missed the meeting must understand what was decided and who does what.
6. Footer: what it was generated from (session {sid}, {n_lines} transcript lines, {t_first}–{t_last}, {source_short}), \
"minutes version {v}", generated {now}, and a reminder to review names, dates and owners before sending.
7. Static HTML only: no JavaScript, no external resources, no images.
8. Output file: {out}
   Create it with the Write tool. Don't create, modify or delete any other file.
9. When finished, reply with one short line only: language · attendees · decisions · action items.
"""


def minutes_dir(project: str, sid: str) -> Path:
    return S.session_dir(project, sid) / "minutes"


def load_manifest(project: str, sid: str) -> dict:
    return S._read_json(minutes_dir(project, sid) / "manifest.json", {"versions": []})


def save_manifest(project: str, sid: str, manifest: dict):
    S._write_json(minutes_dir(project, sid) / "manifest.json", manifest)


def public_manifest(project: str, sid: str) -> dict:
    out = []
    for v in load_manifest(project, sid)["versions"]:
        v = dict(v)
        if v.get("html"):
            v["url"] = S.file_url(minutes_dir(project, sid) / v["html"])
        if v.get("docs_copy"):
            v["docs_url"] = S.file_url(v["docs_copy"])
        out.append(v)
    return {"versions": out}


def prepare(project: str, sid: str) -> dict:
    """Pick the source, allocate the next version and build the prompt."""
    settings = S.load_settings()
    psettings = S.load_project_settings(project)
    if not S.transcript_path(project, sid).exists():
        raise FileNotFoundError(f"Transcript not found: {S.transcript_path(project, sid)}")
    mdir = minutes_dir(project, sid)
    mdir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(project, sid)
    v = max([x["v"] for x in manifest["versions"]] + [0]) + 1

    # Transcript as Beautify sees it (hidden lines left out); always given for timestamps.
    text = S.beautify_text(project, sid)
    snapshot = mdir / f"input_m{v}.txt"
    snapshot.write_text(text, encoding="utf-8")
    entries = S.parse_transcript(text)
    n_lines = len([l for l in text.splitlines() if l.startswith("[")])
    t_first = entries[0]["time"] if entries else "—"
    t_last = entries[-1]["time"] if entries else "—"
    if not entries:
        raise ValueError("The transcript is empty: nothing to write minutes about")

    beautified = S.latest_version(S.load_manifest(project, sid))
    bpath = S.session_dir(project, sid) / beautified["html"] if beautified else None
    if bpath and bpath.exists():
        source, source_short = bpath, f"beautified version v{beautified['v']}"
        source_desc = (f"Beautified (annotated) version v{beautified['v']} of the meeting: speakers already "
                       "identified by name, topics grouped. Use it as the main source")
        transcript_block = (f"- Raw transcript snapshot (for exact [HH:MM:SS] times and anything the beautified "
                            f"version missed; {n_lines} lines, {t_first}–{t_last}): {snapshot}\n")
    else:
        source, source_short = snapshot, "raw transcript"
        source_desc = (f"Raw speech-to-text transcript ({n_lines} lines, {t_first}–{t_last}). Lines: "
                       "\"[HH:MM:SS] text\", \"[HH:MM:SS] [Speaker N] text\" (automatic diarization, weak hint), "
                       "\"[HH:MM:SS] [screenshot: path]\", \"[HH:MM:SS] [selection: text]\". Expect recognition errors")
        transcript_block = ""

    shots = [e["path"] for e in entries if e["kind"] == "screenshot" and Path(e["path"]).exists()]
    shots_txt = "\n".join(f"  - {p}" for p in shots[-40:]) or "  (none)"

    notes = S.load_notes(project).strip()
    notes_block = (
        f"- The user's notes for this project ({S.notes_path(project)}), AUTHORITATIVE for names, roles, the client "
        "and terms; apply every correction (heard → meant):\n"
        + "".join(f"    {l}\n" for l in notes.splitlines()) if notes else "")

    prev = [x for x in manifest["versions"] if x.get("status") == "done"]
    prev_block = ""
    if prev and (mdir / prev[-1]["html"]).exists():
        prev_block = (f"- Previous minutes of this session (m{prev[-1]['v']}): {mdir / prev[-1]['html']}\n"
                      "  For consistency only; the sources above are the truth.\n")

    out = mdir / f"m{v}.html"
    prompt = PROMPT.format(
        source_desc=source_desc, source=source, transcript_block=transcript_block, shots=shots_txt,
        template=TEMPLATE, notes_block=notes_block, prev_block=prev_block, sid=sid, n_lines=n_lines,
        t_first=t_first, t_last=t_last, source_short=source_short, v=v,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"), out=out,
    )
    record = {"v": v, "status": "running", "created": datetime.now().isoformat(timespec="seconds"),
              "html": out.name, "input": snapshot.name, "source": source_short, "lines": n_lines,
              "t_first": t_first, "t_last": t_last, "model": settings["beautify_model"]}
    manifest["versions"].append(record)
    save_manifest(project, sid, manifest)

    context_dir = psettings["context_dir"].strip()
    cwd = Path(context_dir).expanduser() if context_dir and Path(context_dir).expanduser().is_dir() else S.ROOT
    return {"project": project, "sid": sid, "v": v, "out": out, "prompt": prompt, "cwd": cwd,
            "settings": settings, "source": source_short, "lines": n_lines,
            "add_dirs": sorted({str(S.project_dir(project).resolve()), str(S.ROOT)}),
            "labels": {str(snapshot): "Reading the transcript", snapshot.name: "Reading the transcript",
                       str(TEMPLATE): "Studying the minutes template", TEMPLATE.name: "Studying the minutes template",
                       **({str(bpath): f"Reading beautified v{beautified['v']}", bpath.name:
                           f"Reading beautified v{beautified['v']}"} if bpath and bpath.exists() else {})}}


def claude_command(spec: dict) -> list[str]:
    tools = ["Read", "Write", "Glob", "Grep", "mcp__claude-context"]
    return claude_cli.build_command(spec["settings"]["beautify_model"], tools, spec["add_dirs"],
                                    accept_edits=True)


def html_stats(html: str) -> dict:
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    lang = re.search(r'<html[^>]*\blang="([a-zA-Z-]+)"', html)
    return {
        "title": re.sub(r"\s+", " ", title.group(1)).strip() if title else "",
        "lang": lang.group(1) if lang else "",
        "decisions": len(re.findall(r'class="decision\b', html)),
        "actions": len(re.findall(r'class="action\b', html)),
        "risks": len(re.findall(r'class="risk\b', html)),
        "open": len(re.findall(r'class="open\b', html)),
        "size": len(html.encode("utf-8")),
    }


def _slug(title: str) -> str:
    t = re.split(r"\s[—–-]\s", title, maxsplit=2)
    t = t[1] if len(t) > 1 else (t[0] if t else "")
    import unicodedata
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return "-".join(s.split("-")[:6]) or "meeting"


def finish(project: str, sid: str, v: int, status: str, **info) -> dict:
    """Record the outcome of a version; a successful one is copied to docs/."""
    manifest = load_manifest(project, sid)
    rec = next(x for x in manifest["versions"] if x["v"] == v)
    rec.update(info)
    rec["status"] = status
    rec["finished"] = datetime.now().isoformat(timespec="seconds")
    out = minutes_dir(project, sid) / rec["html"]
    if status == "done":
        if not out.exists() or out.stat().st_size < 500:
            rec["status"] = "failed"
            rec["error"] = "The model finished without writing the minutes."
        else:
            html = out.read_text(encoding="utf-8", errors="replace")
            rec.update(html_stats(html))
            docs = S.project_dir(project) / "docs"
            docs.mkdir(parents=True, exist_ok=True)
            dt = S.sid_datetime(sid)
            target = docs / f"{dt.strftime('%Y-%m-%d') if dt else sid}-{_slug(rec.get('title', ''))}-minutes.html"
            target.write_text(html, encoding="utf-8")
            rec["docs_copy"] = str(target)
    save_manifest(project, sid, manifest)
    return rec


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Write the formal minutes of a recorded meeting")
    ap.add_argument("project")
    ap.add_argument("sid", help="Session id, e.g. 20261018_100200 (or 'latest')")
    ap.add_argument("--model", default=None, help="Claude model alias (default from settings)")
    args = ap.parse_args()
    sid = S.list_sessions(args.project)[0]["sid"] if args.sid == "latest" else args.sid
    spec = prepare(args.project, sid)
    if args.model:
        spec["settings"]["beautify_model"] = args.model
    print(f"Minutes m{spec['v']} from {spec['source']} → {spec['out']}")
    started = time.time()
    proc = subprocess.Popen(claude_command(spec), cwd=spec["cwd"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    proc.stdin.write(spec["prompt"])
    proc.stdin.close()
    result = {}
    for line in proc.stdout:
        ev = claude_cli.parse_line(line)
        if not ev:
            continue
        for name, inp in claude_cli.tool_uses(ev):
            print(f"  [{time.time() - started:6.0f}s] {claude_cli.describe_tool(name, inp, spec['labels'])}")
        if ev.get("type") == "result":
            result = ev
    proc.wait()
    ok = proc.returncode == 0 and not result.get("is_error")
    rec = finish(args.project, sid, spec["v"], "done" if ok else "failed",
                 duration=round(time.time() - started), summary=(result.get("result") or "")[:500],
                 cost=result.get("total_cost_usd"),
                 **({} if ok else {"error": (result.get("result") or "Minutes failed")[-500:]}))
    print(f"Status: {rec['status']}  {rec.get('summary') or rec.get('error', '')}")
    print(f"Output: {spec['out']}")
    if rec.get("docs_copy"):
        print(f"Docs copy: {rec['docs_copy']}")
    sys.exit(0 if rec["status"] == "done" else 1)


if __name__ == "__main__":
    main()
