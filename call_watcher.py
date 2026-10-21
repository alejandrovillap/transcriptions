#!/usr/bin/env python3
"""Call watcher (Windows): notices when a call app takes the microphone, asks whether to
record, records through the web UI server, stops when the call ends and can then build the
final beautified version and the minutes.

Detection uses the registry that drives the taskbar microphone icon:
  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\ConsentStore\\microphone
Every app that used the mic has a key with LastUsedTimeStart / LastUsedTimeStop; Stop is 0
while the app is using it. Only apps in the allow-list count (Teams, Zoom, Webex, browsers…),
so the recorder's own Python, dictation tools etc. never trigger it.

Runs in the background with an icon next to the clock (pythonw call_watcher.py, or
callwatch.bat). Settings: .webui/call_watcher.json (also from the icon's menu).

  pythonw call_watcher.py          # normal use
  python call_watcher.py --status  # print the apps using the microphone right now and exit
"""

import argparse
import ctypes
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import winreg
from pathlib import Path

import sessions as S

ROOT = S.ROOT
CONFIG_PATH = S.WEBUI_DIR / "call_watcher.json"
SERVER_INFO = S.WEBUI_DIR / "server.json"
LOG_PATH = S.WEBUI_DIR / "call_watcher.log"
MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
STARTUP_LNK = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup\voice-rec call watcher.lnk"
STATE_PATH = S.WEBUI_DIR / "call_watcher_state.json"
WATCHDOG_TASK = "voice-rec call watcher"     # Task Scheduler: relaunch every 5 min if it was closed
CREATE_NO_WINDOW = 0x08000000

DEFAULT_CONFIG = {
    "mode": "ask",              # ask | auto | off — what to do when a call starts
    "after_call": "ask",        # ask | final_minutes | final | nothing — when the call ends
    "project": "",              # empty = the project last used in the UI (else "llamadas")
    "start_after_sec": 3,       # the mic must stay taken this long before it counts as a call
    "stop_after_sec": 15,       # …and released this long before the call counts as ended
    "prompt_timeout_sec": 45,   # unanswered "Record?" prompt = don't record
    # registry key name (lower-case) contains → label shown to the user
    "apps": {
        "msteams": "Teams", "ms-teams": "Teams", "teams.exe": "Teams",
        "zoom": "Zoom", "webex": "Webex", "ciscocollabhost": "Webex", "slack": "Slack",
        "discord": "Discord", "skype": "Skype", "whatsapp": "WhatsApp", "telegram": "Telegram",
        "signal": "Signal", "ringcentral": "RingCentral", "gotomeeting": "GoTo Meeting",
        "chrome.exe": "Chrome (Meet / web call)", "msedge.exe": "Edge (Meet / web call)",
        "firefox.exe": "Firefox (web call)", "brave.exe": "Brave (web call)", "opera": "Opera (web call)",
    },
}

log = logging.getLogger("call_watcher")


# ── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = S._read_json(CONFIG_PATH, DEFAULT_CONFIG)
    cfg["apps"] = {**DEFAULT_CONFIG["apps"], **(cfg.get("apps") or {})}
    return cfg


def save_config(cfg: dict):
    S._write_json(CONFIG_PATH, cfg)


# ── Microphone detection ─────────────────────────────────────────────────────

