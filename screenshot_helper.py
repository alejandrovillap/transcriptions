#!/usr/bin/env python3
"""screenshot_helper — Windows companion for main.py / server.py.

Drop-in replacement for the macOS screenshot_helper.swift: same modes, same
stdout protocol, pure Python (ctypes + Pillow + tkinter).

Modes:
  screenshot_helper.py <output-dir> [capture-config.json]
      Daemon: global hotkeys (Ctrl+Shift+S screenshot, Ctrl+Shift+W selection).
      Also accepts "SNAP" lines on stdin. The capture config is re-read on every
      screenshot, so the capture area can be changed while recording.
      "AUTO <seconds>" starts automatic frames: every <seconds> a JPEG of the capture
      area goes to <output-dir>/.frames/ and "FRAME:<path>" is printed (no flash).
      "AUTO OFF" stops them. main.py decides which frames are worth keeping.
  screenshot_helper.py --select-region     Drag-select overlay -> prints REGION:{json}
  screenshot_helper.py --pick-window       Click-a-window overlay -> prints WINDOW:{json}
  screenshot_helper.py --list-windows      Prints visible windows as a JSON array
  screenshot_helper.py --capture <config.json> <out.png>   One-shot capture (preview)
  screenshot_helper.py --capture-series <config.json> <out-dir> <count> <interval>
      f00.png, f01.png… every <interval> s in one process (used by "Detect video")
  screenshot_helper.py --flash x y w h     Internal: brief white frame after a capture

Capture config (JSON):
  {"mode": "full"}
  {"mode": "region", "rect": {"x":..,"y":..,"w":..,"h":..}}   virtual-screen pixels, top-left origin
  {"mode": "window", "windowId": 123, "owner": "Teams.exe", "title": "..."}
  Optional "frames_crop": {"x":..,"y":..,"w":..,"h":..} in fractions of the captured image
  (set by "Detect video"): automatic frames keep only that part. Screenshots stay whole.
"""

import base64
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

if sys.platform != "win32":
    sys.exit("screenshot_helper.py is for Windows; on macOS use the Swift screenshot_helper")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

# Physical pixels everywhere: overlay, window rects and captures must agree.
try:
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        user32.SetProcessDPIAware()

from PIL import Image, ImageGrab  # noqa: E402  (after DPI awareness)

# ── Win32 declarations ───────────────────────────────────────────────────────

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
PW_RENDERFULLCONTENT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0002, 0x0004, 0x4000
WM_HOTKEY = 0x0312
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
CF_UNICODETEXT = 13
INPUT_KEYBOARD, KEYEVENTF_KEYUP = 1, 0x0002
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):  # only so the INPUT union has the right size
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


def log(msg: str):
    print(f"[screenshot-helper] {msg}", file=sys.stderr, flush=True)


_out_lock = threading.Lock()


def emit(line: str):
    """One protocol line on stdout; several threads write (screenshots, frames, selections)."""
    with _out_lock:
        print(line, flush=True)


# ── Capture config ───────────────────────────────────────────────────────────

def load_config(path: str | None) -> dict:
    cfg = {"mode": "full"}
    if path:
        try:
            cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


# ── Windows ──────────────────────────────────────────────────────────────────

def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _owner(hwnd) -> str:
    """Executable name of the window's process, e.g. 'chrome.exe'."""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, _pid(hwnd))
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return ""
    finally:
        kernel32.CloseHandle(h)


def _cloaked(hwnd) -> bool:
    v = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return bool(v.value)


def window_rect(hwnd) -> tuple[int, int, int, int]:
    """Visible bounds (left, top, right, bottom) without the invisible resize border."""
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def visible_windows() -> list[int]:
    """Top-level, visible, normal windows of other processes, front to back."""
    me = os.getpid()
    out: list[int] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(hwnd):
            return True
        if user32.GetWindow(hwnd, GW_OWNER) or user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if _pid(hwnd) == me or not _title(hwnd):
            return True
        l, t, r, b = window_rect(hwnd)
        if r - l >= 40 and b - t >= 40:
            out.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def window_json(hwnd) -> dict:
    l, t, r, b = window_rect(hwnd)
    return {"windowId": int(hwnd), "owner": _owner(hwnd), "title": _title(hwnd),
            "rect": {"x": l, "y": t, "w": r - l, "h": b - t}}


def resolve_window(cfg: dict) -> int | None:
    """Find the configured window: by id first, then by owner + title, then by owner."""
    wid = int(cfg.get("windowId") or 0)
    if wid and user32.IsWindow(wid):
        return wid
    owner, title = cfg.get("owner", ""), cfg.get("title", "")
    candidates = [h for h in visible_windows() if _owner(h) == owner]
    match = next((h for h in candidates if _title(h) == title), None)
    return match or (candidates[0] if candidates else None)


