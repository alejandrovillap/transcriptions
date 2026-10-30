"""Rename a session, or move it to another project (Windows build).

Kept out of server.py so upstream merges stay easy; server.py only does
`app.include_router(session_admin_api.router)` and sets `session_admin_api.busy`.

  POST /api/projects/{project}/sessions/{sid}/rename  {"title": "..."}   ('' clears it)
  POST /api/projects/{project}/sessions/{sid}/move    {"project": "..."} (created if missing)

The title lives in sessions/<sid>/meta.json (sessions.session_title reads it). A move takes
every file of the session along (transcripts, Beautify versions, minutes, chat, frames, the
.pkl, recordings, its screenshots and the docs copies) and rewrites the paths stored inside
them, so the session keeps working in its new project. A live or busy session is refused.
"""

from __future__ import annotations

import asyncio
import json
import pickle
import re
import shutil
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException

import sessions as S

router = APIRouter()
busy = lambda project, sid: ""   # noqa: E731 — server.py: reason the session can't be moved, '' if free

TEXT_SUFFIXES = {".txt", ".json", ".jsonl", ".html", ".md"}


def _err(e: Exception, code: int = 400):
    raise HTTPException(code, str(e))


# ── rename ──────────────────────────────────────────────────────────────────

def set_title(project: str, sid: str, title: str) -> str:
    title = " ".join((title or "").split())[:120]
    d = S.session_dir(project, sid)
    if not S.transcript_path(project, sid).exists() and not d.exists():
        raise ValueError("Session not found")
    d.mkdir(parents=True, exist_ok=True)
    meta = S._read_json(d / "meta.json", {})
    meta["title"] = title
    S._write_json(d / "meta.json", meta)
    return title


# ── move ────────────────────────────────────────────────────────────────────

def _path_rewriter(old: str, new: str):
    """Replace the project's folder in every spelling the files use: absolute or relative
    (projects\\OLD\\...), JSON-escaped backslashes, forward slashes, and /files/OLD/ URLs."""
    root = re.escape(S.PROJECTS_DIR.name)
    sep = r"(?:\\\\|\\|/)"
    pat = re.compile(rf"({root}{sep}){re.escape(old)}(?={sep})")
    url_old, url_new = f"/files/{quote(old)}/", f"/files/{quote(new)}/"

    def fix(text: str) -> str:
        return pat.sub(lambda m: m.group(1) + new, text).replace(url_old, url_new)
    return fix


def _walk(obj, fix):
    if isinstance(obj, str):
        return fix(obj)
    if isinstance(obj, dict):
        return {k: _walk(v, fix) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fix) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_walk(v, fix) for v in obj)
    return obj


def _docs_copies(manifest_path: Path) -> list[str]:
    data = S._read_json(manifest_path, {})
    return [v["docs_copy"] for v in data.get("versions", []) if v.get("docs_copy")]


