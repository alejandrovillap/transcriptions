"""Downloads (Windows build): a session's transcript (.txt), annotated version (.html) and
minutes (.html) as files with readable names. The annotated version gets its screenshots
embedded (JPEG data URIs), so it opens anywhere without the project folder.

server.py only does `app.include_router(downloads_api.router)`.

  GET /api/projects/{project}/sessions/{sid}/download/transcript
  GET /api/projects/{project}/sessions/{sid}/download/beautified[?v=N]   (default: latest done)
  GET /api/projects/{project}/sessions/{sid}/download/minutes[?v=N]      (default: latest done)
"""

import asyncio
import base64
import io
import re
import unicodedata
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

import minutes
import sessions as S

router = APIRouter()
IMG_MAX_WIDTH = 1600          # embedded screenshots are shrunk to this width…
IMG_JPEG_QUALITY = 80         # …and re-encoded, so a long meeting stays a few MB


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-").lower() or "sesion"


def _base_name(project: str, sid: str) -> str:
    dt = S.sid_datetime(sid)
    when = dt.strftime("%Y-%m-%d_%H%M") if dt else sid
    return f"{_slug(project)}_{when}"


def _pick(versions: list, v: int | None) -> dict:
    done = [x for x in versions if x.get("status") == "done"]
    if v is not None:
        done = [x for x in done if x.get("v") == v]
    if not done:
        raise HTTPException(404, "Nothing generated yet for this session")
    return done[-1]


def _embed_images(html: str, base_dir: Path) -> str:
    """Replace relative <img src="..."> files with JPEG data URIs."""
    from PIL import Image

    cache: dict[str, str] = {}

    def data_uri(rel: str) -> str | None:
        if rel in cache:
            return cache[rel]
        p = (base_dir / rel).resolve()
        if not p.is_file():
            return None
        try:
            with Image.open(p) as im:
                im = im.convert("RGB")
                if im.width > IMG_MAX_WIDTH:
                    im = im.resize((IMG_MAX_WIDTH, round(im.height * IMG_MAX_WIDTH / im.width)))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=IMG_JPEG_QUALITY, optimize=True)
        except Exception:
            return None
        cache[rel] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        return cache[rel]

    def repl(m):
        rel = m.group(2)
        if rel.startswith(("data:", "http:", "https:")):
            return m.group(0)
        uri = data_uri(rel)
        return f'{m.group(1)}"{uri}"' if uri else m.group(0)

    return re.sub(r'(<img\b[^>]*?\bsrc=)"([^"]+)"', repl, html, flags=re.I)


def _file(content: bytes, filename: str, media: str) -> Response:
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _build(project: str, sid: str, kind: str, v: int | None) -> tuple[bytes, str, str]:
    base = _base_name(project, sid)
    if kind == "transcript":
        text = S.read_transcript(project, sid)
        if not text.strip():
            raise HTTPException(404, "The transcript is empty")
        # BOM so Notepad / Word open the accents correctly
        return ("﻿" + text).encode("utf-8"), f"{base}_transcripcion.txt", "text/plain; charset=utf-8"
    if kind == "beautified":
        rec = _pick(S.load_manifest(project, sid)["versions"], v)
        path = S.session_dir(project, sid) / rec["html"]
        html = _embed_images(path.read_text(encoding="utf-8", errors="replace"), path.parent)
        return html.encode("utf-8"), f"{base}_anotada-v{rec['v']}.html", "text/html; charset=utf-8"
    if kind == "minutes":
        rec = _pick(minutes.load_manifest(project, sid)["versions"], v)
        path = minutes.minutes_dir(project, sid) / rec["html"]
        return path.read_bytes(), f"{base}_minuta-m{rec['v']}.html", "text/html; charset=utf-8"
    raise HTTPException(400, "Unknown download")


@router.get("/api/projects/{project}/sessions/{sid}/download/{kind}")
async def download(project: str, sid: str, kind: str, v: int | None = None):
    try:
        content, name, media = await asyncio.to_thread(_build, project, sid, kind, v)
    except ValueError as e:      # bad project / session name
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "File not found")
    return _file(content, name, media)


@router.get("/api/projects/{project}/sessions/{sid}/downloads")
async def available(project: str, sid: str):
    """What can be downloaded for this session (for the Download menu)."""
    def collect():
        out = {"transcript": S.transcript_path(project, sid).exists()}
        done = [x for x in S.load_manifest(project, sid)["versions"] if x.get("status") == "done"]
        out["beautified"] = done[-1]["v"] if done else None
        mdone = [x for x in minutes.load_manifest(project, sid)["versions"] if x.get("status") == "done"]
        out["minutes"] = mdone[-1]["v"] if mdone else None
        return out
    try:
        return await asyncio.to_thread(collect)
    except ValueError as e:
        raise HTTPException(400, str(e))