def print_window(hwnd) -> Image.Image | None:
    """Render a window with PrintWindow, so it works even when other windows cover it."""
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    if w <= 1 or h <= 1:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
            return None
        bih = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bih), 0):
            return None
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
    # Crop the invisible resize border that GetWindowRect includes.
    l, t, r, b = window_rect(hwnd)
    return img.crop((l - wr.left, t - wr.top, r - wr.left, b - wr.top))


def capture_image(cfg: dict) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """Image plus the captured rect (x, y, w, h) in virtual-screen pixels (for the flash)."""
    mode = cfg.get("mode", "full")
    if mode == "region":
        r = cfg.get("rect") or {}
        x, y, w, h = (int(r.get(k, 0)) for k in ("x", "y", "w", "h"))
        if w >= 4 and h >= 4:
            return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True), (x, y, w, h)
    elif mode == "window":
        hwnd = resolve_window(cfg)
        if hwnd and not user32.IsIconic(hwnd):
            img = print_window(hwnd)
            if img is not None and img.width > 1:
                l, t, r, b = window_rect(hwnd)
                return img, (l, t, r - l, b - t)
        log(f"window '{cfg.get('owner', '')}' not found or minimized, capturing full screen")
    img = ImageGrab.grab()  # primary monitor
    return img, (0, 0, img.width, img.height)