def session_files(project: str, sid: str) -> tuple[list[Path], list[Path]]:
    """(files to move, files to copy) for a session; paths in the old project."""
    root = S.project_dir(project)
    sid = S.safe_name(sid)
    move: list[Path] = []
    tdir = root / "transcripts"
    move += sorted(tdir.glob(f"transcript_{sid}*.txt"))
    move += sorted((tdir / "rediarized").glob(f"transcript_{sid}*.txt"))
    for d in (S.session_dir(project, sid), S.frames_dir(project, sid)):
        if d.exists():
            move += [f for f in d.rglob("*") if f.is_file()]
    move += sorted(root.glob(f"session_{sid}*.pkl")) + sorted(root.glob(f"recording_{sid}*"))

    # Docs copies of its Beautify versions and minutes.
    sdir = S.session_dir(project, sid)
    for m in (sdir / "manifest.json", sdir / "minutes" / "manifest.json"):
        for p in _docs_copies(m):
            p = Path(p)
            if not p.is_absolute():
                p = S.PROJECTS_DIR.parent / p
            if p.exists() and p.resolve().is_relative_to(root.resolve()):
                move.append(p)

    # Screenshots referenced by its transcripts; copied, not moved, when another session
    # of the old project also uses them.
    shots_dir = (root / "screenshots").resolve()
    own_text = "".join(f.read_text(encoding="utf-8", errors="replace")
                       for f in move if f.parent.name in ("transcripts", "rediarized"))
    others = "".join(f.read_text(encoding="utf-8", errors="replace")
                     for f in tdir.glob("transcript_*.txt") if not f.name.startswith(f"transcript_{sid}"))
    copy: list[Path] = []
    for ref in sorted(set(re.findall(r"\[screenshot: ([^\]]+)\]", own_text))):
        p = Path(ref.strip())
        if not p.is_absolute():
            p = S.PROJECTS_DIR.parent / p
        if not p.exists() or p.resolve().parent != shots_dir:
            continue
        group = [f for f in p.parent.glob(p.stem + ".*") if f.is_file()]   # .png + OCR .txt (+ caches)
        (copy if p.name in others else move).extend(group)
    return list(dict.fromkeys(move)), list(dict.fromkeys(f for f in copy if f not in move))


def move_session(project: str, sid: str, target: str) -> dict:
    target = S.safe_name(target)
    if target == project:
        raise ValueError("The session is already in that project")
    if not S.transcript_path(project, sid).exists():
        raise ValueError("Session not found")
    old_root, new_root = S.project_dir(project), S.project_dir(target)
    move, copy = session_files(project, sid)
    plan = [(f, new_root / f.relative_to(old_root), False) for f in move]
    plan += [(f, new_root / f.relative_to(old_root), True) for f in copy]
    clash = [str(dst.relative_to(new_root)) for _, dst, _ in plan if dst.exists()]
    if clash:
        raise ValueError(f"Already in '{target}': {', '.join(clash[:3])}")

    for sub in ("transcripts", "screenshots", "docs"):
        (new_root / sub).mkdir(parents=True, exist_ok=True)
    fix = _path_rewriter(project, target)
    for src, dst, keep in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in TEXT_SUFFIXES:
            text = src.read_text(encoding="utf-8", errors="surrogateescape")
            dst.write_text(fix(text), encoding="utf-8", errors="surrogateescape", newline="")
            shutil.copystat(src, dst)
            if not keep:
                src.unlink()
        elif src.suffix == ".pkl":
            with open(src, "rb") as fh:
                data = pickle.load(fh)
            with open(dst, "wb") as fh:
                pickle.dump(_walk(data, fix), fh)
            shutil.copystat(src, dst)
            src.unlink()
        elif keep:
            shutil.copy2(src, dst)
        else:
            shutil.move(str(src), str(dst))

    # Drop the folders the session left empty.
    for d in (S.session_dir(project, sid), S.frames_dir(project, sid)):
        if d.exists():
            for sub in sorted((p for p in d.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                if not any(sub.iterdir()):
                    sub.rmdir()
            if not any(d.iterdir()):
                d.rmdir()
    return {"project": target, "sid": sid, "moved": len(move), "copied": len(copy)}


# ── routes ──────────────────────────────────────────────────────────────────

@router.post("/api/projects/{project}/sessions/{sid}/rename")
async def rename(project: str, sid: str, data: dict = Body(...)):
    try:
        title = await asyncio.to_thread(set_title, project, sid, str(data.get("title", "")))
    except ValueError as e:
        _err(e)
    return {"project": project, "sid": sid, "title": title}


@router.post("/api/projects/{project}/sessions/{sid}/move")
async def move(project: str, sid: str, data: dict = Body(...)):
    reason = busy(project, sid)
    if reason:
        raise HTTPException(409, reason)
    try:
        return await asyncio.to_thread(move_session, project, sid, str(data.get("project", "")))
    except ValueError as e:
        _err(e)
