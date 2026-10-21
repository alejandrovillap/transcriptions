"""Meetings vault: a private folder of Markdown notes, one per meeting, plus a glossary and
(later) a living project state per project. It is the memory the app builds over time.

Layout (settings["vault_dir"], e.g. C:\\Users\\you\\meetings-vault):

    <vault>/
      _index.md                       all projects, latest meetings first
      <project>/
        _index.md                     the project's meetings, latest first
        Glosario.md                   terms and "heard" → "meant" corrections (user-edited)
        Reuniones/
          2026-10-19_1734_<slug>.md   one note per session, rewritten by newer minutes

Notes use the KnowledgeBase format (YAML frontmatter + Markdown), so its tools (index,
search, merge) work on the vault too; the vault itself is never published. When the vault
is a git repository (created on first use), every archived minute is committed locally.

The conversion from the minutes HTML is deterministic (no model call): minutes.py writes
HTML from templates/minutes_template.html, whose structure is known.
"""

import re
import subprocess
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import sessions as S

MEETINGS_DIR = "Reuniones"
GLOSSARY = "Glosario.md"
TS_RE = re.compile(r"\s*\[(\d{2}:\d{2}:\d{2})\]\s*")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
CREATE_NO_WINDOW = 0x08000000

GLOSSARY_TEMPLATE = """\
# Glosario · {project}

Nombres, siglas y términos de este proyecto. El reconocimiento de voz los usa en cada
grabación (Whisper como pistas; Parakeet con las correcciones), y la versión anotada y la
minuta los toman como la forma correcta de escribirlos.

## Términos
<!-- Uno por línea: "- Término" o "- Término — qué es". -->

## Correcciones (se oyó → debe ser)
<!-- Ejemplo: - "Pemec" → "Pemex" -->
"""


# ── Paths ─────────────────────────────────────────────────────────────────────

def vault_dir() -> Path | None:
    v = (S.load_settings().get("vault_dir") or "").strip()
    return Path(v).expanduser() if v else None


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .") or "proyecto"


def project_dir(project: str) -> Path | None:
    v = vault_dir()
    return v / _safe(project) if v else None


def glossary_path(project: str, create: bool = True) -> Path | None:
    """The project's glossary; created from the template the first time (when create)."""
    pdir = project_dir(project)
    if pdir is None:
        return None
    path = pdir / GLOSSARY
    if create and not path.exists():
        ensure_vault()
        pdir.mkdir(parents=True, exist_ok=True)
        path.write_text(GLOSSARY_TEMPLATE.format(project=project), encoding="utf-8")
    return path


def glossary_block(project: str) -> str:
    """Prompt lines with the project's glossary, or "" when it has no entries yet."""
    path = glossary_path(project, create=False)
    if path is None or not path.exists():
        return ""
    entries = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
               if re.match(r"^\s*[-*]\s+\S", l) and "<!--" not in l]
    if not entries:
        return ""
    return (f"- The project glossary ({path}), AUTHORITATIVE for how names, acronyms and terms are written; "
            "apply its corrections (heard → meant) too:\n" + "".join(f"    {l.strip()}\n" for l in entries))


def context_dir(project: str, psettings: dict | None = None) -> str:
    """Folder where Claude runs for Beautify / Minutes / Ask: the project's own setting, or
    else the project's folder in the vault (so Claude can read earlier meetings)."""
    psettings = psettings or S.load_project_settings(project)
    own = (psettings.get("context_dir") or "").strip()
    if own:
        return own
    pdir = project_dir(project)
    if pdir is not None:
        pdir.mkdir(parents=True, exist_ok=True)
        return str(pdir)
    return ""


def ensure_vault() -> Path | None:
    v = vault_dir()
    if v is None:
        return None
    v.mkdir(parents=True, exist_ok=True)
    readme = v / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Meetings vault\n\nMemoria privada de reuniones creada por voice-rec studio "
            "(transcription-and-note-manager). Contiene datos de clientes: no lo publiques ni lo "
            "subas a un repositorio público.\n", encoding="utf-8")
    if not (v / ".git").exists() and _git(v, "init", "-q") is not None:
        _git(v, "add", "-A")
        _git(v, "commit", "-q", "-m", "Crear el vault de reuniones")
    return v


def _git(cwd: Path, *args) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# ── Minutes HTML → Markdown ───────────────────────────────────────────────────