def flash(rect: tuple[int, int, int, int]):
    """Non-blocking visual feedback, in its own process (tkinter wants the main thread)."""
    if rect[2] <= 0:
        return
    try:
        subprocess.Popen([sys.executable, __file__, "--flash", *map(str, rect)],
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass


# ── Daemon actions ───────────────────────────────────────────────────────────

_capture_lock = threading.Lock()


def take_screenshot(out_dir: str, config_path: str | None):
    with _capture_lock:
        name = f"screenshot_{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3] + ".png"
        path = str(Path(out_dir) / name)
        try:
            result = capture_image(load_config(config_path))
        except Exception as e:
            log(f"capture failed: {e}")
            return
        if not result:
            log("capture failed")
            return
        img, rect = result
        try:
            img.save(path, "PNG")
        except Exception as e:
            log(f"failed to write PNG: {e}")
            return
        emit(f"SCREENSHOT:{path}")
        flash(rect)


# ── Automatic frames ("AUTO <seconds>" on stdin) ────────────────────────────

def crop_fraction(img: Image.Image, crop: dict | None) -> Image.Image:
    """Keep the frames_crop part (fractions of the image), if it is big enough."""
    if not crop:
        return img
    try:
        fx, fy, fw, fh = (float(crop.get(k, 0)) for k in ("x", "y", "w", "h"))
    except (TypeError, ValueError):
        return img
    if fw <= 0.02 or fh <= 0.02:
        return img
    w, h = img.size
    box = (max(0, round(fx * w)), max(0, round(fy * h)), min(w, round((fx + fw) * w)), min(h, round((fy + fh) * h)))
    if box[2] - box[0] >= 16 and box[3] - box[1] >= 16:
        return img.crop(box)
    return img


def take_frame(out_dir: str, config_path: str | None):
    frames = Path(out_dir) / ".frames"
    frames.mkdir(parents=True, exist_ok=True)
    path = str(frames / (f"frame_{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3] + ".jpg"))
    cfg = load_config(config_path)
    try:
        result = capture_image(cfg)
    except Exception as e:
        log(f"frame capture failed: {e}")
        return
    if not result:
        log("frame capture failed")
        return
    try:
        crop_fraction(result[0], cfg.get("frames_crop")).convert("RGB").save(path, "JPEG", quality=85)
    except Exception as e:
        log(f"failed to write JPEG: {e}")
        return
    emit(f"FRAME:{path}")


class AutoFrames:
    """Timer thread that takes a frame every *interval* seconds. A tick is skipped
    while the previous capture is still running, like the macOS helper."""

    def __init__(self, out_dir: str, config_path: str | None):
        self.out_dir, self.config_path = out_dir, config_path
        self._stop: threading.Event | None = None
        self._lock = threading.Lock()

    def set(self, interval: float | None):
        with self._lock:
            if self._stop:
                self._stop.set()
                self._stop = None
            if not interval or interval <= 0:
                log("auto frames off")
                return
            stop = self._stop = threading.Event()
            threading.Thread(target=self._run, args=(interval, stop), daemon=True).start()
            log(f"auto frames every {interval}s")

    def _run(self, interval: float, stop: threading.Event):
        nxt = time.monotonic()
        while not stop.is_set():
            take_frame(self.out_dir, self.config_path)
            nxt += interval
            delay = nxt - time.monotonic()
            if delay < 0:            # capture took longer than the interval: skip missed ticks
                nxt = time.monotonic()
                delay = 0
            stop.wait(delay)


def _send_keys(*events: tuple[int, bool]):
    arr = (INPUT * len(events))()
    for i, (vk, up) in enumerate(events):
        arr[i].type = INPUT_KEYBOARD
        arr[i].ki = KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP if up else 0)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _read_clipboard_text() -> str | None:
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def get_selected_text():
    # The hotkey fires while Ctrl+Shift are still held; wait for release so the
    # simulated Ctrl+C isn't seen as Ctrl+Shift+C.
    deadline = time.time() + 1.5
    while time.time() < deadline and any(user32.GetAsyncKeyState(k) & 0x8000
                                         for k in (VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN, ord("W"))):
        time.sleep(0.02)
    seq = user32.GetClipboardSequenceNumber()
    _send_keys((VK_CONTROL, False), (ord("C"), False), (ord("C"), True), (VK_CONTROL, True))
    deadline = time.time() + 0.6
    while time.time() < deadline and user32.GetClipboardSequenceNumber() == seq:
        time.sleep(0.02)
    text = _read_clipboard_text() if user32.GetClipboardSequenceNumber() != seq else None
    if text:
        emit("SELECTION:" + base64.b64encode(text.encode("utf-8")).decode("ascii"))
    else:
        log("no selection found")


def run_daemon(out_dir: str, config_path: str | None):
    hotkeys = {1: ord("S"), 2: ord("W")}
    for hk_id, vk in hotkeys.items():
        if not user32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, vk):
            log(f"could not register Ctrl+Shift+{chr(vk)} (already used by another app?)")

    auto = AutoFrames(out_dir, config_path)

    def stdin_reader():
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "SNAP":
                threading.Thread(target=take_screenshot, args=(out_dir, config_path), daemon=True).start()
            elif cmd.upper().startswith("AUTO"):
                arg = cmd[4:].strip()
                if arg.upper() == "OFF":
                    auto.set(None)
                else:
                    try:
                        auto.set(float(arg) if arg else 1.0)
                    except ValueError:
                        auto.set(1.0)
        auto.set(None)   # stdin closed: main.py is gone

    threading.Thread(target=stdin_reader, daemon=True).start()
    log("Helper ready (Ctrl+Shift+S: screenshot, Ctrl+Shift+W: selection)")

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            if msg.wParam == 1:
                log("hotkey 1 (screenshot) fired")
                threading.Thread(target=take_screenshot, args=(out_dir, config_path), daemon=True).start()
            elif msg.wParam == 2:
                log("hotkey 2 (selection) fired")
                threading.Thread(target=get_selected_text, daemon=True).start()


# ── Overlays (tkinter) ───────────────────────────────────────────────────────

ACCENT = "#7d9cff"


def virtual_screen() -> tuple[int, int, int, int]:
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def run_flash(x: int, y: int, w: int, h: int):
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="white")
    root.geometry(f"{w}x{h}{x:+d}{y:+d}")
    alpha = [0.35]
    root.attributes("-alpha", alpha[0])

    def fade():
        alpha[0] -= 0.05
        if alpha[0] <= 0:
            root.destroy()
            return
        root.attributes("-alpha", alpha[0])
        root.after(30, fade)

    root.after(60, fade)
    root.mainloop()


