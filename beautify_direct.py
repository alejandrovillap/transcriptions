"""Direct Beautify (Windows build): the annotated transcript in ONE generation, without the
Claude Code agent loop.

The agent mode (beautify.claude_command) lets Claude read files, think and write the
document with tools. On long meetings that meant many minutes of silent thinking and
re-sent context before a single character was written. Here everything the model needs is
put in one prompt (transcript, screenshot list with OCR text, notes and glossary, recent
minutes for names, the style reference) and the HTML comes back as the reply:
  - Claude Code with no tools, no MCP servers and a short system prompt (`--tools ""`)
  - no extended thinking (MAX_THINKING_TOKENS=0), up to 64k output tokens per call
  - if the reply is cut off by the output limit, it asks for the continuation (up to
    MAX_PARTS calls) and joins the parts
  - progress: every streamed character is reported

The screenshots are referenced by their relative src (the model cannot look at them; their
OCR text is given instead). Used by server.py when beautify_engine == "claude_direct".
"""

import asyncio
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import claude_cli
import sessions as S
from beautify import _rel

MAX_PARTS = 4                  # one call + up to 3 continuations for very long meetings
OCR_PER_SHOT = 1200            # characters of OCR text per screenshot
OCR_TOTAL = 24000
MINUTES_CONTEXT = 8000         # characters of earlier minutes (plain text) for names/roles
SYSTEM = ("You turn raw meeting transcripts into complete, self-contained HTML documents. "
          "Reply with the HTML document only: no markdown fences, no comments before or after it.")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "section", "aside"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def _plain(html: str) -> str:
    p = _Text()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n", "".join(p.out)).strip()


def build_prompt(spec: dict) -> str:
    project, sid = spec["project"], spec["sid"]
    sdir = S.session_dir(project, sid)
    text = spec["input"].read_text(encoding="utf-8", errors="replace")

    # Requirements: the agent prompt's own list, minus the file-writing instructions.
    req = spec["prompt"].split("## Requirements", 1)[1].split("8. Output file:", 1)[0]
    req = req.replace("Use the exact relative src listed for each screenshot above.",
                      "Use the exact src given for each screenshot in the SCREENSHOTS section.")

    shots, ocr_total = [], 0
    for e in S.parse_transcript(text):
        if e["kind"] != "screenshot" or not Path(e["path"]).exists():
            continue
        p = Path(e["path"])
        ocr = ""
        txt = p.with_suffix(".txt")
        if txt.exists() and ocr_total < OCR_TOTAL:
            ocr = txt.read_text(encoding="utf-8", errors="replace").strip()[:OCR_PER_SHOT]
            ocr_total += len(ocr)
        shots.append(f'- {e["time"]}  src="{_rel(p, sdir)}"' + (f"\n  OCR text: {ocr}" if ocr else ""))

    notes = S.load_notes(project).strip()
    try:
        import vault
        glossary = vault.glossary_block(project).strip()
    except Exception:
        glossary = ""

    docs = S.project_dir(project) / "docs"
    minutes_txt = ""
    if docs.exists():
        for f in sorted(p for p in docs.glob("*") if re.search(r"minut", p.name, re.I))[-2:]:
            minutes_txt += f"--- {f.name} ---\n" + _plain(f.read_text(encoding="utf-8", errors="replace")) + "\n"
        minutes_txt = minutes_txt[-MINUTES_CONTEXT:]

    dt = S.sid_datetime(sid)
    parts = [
        "Create the annotated transcript of a recorded meeting as ONE complete, self-contained HTML document."
        + (f"\nThe meeting took place on {dt:%Y-%m-%d} (use this date in the title and header; "
           "it is not the generation date)." if dt else ""),
        "## Requirements" + req.rstrip(),
        "10. You cannot open the screenshots; use the OCR text below to say what each one shows. "
        "Reply with the complete HTML document only.",
    ]
    if notes or glossary:
        parts.append("## USER NOTES (authoritative: names, roles, client, terms; apply every "
                     "'heard → meant' correction everywhere)\n" + "\n".join(x for x in (notes, glossary) if x))
    if minutes_txt:
        parts.append("## EARLIER MINUTES OF THIS PROJECT (only for names, roles and context)\n" + minutes_txt)
    parts.append("## SCREENSHOTS (time, exact src to use, OCR text)\n" + ("\n".join(shots) or "(none)"))
    parts.append("## STYLE REFERENCE (copy its CSS and structure, never its content)\n"
                 + Path(spec["style"]).read_text(encoding="utf-8", errors="replace"))
    parts.append("## RAW TRANSCRIPT\n" + text)
    return "\n\n".join(parts) + "\n"


def _command(model: str) -> list[str]:
    exe = claude_cli.claude_binary()
    if not exe:
        raise RuntimeError("Claude Code CLI ('claude') not found in PATH")
    return [exe, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", model or "sonnet", "--tools", "", "--system-prompt", SYSTEM,
            "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]


def _clean(html: str) -> str:
    html = html.strip()
    html = re.sub(r"^```(?:html)?\s*\n", "", html)
    html = re.sub(r"\n```\s*$", "", html)
    return html


async def _one_call(prompt: str, model: str, cwd: str, on_chars, register_proc) -> tuple[str, dict]:
    env = {**os.environ, "MAX_THINKING_TOKENS": "0", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "64000"}
    proc = await asyncio.create_subprocess_exec(
        *_command(model), cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
    if register_proc:
        register_proc(proc)
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    out, result = [], {}
    async for raw in proc.stdout:
        ev = claude_cli.parse_line(raw)
        if not ev:
            continue
        d = claude_cli.text_delta(ev)
        if d:
            out.append(d)
            on_chars(len(d))
        if ev.get("type") == "result":
            result = ev
    err = (await proc.stderr.read()).decode("utf-8", errors="replace")
    await proc.wait()
    if proc.returncode != 0 or result.get("is_error"):
        raise RuntimeError((result.get("result") or err or "Claude failed").strip()[-600:])
    return "".join(out) or (result.get("result") or ""), result


async def run(spec: dict, on_chars, on_step, register_proc=None) -> dict:
    """Generate spec["out"]. Returns {"cost": float, "parts": int}."""
    import process_transcript2 as pt
    model = spec["settings"].get("beautify_model") or "sonnet"
    base_prompt = build_prompt(spec)
    on_step(f"Generando el documento en una sola pasada ({len(base_prompt) // 1000} K caracteres de entrada)")
    html, cost, parts = "", 0.0, 0
    prompt = base_prompt
    while parts < MAX_PARTS:
        parts += 1
        chunk, result = await _one_call(prompt, model, str(S.ROOT), on_chars, register_proc)
        cost += result.get("total_cost_usd") or 0.0
        html += chunk if parts == 1 else chunk.lstrip("\n")
        if re.search(r"</html>\s*$", _clean(html), re.I):
            break
        # Cut off by the output limit: ask for the rest.
        on_step(f"Documento largo: pidiendo la continuación (parte {parts + 1})")
        prompt = (base_prompt + "\n## ALREADY WRITTEN (your previous reply was cut off by the length limit)\n"
                  "Here are the last characters of the document so far:\n<<<\n" + html[-6000:] + "\n>>>\n"
                  "Continue the document EXACTLY where it stops: no repetition, no restart, no fences, "
                  "and finish it with </body></html>.")
    html = _clean(html)
    if not re.search(r"</html>\s*$", html, re.I):
        raise RuntimeError("The document is still incomplete after several parts (meeting too long)")
    Path(spec["out"]).write_text(pt.fix_unrendered_templates(html), encoding="utf-8")
    return {"cost": round(cost, 4), "parts": parts}