def _mic_entries():
    """(key name, in_use) for every app Windows has recorded using the microphone."""
    def walk(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        return
                    i += 1
                    yield name, path + "\\" + name
        except OSError:
            return

    for name, full in walk(MIC_KEY):
        if name == "NonPackaged":
            for sub, subfull in walk(full):
                yield sub, _in_use(subfull)
        else:
            yield name, _in_use(full)


def _in_use(path: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
            start, _ = winreg.QueryValueEx(k, "LastUsedTimeStart")
            return stop == 0 and start != 0
    except OSError:
        return False


def call_apps_using_mic(apps: dict, test_file: Path | None = None) -> list[str]:
    """Labels of allow-listed apps holding the microphone right now."""
    if test_file is not None:   # tests: the file's lines are the labels "in a call"
        try:
            return [l.strip() for l in test_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return []
    found = []
    for name, in_use in _mic_entries():
        if not in_use:
            continue
        low = name.lower()
        label = next((lbl for pat, lbl in apps.items() if pat in low), None)
        if label and label not in found:
            found.append(label)
    return found


# ── Server (web UI) ──────────────────────────────────────────────────────────

class Server:
    def __init__(self):
        self.url = ""

    def _req(self, method: str, path: str, body=None, timeout=15):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, method=method, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body=None):
        return self._req("POST", path, body if body is not None else {})

    def alive(self) -> bool:
        info = S._read_json(SERVER_INFO, {})
        if not info.get("url"):
            return False
        self.url = info["url"]
        try:
            self.get("/api/status")
            return True
        except Exception:
            return False

    def ensure(self, wait=90) -> bool:
        """Start server.py in the background (no window) if it isn't running."""
        if self.alive():
            return True
        python = ROOT / ".venv" / "Scripts" / "python.exe"
        python = python if python.exists() else Path(sys.executable.replace("pythonw.exe", "python.exe"))
        log.info("starting the web UI server (%s)", python)
        out = open(S.WEBUI_DIR / "server.log", "a", encoding="utf-8")
        subprocess.Popen([str(python), "-u", str(ROOT / "server.py"), "--no-browser"], cwd=str(ROOT),
                         stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=CREATE_NO_WINDOW,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(1)
            if self.alive():
                return True
        log.error("the server did not come up; see %s", S.WEBUI_DIR / "server.log")
        return False

    def recorder(self) -> dict:
        return self.get("/api/status")["recorder"]


def sess_path(project: str, sid: str) -> str:
    q = urllib.request.quote
    return f"/api/projects/{q(project, safe='')}/sessions/{q(sid, safe='')}"


# ── Popups (tkinter, bottom-right corner) ────────────────────────────────────

def _work_area():
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    r = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)   # SPI_GETWORKAREA
    return r.l, r.t, r.r, r.b


class Popups:
    BG, FG, MUTED, ACCENT, LINE = "#161a22", "#e6e9f0", "#a3abbc", "#4f6bed", "#2a3140"

    def __init__(self, root):
        self.root = root
        self.current = None

    def show(self, title: str, text: str, buttons: list[tuple[str, object]], timeout: int = 0,
             on_timeout=None, primary: int = 0):
        """buttons: [(label, callback)]; the first one is the primary (accent) button."""
        import tkinter as tk
        self.close()
        w = tk.Toplevel(self.root)
        self.current = w
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=self.LINE)
        inner = tk.Frame(w, bg=self.BG, padx=18, pady=14)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(inner, text="●  voice-rec", bg=self.BG, fg="#ef5a5a", font=("Segoe UI", 9, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(inner, text=title, bg=self.BG, fg=self.FG, font=("Segoe UI", 12, "bold"),
                 anchor="w", justify="left", wraplength=340).pack(fill="x", pady=(4, 2))
        tk.Label(inner, text=text, bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10),
                 anchor="w", justify="left", wraplength=340).pack(fill="x")
        row = tk.Frame(inner, bg=self.BG)
        row.pack(fill="x", pady=(12, 0))
        countdown = tk.Label(row, text="", bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9))
        countdown.pack(side="left")
        for i, (label, cb) in reversed(list(enumerate(buttons))):
            is_primary = i == primary
            b = tk.Button(row, text=label, relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
                          font=("Segoe UI", 10, "bold" if is_primary else "normal"),
                          bg=self.ACCENT if is_primary else "#262c38", fg="white",
                          activebackground="#6a82f0" if is_primary else "#323a4a", activeforeground="white",
                          command=lambda cb=cb: (self.close(), cb and cb()))
            b.pack(side="right", padx=(8, 0))
        w.update_idletasks()
        l, t, r, b = _work_area()
        ww, wh = max(380, w.winfo_reqwidth()), w.winfo_reqheight()
        w.geometry(f"{ww}x{wh}+{r - ww - 16}+{b - wh - 16}")
        if timeout:
            end = time.time() + timeout

            def tick():
                if self.current is not w:
                    return
                left = int(end - time.time())
                if left <= 0:
                    self.close()
                    if on_timeout:
                        on_timeout()
                    return
                countdown.configure(text=f"{left}s")
                w.after(500, tick)
            tick()

    def close(self):
        if self.current is not None:
            try:
                self.current.destroy()
            except Exception:
                pass
            self.current = None


# ── Watcher ──────────────────────────────────────────────────────────────────

class Watcher:
    def __init__(self, root, test_file: Path | None = None):
        self.root = root
        self.popups = Popups(root)
        self.server = Server()
        self.test_file = test_file
        self.ui: queue.Queue = queue.Queue()     # callables to run on the Tk thread
        self.cfg = load_config()
        self.in_call = False
        self.call_label = ""
        self.seen_since = None      # mic taken since (debounce start)
        self.free_since = None      # mic released since (debounce end)
        self.call_started = 0.0
        # (project, sid) of the recording we started; kept on disk so a restarted watcher
        # (crash, Windows closing it) can still stop it when the call ends
        state = S._read_json(STATE_PATH, {})
        self._ours = tuple(state["ours"]) if state.get("ours") else None
        self.state_text = "Esperando llamadas"
        self.tray = None
        # The registry is polled on its own thread, so the Tk thread (which owns windows
        # Windows talks to) never waits on it.
        self.latest_apps: list[str] = []
        threading.Thread(target=self._poll_mic, daemon=True, name="mic-poll").start()

    @property
    def ours(self):
        return self._ours

    @ours.setter
    def ours(self, value):
        self._ours = tuple(value) if value else None
        try:
            S._write_json(STATE_PATH, {"ours": list(self._ours) if self._ours else None})
        except Exception:
            log.exception("could not save the watcher state")

    def _poll_mic(self):
        while True:
            try:
                self.latest_apps = call_apps_using_mic(load_config()["apps"], self.test_file)
            except Exception:
                log.exception("microphone check failed")
            time.sleep(1)

    # -- helpers --
    def on_ui(self, fn):
        self.ui.put(fn)

    def bg(self, fn, *a):
        threading.Thread(target=self._safe, args=(fn, *a), daemon=True).start()

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as e:
            log.exception("background task failed")
            self.on_ui(lambda: self.popups.show("Algo falló", str(e)[:300], [("OK", None)], timeout=20))

    def set_state(self, text: str, recording: bool | None = None):
        self.state_text = text
        if self.tray:
            self.tray.update(text, recording)

    # -- main loop (Tk thread) --
    def tick(self):
        try:
            while True:
                try:
                    fn = self.ui.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:   # a failing popup must never stop the watcher
                    log.exception("UI action failed")
            self.cfg = load_config()
            self.step(list(self.latest_apps))
        except Exception:
            log.exception("tick failed")
        finally:
            self.root.after(1000, self.tick)
        if time.time() - getattr(self, "_last_alive", 0) > 3600:
            self._last_alive = time.time()
            log.info("alive (%s)", self.state_text)

    def step(self, apps: list[str]):
        now = time.time()
        if not self.in_call:
            if apps:
                self.seen_since = self.seen_since or now
                if now - self.seen_since >= self.cfg["start_after_sec"]:
                    self.in_call, self.call_label, self.call_started = True, apps[0], now
                    self.free_since = None
                    log.info("call started: %s", self.call_label)
                    self.call_start()
            else:
                self.seen_since = None
        else:
            if apps:
                self.free_since = None
            else:
                self.free_since = self.free_since or now
                if now - self.free_since >= self.cfg["stop_after_sec"]:
                    self.in_call, self.seen_since = False, None
                    log.info("call ended: %s (%.0f min)", self.call_label, (now - self.call_started) / 60)
                    self.call_end()

    # -- call start --
    def call_start(self):
        mode = self.cfg["mode"]
        self.set_state(f"Llamada en {self.call_label}")
        if mode == "off":
            return
        if self.ours:
            # Saved at start-up: we may have been restarted during a call we were recording.
            self.bg(self._adopt_or_ask)
            return
        if mode == "auto":
            self.bg(self.start_recording, True)
            return
        self.ask_to_record()

    def _adopt_or_ask(self):
        """Resume our saved recording if it is still running; otherwise forget it and
        handle the call normally (ask, or record if the mode is automatic)."""
        rec = self.server.recorder() if self.server.alive() else {}
        if rec.get("active") and (rec.get("project"), rec.get("sid")) == self.ours:
            log.info("resuming our recording %s / %s after a restart", *self.ours)
            self.set_state(f"Grabando {self.call_label} → {self.ours[0]}", recording=True)
            return
        self.ours = None
        if self.cfg["mode"] == "auto":
            self.start_recording(True)
        else:
            self.on_ui(self.ask_to_record)

    def ask_to_record(self):
        self.popups.show(
            f"Llamada detectada en {self.call_label}",
            "¿Grabar y transcribir esta llamada? Avisa a los demás participantes de que se está grabando.",
            [("Grabar", lambda: self.bg(self.start_recording, False)), ("Ahora no", self.declined)],
            timeout=self.cfg["prompt_timeout_sec"], on_timeout=self.declined)

    def declined(self):
        log.info("not recording this call")
        self.set_state(f"Llamada en {self.call_label} (sin grabar)")

    def start_recording(self, automatic: bool):
        if not self.server.ensure():
            raise RuntimeError("No se pudo iniciar el servidor de voice-rec (ver .webui/server.log)")
        rec = self.server.recorder()
        if rec.get("active"):
            log.info("a recording is already running; leaving it alone")
            self.ours = None
            self.on_ui(lambda: self.popups.show("Ya se está grabando",
                                                f"Ya había una grabación en curso ({rec.get('project')}); "
                                                "sigue tal cual.", [("OK", None)], timeout=8))
            return
        settings = self.server.get("/api/settings")
        project = self.cfg["project"].strip() or settings.get("last_project") or "llamadas"
        self.server.post("/api/projects", {"name": project})
        body = {"project": project, **{k: settings.get(k) for k in
                ("lang", "speakers", "minutes", "pause", "save_audio", "engine", "auto_frames",
                 "system_audio")}}
        self.server.post("/api/record/start", body)
        sid = ""
        for _ in range(120):            # the session id arrives once main.py is up
            rec = self.server.recorder()
            sid = rec.get("sid") or ""
            if sid or rec.get("state") == "error":
                break
            time.sleep(1)
        if not sid:
            raise RuntimeError(f"La grabación no arrancó: {rec.get('error') or rec.get('detail')}")
        self.ours = (project, sid)
        log.info("recording %s / %s", project, sid)
        self.set_state(f"Grabando {self.call_label} → {project}", recording=True)
        self.on_ui(lambda: self.popups.show(
            f"Grabando {self.call_label}", f"Proyecto “{project}”. Se detiene sola cuando termine la llamada.",
            [("Abrir", self.open_ui), ("Detener", lambda: self.bg(self.stop_recording))],
            timeout=8 if automatic else 6))

    def stop_recording(self):
        if not self.ours or not self.server.alive():
            return
        rec = self.server.recorder()
        if rec.get("active") and (rec.get("project"), rec.get("sid")) == self.ours:
            self.server.post("/api/record/stop")
            log.info("stopped %s / %s", *self.ours)
        self.set_state(f"Llamada en {self.call_label} (detenida)" if self.in_call else "Esperando llamadas",
                       recording=False)

    # -- call end --
    def call_end(self):
        self.popups.close()
        ours, self.ours = self.ours, None
        self.set_state("Esperando llamadas", recording=False)
        if not ours:
            return
        minutes = max(1, round((time.time() - self.call_started) / 60))
        self.bg(self._finish_call, ours, minutes)

    def _finish_call(self, ours, minutes):
        project, sid = ours
        self.stop_after_mine(ours)
        what = self.cfg["after_call"]
        if what == "nothing":
            return
        if what == "ask":
            self.on_ui(lambda: self.popups.show(
                f"Terminó la llamada de {self.call_label} ({minutes} min)",
                "¿Generar ahora la versión final anotada y la minuta? Las escribe Claude y tardan unos minutos.",
                [("Final + minuta", lambda: self.bg(self.post_process, project, sid, True)),
                 ("Solo final", lambda: self.bg(self.post_process, project, sid, False)),
                 ("Nada", None)],
                timeout=120))
        else:
            self.post_process(project, sid, what == "final_minutes")

    def stop_after_mine(self, ours):
        """Stop our recording and wait until main.py has finalized it."""
        if not self.server.alive():
            return
        rec = self.server.recorder()
        if rec.get("active") and (rec.get("project"), rec.get("sid")) == ours:
            self.server.post("/api/record/stop")
            log.info("call over: stopped %s / %s", *ours)
        for _ in range(900):
            if not self.server.recorder().get("active"):
                break
            time.sleep(1)

    def post_process(self, project: str, sid: str, with_minutes: bool):
        base = sess_path(project, sid)
        last_beautify = lambda: (self.server.get(base)["manifest"]["versions"] or [{}])[-1]
        last_minutes = lambda: (self.server.get(base + "/minutes")["versions"] or [{}])[-1]
        self.set_state("Generando la versión final…", recording=False)
        self.server.post(base + "/beautify", {"mode": "final"})
        ok = self._wait(lambda: last_beautify().get("status"))
        if not ok:
            self._failed("Falló la versión final", last_beautify().get("error"))
            return
        if with_minutes:
            self.set_state("Escribiendo la minuta…")
            self.server.post(base + "/minutes")
            ok = self._wait(lambda: last_minutes().get("status"))
            if not ok:
                self._failed("Falló la minuta (la versión final sí está lista)", last_minutes().get("error"))
                return
        self.set_state("Esperando llamadas")
        title = "Minuta lista" if with_minutes else "Versión final lista"
        self.on_ui(lambda: self.popups.show(title, f"Proyecto “{project}” · sesión {sid}.",
                                            [("Abrir", self.open_ui), ("Cerrar", None)], timeout=120))

    def _failed(self, title: str, error: str | None):
        self.set_state("Esperando llamadas")
        reason = (error or "").strip()
        if "credit balance" in reason.lower():
            reason = "Tu cuenta de Claude no tiene crédito suficiente. Recárgala y vuelve a generar desde voice-rec."
        log.warning("%s: %s", title, error)
        self.on_ui(lambda: self.popups.show(title, (reason or "Abre voice-rec para ver el motivo.")[:300],
                                            [("Abrir", self.open_ui), ("Cerrar", None)], timeout=120))

    def _wait(self, status_fn, limit=3600) -> bool:
        t0 = time.time()
        time.sleep(3)
        while time.time() - t0 < limit:
            st = status_fn()
            if st in ("done", "failed", "cancelled"):
                return st == "done"
            time.sleep(4)
        return False

    def open_ui(self):
        def go():
            if self.server.ensure():
                webbrowser.open(self.server.url)
        self.bg(go)


# ── Tray icon ────────────────────────────────────────────────────────────────

class Tray:
    def __init__(self, watcher: Watcher, on_quit):
        import pystray
        self.pystray = pystray
        self.w = watcher
        self.recording = False
        self.icon = pystray.Icon("voice-rec", self._image(False), "voice-rec · esperando llamadas",
                                 menu=self._menu())
        self.on_quit = on_quit

    def _image(self, recording: bool):
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((2, 2, 62, 62), fill=(220, 60, 60, 255) if recording else (79, 107, 237, 255))
        d.rounded_rectangle((25, 13, 39, 37), radius=7, fill="white")          # microphone
        d.arc((18, 22, 46, 46), 0, 180, fill="white", width=4)
        d.line((32, 46, 32, 52), fill="white", width=4)
        return im

    def _menu(self):
        p = self.pystray
        cfg = lambda: load_config()

        def setter(key, value):
            def f(icon, item):
                c = load_config()
                c[key] = value
                save_config(c)
                log.info("setting %s = %s", key, value)
            return f

        def checked(key, value):
            return lambda item: cfg().get(key) == value

        return p.Menu(
            p.MenuItem(lambda item: self.w.state_text, None, enabled=False),
            p.Menu.SEPARATOR,
            p.MenuItem("Abrir voice-rec", lambda icon, item: self.w.open_ui(), default=True),
            p.MenuItem("Al empezar una llamada", p.Menu(
                p.MenuItem("Preguntar antes de grabar", setter("mode", "ask"), checked=checked("mode", "ask"), radio=True),
                p.MenuItem("Grabar automáticamente", setter("mode", "auto"), checked=checked("mode", "auto"), radio=True),
                p.MenuItem("No hacer nada", setter("mode", "off"), checked=checked("mode", "off"), radio=True))),
            p.MenuItem("Al terminar la llamada", p.Menu(
                p.MenuItem("Preguntar", setter("after_call", "ask"), checked=checked("after_call", "ask"), radio=True),
                p.MenuItem("Versión final + minuta", setter("after_call", "final_minutes"),
                           checked=checked("after_call", "final_minutes"), radio=True),
                p.MenuItem("Solo versión final", setter("after_call", "final"),
                           checked=checked("after_call", "final"), radio=True),
                p.MenuItem("Nada", setter("after_call", "nothing"),
                           checked=checked("after_call", "nothing"), radio=True))),
            p.MenuItem("Detener grabación", lambda icon, item: self.w.bg(self.w.stop_recording),
                       enabled=lambda item: self.recording),
            p.MenuItem("Iniciar con Windows", lambda icon, item: toggle_autostart(),
                       checked=lambda item: STARTUP_LNK.exists()),
            p.MenuItem("Reabrir si se cierra (cada 5 min)", lambda icon, item: self.w.bg(toggle_watchdog),
                       checked=lambda item: watchdog_installed()),
            p.Menu.SEPARATOR,
            p.MenuItem("Salir", lambda icon, item: self.on_quit()),
        )

    def update(self, text: str, recording: bool | None):
        if recording is not None and recording != self.recording:
            self.recording = recording
            self.icon.icon = self._image(recording)
        self.icon.title = f"voice-rec · {text}"[:127]
        self.icon.update_menu()

    def run(self):
        threading.Thread(target=self.icon.run, daemon=True).start()

    def stop(self):
        self.icon.stop()


def toggle_autostart() -> bool:
    """Create or remove a shortcut in the Startup folder (runs pythonw call_watcher.py at logon)."""
    if STARTUP_LNK.exists():
        STARTUP_LNK.unlink()
        log.info("autostart off")
        return False
    pythonw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    pythonw = pythonw if pythonw.exists() else Path(sys.executable.replace("python.exe", "pythonw.exe"))
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
          "$s.TargetPath = $env:TARGET; $s.Arguments = $env:ARGS; $s.WorkingDirectory = $env:WD; "
          "$s.Description = 'voice-rec call watcher'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True,
                   creationflags=CREATE_NO_WINDOW,
                   env={**os.environ, "LNK": str(STARTUP_LNK), "TARGET": str(pythonw),
                        "ARGS": f'"{ROOT / "call_watcher.py"}"', "WD": str(ROOT)})
    log.info("autostart on: %s", STARTUP_LNK)
    return True