class _MinutesParser(HTMLParser):
    """Markdown from the minutes HTML, plus the structured parts (title, attendees,
    decisions, action items) for the frontmatter."""

    BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "dt", "dd", "footer", "td", "th", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.buf: list[str] = []
        self.stack: list[tuple[str, str]] = []   # (tag, class)
        self.skip = 0
        self.table: list[list[str]] | None = None
        self.table_cls = ""
        self.row: list[str] | None = None
        self.list_depth = 0
        self.title = ""
        self.attendees: list[dict] = []
        self.decisions: list[str] = []
        self.actions: list[dict] = []
        self.risks: list[str] = []
        self.open: list[str] = []

    # helpers
    def _flush(self) -> str:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        return text

    def _cls(self, attrs) -> str:
        return dict(attrs).get("class", "") or ""

    def handle_starttag(self, tag, attrs):
        if tag in ("head", "style", "script", "title"):
            self.skip += 1
            return
        if self.skip:
            return
        cls = self._cls(attrs)
        if tag in self.BLOCKS and tag not in ("td", "th", "tr") and self.buf and self.table is None:
            self._emit_para()
        self.stack.append((tag, cls))
        if tag == "table":
            self.table, self.table_cls = [], cls
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th"):
            self.buf = []
        elif tag in ("ul", "ol"):
            self.list_depth += 1
        elif tag in ("b", "strong"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("_")
        elif tag == "br":
            self.buf.append(" ")
        elif tag == "span" and "n" in cls.split():
            self.buf.append("")   # section number; gets a dot on close

    def handle_endtag(self, tag):
        if tag in ("head", "style", "script", "title"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        # pop to the matching tag (HTML from a model may not be perfectly nested)
        cls = ""
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                cls = self.stack[i][1]
                del self.stack[i:]
                break
        else:
            return
        classes = cls.split()
        if tag in ("b", "strong"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("_")
        elif tag == "span" and "n" in classes:
            self.buf.append(". ")
        elif tag in ("td", "th"):
            if self.row is not None:
                self.row.append(self._flush().replace("|", "\\|"))
        elif tag == "tr" and self.table is not None and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self._emit_table()
        elif tag in ("ul", "ol"):
            self.list_depth = max(0, self.list_depth - 1)
            self.out.append("")
        elif tag == "dl":
            self.out.append("")
        elif tag in self.BLOCKS:
            self._emit_block(tag, classes)

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def _emit_para(self):
        text = self._flush()
        if text:
            self.out.append(text)
            self.out.append("")

    def _emit_block(self, tag, classes):
        text = self._flush()
        if not text:
            return
        plain = TS_RE.sub(" ", text.replace("**", "")).strip()
        if tag == "h1":
            self.title = self.title or plain
            self.out += [f"# {text}", ""]
        elif tag in ("h2", "h3", "h4"):
            self.out += [f"{'#' * int(tag[1])} {text}", ""]
        elif tag == "li":
            self.out.append(f"{'  ' * max(0, self.list_depth - 1)}- {text}")
        elif tag == "dt":
            self.out.append(f"- **{text}:**")
        elif tag == "dd":
            if self.out and self.out[-1].endswith(":**"):
                self.out[-1] += f" {text}"
            else:
                self.out.append(f"  {text}")
        elif tag == "footer":
            self.out += ["---", "", f"_{text}_", ""]
        elif tag == "div" and "decision" in classes:
            self.decisions.append(plain)
            self.out += [f"- {text}", ""]
        elif tag == "div" and "risk" in classes:
            self.risks.append(plain)
            self.out += [f"- ⚠️ {text}", ""]
        elif tag == "div" and "open" in classes:
            self.open.append(plain)
            self.out += [f"- ❓ {text}", ""]
        elif tag == "div" and "kicker" in classes:
            self.out += [f"_{text}_", ""]
        else:
            self.out += [text, ""]

    def _emit_table(self):
        rows = [r for r in (self.table or []) if any(c.strip() for c in r)]
        cls = self.table_cls.split()
        self.table, self.row = None, None
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        head, body = rows[0], rows[1:]
        self.out.append("| " + " | ".join(head) + " |")
        self.out.append("|" + "---|" * width)
        for r in body:
            self.out.append("| " + " | ".join(r) + " |")
        self.out.append("")
        plain = [[TS_RE.sub(" ", c.replace("**", "").replace("\\|", "|")).strip() for c in r] for r in body]
        if "attendees" in cls:
            for r in plain:
                self.attendees.append({"name": r[0], "role": r[1] if len(r) > 1 else ""})
        elif "actions" in cls:
            for r in plain:
                r = r + [""] * (5 - len(r))
                self.actions.append({"id": r[0], "task": r[1], "owner": r[2], "due": r[3], "status": r[4]})


def minutes_to_markdown(html: str) -> tuple[str, dict]:
    p = _MinutesParser()
    p.feed(html)
    p.close()
    if p.buf:
        p._emit_para()
    md = re.sub(r"\n{3,}", "\n\n", "\n".join(p.out)).strip() + "\n"
    lang = re.search(r'<html[^>]*\blang="([a-zA-Z-]+)"', html)
    return md, {"title": p.title, "attendees": p.attendees, "decisions": p.decisions,
                "actions": p.actions, "risks": p.risks, "open": p.open,
                "lang": lang.group(1) if lang else ""}


# ── Archive ───────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return "-".join(s.split("-")[:7]) or "reunion"


def _sid_datetime(sid: str) -> datetime:
    """Start time from the session id (20261019_171338, also with a suffix such as .diarized)."""
    m = re.match(r"(\d{8})_(\d{6})", sid)
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else datetime.now()
    except ValueError:
        return datetime.now()


def _frontmatter(meta: dict) -> str:
    import yaml
    return "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000) + "---\n"


def read_frontmatter(path: Path) -> dict:
    import yaml
    try:
        m = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
        return (yaml.safe_load(m.group(1)) or {}) if m else {}
    except Exception:
        return {}


def archive_minutes(project: str, sid: str, rec: dict, html: str) -> Path | None:
    """Write (or rewrite) the session's meeting note from a finished minutes version, refresh
    the indexes and commit. Returns the note's path, or None when no vault is configured."""
    vault = ensure_vault()
    if vault is None:
        return None
    md, info = minutes_to_markdown(html)
    dt = _sid_datetime(sid)
    title = info["title"] or rec.get("title") or f"Reunión {dt:%Y-%m-%d %H:%M}"
    mdir = vault / _safe(project) / MEETINGS_DIR
    mdir.mkdir(parents=True, exist_ok=True)
    glossary_path(project)   # make sure the project has one to fill in

    meta = {
        "title": title,
        "date": dt.strftime("%Y-%m-%d"),
        "updated": datetime.now().strftime("%Y-%m-%d"),
        "type": "meeting",
        "status": "final",
        "tags": ["reunion", _slug(project)],
        "keywords": [a["name"] for a in info["attendees"] if a["name"]][:15],
        "project": project,
        "source": f"voice-rec · sesión {sid} · minuta m{rec.get('v')}",
        "meeting": {
            "session": sid,
            "time": f"{rec.get('t_first', '')}–{rec.get('t_last', '')}",
            "language": info["lang"] or rec.get("lang", ""),
            "attendees": info["attendees"],
            "decisions": info["decisions"],
            "actions": info["actions"],
            "risks": info["risks"],
            "open_questions": info["open"],
        },
    }
    # One note per session: replace an earlier note of the same session (its title may differ).
    for old in mdir.glob(f"{dt:%Y-%m-%d_%H%M}_*.md"):
        if (read_frontmatter(old).get("meeting") or {}).get("session") == sid:
            old.unlink()
    note = mdir / f"{dt:%Y-%m-%d_%H%M}_{_slug(title)}.md"
    note.write_text(_frontmatter(meta) + "\n" + md, encoding="utf-8")
    build_indexes(vault)
    if (vault / ".git").exists():
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", f"Minuta: {project} · {dt:%Y-%m-%d %H:%M} · {title}"[:200])
    return note


# ── Indexes ───────────────────────────────────────────────────────────────────

def _meetings(pdir: Path) -> list[tuple[Path, dict]]:
    items = []
    for f in sorted((pdir / MEETINGS_DIR).glob("*.md")):
        fm = read_frontmatter(f)
        if fm.get("type") == "meeting":
            items.append((f, fm))
    items.sort(key=lambda x: x[0].name, reverse=True)
    return items


def _row(f: Path, fm: dict, base: Path) -> str:
    m = fm.get("meeting") or {}
    link = f.relative_to(base).as_posix()
    title = str(fm.get("title", f.stem)).replace("|", "\\|")
    open_actions = sum(1 for a in m.get("actions") or [] if not re.search(
        r"complet|hech|cerrad|done|closed", str(a.get("status", "")), re.I))
    return (f"| {fm.get('date', '')} {str(m.get('time', '')).split('–')[0][:5]} | [{title}]({link}) "
            f"| {len(m.get('decisions') or [])} | {open_actions} |")


def _n(count: int) -> str:
    return f"{count} reunión" if count == 1 else f"{count} reuniones"


def build_indexes(vault: Path | None = None):
    vault = vault or vault_dir()
    if vault is None or not vault.exists():
        return
    head = "| Fecha | Reunión | Decisiones | Acuerdos abiertos |\n|---|---|---|---|"
    projects = []
    for pdir in sorted(p for p in vault.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        items = _meetings(pdir)
        if not items:
            continue
        projects.append((pdir, items))
        rows = "\n".join(_row(f, fm, pdir) for f, fm in items)
        glossary = f"\nGlosario: [{GLOSSARY}]({GLOSSARY})\n" if (pdir / GLOSSARY).exists() else ""
        (pdir / "_index.md").write_text(
            f"# {pdir.name} · reuniones\n\n{_n(len(items))} · actualizado {datetime.now():%Y-%m-%d %H:%M}\n"
            f"{glossary}\n{head}\n{rows}\n", encoding="utf-8")
    latest = sorted(((f, fm) for _, items in projects for f, fm in items), key=lambda x: x[0].name,
                    reverse=True)[:30]
    summary = "\n".join(f"- [{p.name}]({p.name}/_index.md) · {_n(len(items))}" for p, items in projects)
    rows = "\n".join(_row(f, fm, vault) for f, fm in latest)
    (vault / "_index.md").write_text(
        f"# Meetings vault\n\nActualizado {datetime.now():%Y-%m-%d %H:%M}\n\n## Proyectos\n\n{summary or '(ninguno)'}\n\n"
        f"## Últimas reuniones\n\n{head}\n{rows}\n", encoding="utf-8")