def run_overlay(mode: str) -> int:
    """mode = 'region' (drag) or 'window' (click). Prints REGION:/WINDOW:/CANCEL."""
    import tkinter as tk
    candidates = [(h, window_json(h)) for h in visible_windows()] if mode == "window" else []
    vx, vy, vw, vh = virtual_screen()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.45)
    root.configure(bg="black")
    root.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
    cv = tk.Canvas(root, bg="black", highlightthickness=0,
                   cursor="crosshair" if mode == "region" else "hand2")
    cv.pack(fill="both", expand=True)

    hint = ("Drag to select the capture area  ·  Esc to cancel" if mode == "region"
            else "Click a window to capture it  ·  Esc to cancel")
    # Centre the hint on the primary monitor (origin 0,0 in virtual coords).
    pw = user32.GetSystemMetrics(0)
    cv.create_text(pw // 2 - vx, 60 - vy, text=hint, fill="white", font=("Segoe UI", 15, "bold"))

    state = {"start": None, "rect": None, "hovered": None, "result": None}

    def draw(rect, label):
        cv.delete("hl")
        if not rect:
            return
        x, y, w, h = rect
        cv.create_rectangle(x - vx, y - vy, x - vx + w, y - vy + h, outline=ACCENT, width=3, tags="hl")
        ly = y - vy - 14 if y - vy > 40 else y - vy + h + 14
        cv.create_text(x - vx + 4, ly, text=label, anchor="w", fill="white",
                       font=("Consolas", 11, "bold"), tags="hl")

    def finish(prefix, obj):
        state["result"] = f"{prefix}:{json.dumps(obj, ensure_ascii=False)}"
        root.destroy()

    def cancel(_=None):
        state["result"] = "CANCEL"
        root.destroy()

    def gpos(e):
        return e.x_root, e.y_root

    def on_move(e):
        if mode != "window":
            return
        px, py = gpos(e)
        hit = next((j for _, j in candidates
                    if j["rect"]["x"] <= px < j["rect"]["x"] + j["rect"]["w"]
                    and j["rect"]["y"] <= py < j["rect"]["y"] + j["rect"]["h"]), None)
        state["hovered"] = hit
        if hit:
            r = hit["rect"]
            label = f"{hit['owner']} — {hit['title']}" if hit["title"] else hit["owner"]
            draw((r["x"], r["y"], r["w"], r["h"]), label[:90])
        else:
            draw(None, "")

    def on_down(e):
        if mode == "region":
            state["start"] = gpos(e)
        elif state["hovered"]:
            finish("WINDOW", state["hovered"])

    def on_drag(e):
        if mode != "region" or not state["start"]:
            return
        (sx, sy), (px, py) = state["start"], gpos(e)
        rect = (min(sx, px), min(sy, py), abs(px - sx), abs(py - sy))
        state["rect"] = rect
        draw(rect, f"{rect[2]} × {rect[3]}")

    def on_up(_):
        if mode != "region":
            return
        r = state["rect"]
        if r and r[2] >= 12 and r[3] >= 12:
            finish("REGION", {"rect": {"x": r[0], "y": r[1], "w": r[2], "h": r[3]}})
        else:
            state["start"] = state["rect"] = None
            draw(None, "")

    cv.bind("<Motion>", on_move)
    cv.bind("<ButtonPress-1>", on_down)
    cv.bind("<B1-Motion>", on_drag)
    cv.bind("<ButtonRelease-1>", on_up)
    cv.bind("<ButtonPress-3>", cancel)
    root.bind("<Escape>", cancel)
    root.after(90_000, cancel)  # safety net: never leave the screen blocked
    root.after(50, lambda: (root.focus_force(), cv.focus_set()))
    root.mainloop()

    print(state["result"] or "CANCEL", flush=True)
    return 0 if state["result"] and state["result"] != "CANCEL" else 1


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("Usage: screenshot_helper.py <output-dir> [capture-config.json]\n"
              "       screenshot_helper.py --select-region | --pick-window | --list-windows\n"
              "       screenshot_helper.py --capture <config.json> <out.png>", file=sys.stderr)
        return 1
    if args[0] == "--list-windows":
        print(json.dumps([window_json(h) for h in visible_windows()], ensure_ascii=False))
        return 0
    if args[0] == "--capture":
        if len(args) < 3:
            print("Usage: --capture <config.json> <out.png>", file=sys.stderr)
            return 1
        result = capture_image(load_config(args[1]))
        if not result:
            log("capture failed")
            return 1
        result[0].save(args[2], "PNG")
        print(f"OK:{args[2]}")
        return 0
    if args[0] == "--capture-series":
        if len(args) < 5:
            print("Usage: --capture-series <config.json> <out-dir> <count> <interval>", file=sys.stderr)
            return 1
        out = Path(args[2])
        out.mkdir(parents=True, exist_ok=True)
        count, interval = int(args[3]), float(args[4])
        ok = 0
        for i in range(count):
            t = time.monotonic()
            result = capture_image(load_config(args[1]))
            if result:
                result[0].save(out / f"f{i:02d}.png", "PNG", compress_level=1)
                ok += 1
            time.sleep(max(0.0, interval - (time.monotonic() - t)))
        print(f"OK:{ok}")
        return 0 if ok else 1
    if args[0] in ("--select-region", "--pick-window"):
        return run_overlay("region" if args[0] == "--select-region" else "window")
    if args[0] == "--flash":
        run_flash(*map(int, args[1:5]))
        return 0
    run_daemon(args[0], args[1] if len(args) > 1 else None)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