def _pythonw() -> Path:
    p = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    return p if p.exists() else Path(sys.executable.replace("python.exe", "pythonw.exe"))


def watchdog_installed() -> bool:
    r = subprocess.run(["schtasks", "/Query", "/TN", WATCHDOG_TASK], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


def toggle_watchdog() -> bool:
    """A Task Scheduler task that starts the watcher at logon and every 5 minutes. If the
    watcher is running, the new copy exits at once (single instance); if Windows closed it
    (e.g. after a hang), it is back within 5 minutes."""
    if watchdog_installed():
        subprocess.run(["schtasks", "/Delete", "/TN", WATCHDOG_TASK, "/F"], check=True,
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        log.info("watchdog off")
        return False
    ps = ("$a = New-ScheduledTaskAction -Execute $env:TARGET -Argument $env:ARGS -WorkingDirectory $env:WD; "
          "$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; "
          "$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
          "-RepetitionInterval (New-TimeSpan -Minutes 5); "
          "$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0) "
          "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable; "
          "Register-ScheduledTask -TaskName $env:TASK -Action $a -Trigger $t1,$t2 -Settings $s "
          "-Description 'Reopens the voice-rec call watcher if it was closed' | Out-Null")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True,
                   capture_output=True, creationflags=CREATE_NO_WINDOW,
                   env={**os.environ, "TASK": WATCHDOG_TASK, "TARGET": str(_pythonw()),
                        "ARGS": f'"{ROOT / "call_watcher.py"}"', "WD": str(ROOT)})
    log.info("watchdog on: task '%s'", WATCHDOG_TASK)
    return True


def install_crash_logging(root=None):
    """Write any uncaught error (main thread, other threads, Tk callbacks) to the log;
    pythonw has no console, so otherwise they vanish."""
    sys.excepthook = lambda t, v, tb: log.critical("uncaught error", exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.critical("uncaught error in thread %s", a.thread and a.thread.name,
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    if root is not None:
        root.report_callback_exception = lambda t, v, tb: log.error("Tk callback error", exc_info=(t, v, tb))


# ── Entry point ──────────────────────────────────────────────────────────────

def single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\voice-rec-call-watcher")
    return ctypes.windll.kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS


def main():
    ap = argparse.ArgumentParser(description="Record calls automatically (Windows)")
    ap.add_argument("--status", action="store_true", help="Print the call apps using the microphone and exit")
    ap.add_argument("--autostart", choices=["on", "off"], help="Start (or not) with Windows, then exit")
    ap.add_argument("--watchdog", choices=["on", "off"],
                    help="Reopen the watcher every 5 min if it was closed (Task Scheduler), then exit")
    ap.add_argument("--test-mic-file", type=Path, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--no-tray", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.status:
        sys.stdout.reconfigure(encoding="utf-8")
        cfg = load_config()
        for name, in_use in _mic_entries():
            print(f"{'IN USE' if in_use else '      '}  {name}")
        print("Call apps using the mic now:", call_apps_using_mic(cfg["apps"]) or "none")
        return
    if args.autostart:
        if STARTUP_LNK.exists() != (args.autostart == "on"):
            toggle_autostart()
        print(f"Start with Windows: {'on' if STARTUP_LNK.exists() else 'off'}")
        return
    if args.watchdog:
        if watchdog_installed() != (args.watchdog == "on"):
            toggle_watchdog()
        print(f"Reopen if closed: {'on' if watchdog_installed() else 'off'}")
        return

    S.WEBUI_DIR.mkdir(exist_ok=True)
    if not single_instance():
        return   # already running (the watchdog task tries every 5 min): exit quietly
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    install_crash_logging()
    if not CONFIG_PATH.exists():
        save_config(load_config())

    try:   # crisp popups on scaled displays
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    install_crash_logging(root)
    watcher = Watcher(root, args.test_mic_file)

    def quit_all():
        log.info("quit")
        if watcher.tray:
            watcher.tray.stop()
        watcher.on_ui(root.destroy)

    if not args.no_tray:
        watcher.tray = Tray(watcher, quit_all)
        watcher.tray.run()
    log.info("watching for calls (mode=%s, after_call=%s)", watcher.cfg["mode"], watcher.cfg["after_call"])
    root.after(500, watcher.tick)
    root.mainloop()


if __name__ == "__main__":
    main()
