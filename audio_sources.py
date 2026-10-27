"""Microphone + system audio ("what the others say" in a call) for main.py on Windows.

With a headset, the other participants' voices go straight to the earphones and the
microphone never hears them. MixedInput records the microphone and the WASAPI loopback of
the output devices (what you hear) and mixes them into the same 16 kHz mono 100 ms blocks
that main.py's sounddevice stream would deliver, calling the same callback.

Built on PyAudioWPatch (PortAudio with WASAPI loopback). Each device is captured at its own
rate and channel count, mixed down to mono and resampled to 16 kHz with a stateful
anti-alias filter. A loopback device delivers nothing while nothing plays; the mixer then
uses silence.

Devices:
  mic     ""  → the Windows default microphone, else a name or part of it
  output  ""  → ALL output devices (the call is captured wherever it plays: headset,
          speakers, monitor), "default" → the default output, else a name or part of it

  python audio_sources.py            # list devices
"""

from __future__ import annotations

import collections
import sys
import threading
import time
from math import gcd

import numpy as np

TARGET_SR = 16000


def _pa():
    import pyaudiowpatch as pa
    return pa


def _wasapi_devices(p, pa):
    wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
    devs = [p.get_device_info_by_index(i) for i in range(p.get_device_count())]
    devs = [d for d in devs if d["hostApi"] == wasapi["index"]]
    mics = [d for d in devs if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")]
    loops = [d for d in devs if d.get("isLoopbackDevice")]
    return wasapi, mics, loops


def _clean(name: str) -> str:
    return name.replace(" [Loopback]", "")


def _pick(devs: list, name: str):
    low = name.lower()
    return (next((d for d in devs if _clean(d["name"]).lower() == low), None)
            or next((d for d in devs if low in d["name"].lower()), None))


def list_devices() -> dict:
    pa = _pa()
    p = pa.PyAudio()
    try:
        wasapi, mics, loops = _wasapi_devices(p, pa)
        out = {"mics": [d["name"] for d in mics], "outputs": [_clean(d["name"]) for d in loops]}
        if wasapi["defaultInputDevice"] >= 0:
            out["default_mic"] = p.get_device_info_by_index(wasapi["defaultInputDevice"])["name"]
        if wasapi["defaultOutputDevice"] >= 0:
            out["default_output"] = p.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
        return out
    finally:
        p.terminate()


class _Resampler:
    """Interleaved float32 at (rate, channels) → mono float32 at 16 kHz, keeping filter state
    across blocks so block edges don't click."""

    def __init__(self, rate: int, channels: int):
        from scipy import signal
        self.signal, self.channels = signal, channels
        g = gcd(int(rate), TARGET_SR)
        self.up, self.down = TARGET_SR // g, int(rate) // g
        self.decimate_only = self.up == 1
        if self.decimate_only and self.down > 1:      # 48 kHz → 16 kHz etc.: FIR + take every Nth
            self.taps = signal.firwin(96, 0.9 / self.down)
            self.zi = np.zeros(len(self.taps) - 1)
            self.phase = 0

    def __call__(self, raw: bytes) -> np.ndarray:
        x = np.frombuffer(raw, np.float32)
        if self.channels > 1:
            x = x.reshape(-1, self.channels).mean(axis=1)
        if self.up == 1 and self.down == 1:
            return x.astype(np.float32)
        if self.decimate_only:
            y, self.zi = self.signal.lfilter(self.taps, 1.0, x, zi=self.zi)
            out = y[self.phase::self.down]
            self.phase = (self.phase - len(x)) % self.down
            return out.astype(np.float32)
        return self.signal.resample_poly(x, self.up, self.down).astype(np.float32)   # e.g. 44.1 kHz


class MixedInput:
    """Drop-in for sd.InputStream(callback=...): start() / stop() / close().

    A watchdog keeps it alive: if the microphone stops delivering audio for STALL_SEC
    (Windows reset the device, e.g. a webcam mic when Teams toggles the camera, a headset
    plugged in or out), the devices are looked up again and reopened, retrying every
    RETRY_SEC. Meanwhile silence is fed in real time, so the recorder's timeline (and the
    timestamps) stay aligned with the clock. on_event(kind, message) reports it."""

    MAX_BUFFER_SEC = 2.0
    STALL_SEC = 3.0        # no microphone audio for this long = the stream died
    RETRY_SEC = 3.0        # between reopen attempts

    def __init__(self, callback, samplerate: int = TARGET_SR, blocksize: int = 1600, mic: str = "",
                 system: bool = True, output: str = "", mic_gain: float = 1.0, system_gain: float = 1.0,
                 on_event=None):
        if samplerate != TARGET_SR:
            raise ValueError("MixedInput produces 16 kHz audio")
        self.callback, self.block = callback, blocksize
        self.mic_gain, self.system_gain = mic_gain, system_gain
        self.want_mic, self.want_system, self.want_output = mic, system, output
        self.on_event = on_event or (lambda kind, message: None)
        self.pa = _pa()
        self.p = None
        self._streams = []
        self._lock = threading.Lock()          # system-audio buffers
        self._feed_lock = threading.Lock()     # one producer of 100 ms blocks at a time
        self._reopen_lock = threading.Lock()
        self.errors: list[str] = []
        self._running = threading.Event()
        self._last_mic = 0.0
        self.restarts = 0
        self._resolve()

    # -- devices --
    def _resolve(self):
        """(Re)create PyAudio, so devices plugged in or reset since are seen, and pick them."""
        if self.p is not None:
            try:
                self.p.terminate()
            except Exception:
                pass
        self.p = self.pa.PyAudio()
        wasapi, mics, loops = _wasapi_devices(self.p, self.pa)
        self.mic = None
        if self.want_mic:
            self.mic = _pick(mics, self.want_mic)
            if self.mic is None:
                print(f"[audio] microphone '{self.want_mic}' not found, using the default one", file=sys.stderr)
        if self.mic is None:
            self.mic = self.p.get_device_info_by_index(wasapi["defaultInputDevice"])
        if not self.want_system:
            self.loops = []
        elif not self.want_output:
            self.loops = loops
        elif self.want_output == "default":
            default_name = self.p.get_device_info_by_index(wasapi["defaultOutputDevice"])["name"]
            self.loops = [d for d in loops if _clean(d["name"]) == default_name][:1]
        else:
            d = _pick(loops, self.want_output)
            self.loops = [d] if d else loops
        self._mic_pending = np.zeros(0, np.float32)
        with self._lock:
            self._bufs = [collections.deque() for _ in self.loops]

    def describe(self) -> str:
        outs = ", ".join(_clean(d["name"]) for d in self.loops)
        return f"mic: {self.mic['name']}" + (f" + system audio: {outs}" if outs else "")

    def _open(self, dev: dict, on_audio):
        rate, ch = int(dev["defaultSampleRate"]), max(1, min(2, int(dev["maxInputChannels"])))
        rs = _Resampler(rate, ch)
        pa = self.pa

        def cb(data, frames, time_info, status):
            try:
                on_audio(rs(data))
            except Exception as e:   # never raise inside PortAudio's thread
                self.errors.append(str(e))
            return (None, pa.paContinue)

        s = self.p.open(format=pa.paFloat32, channels=ch, rate=rate, input=True,
                        input_device_index=dev["index"], frames_per_buffer=rate // 10, stream_callback=cb)
        self._streams.append(s)
        return s

    def _open_all(self):
        for i, dev in enumerate(self.loops):
            try:
                self._open(dev, lambda x, i=i: self._push_loop(i, x)).start_stream()
            except Exception as e:     # one output failing must not stop the recording
                self.errors.append(f"system audio ({_clean(dev['name'])}): {e}")
                print(f"[audio] cannot record {_clean(dev['name'])}: {e}", file=sys.stderr)
        self._open(self.mic, self._push_mic).start_stream()

    def _close_streams(self):
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
        self._streams = []

    def start(self):
        self._running.set()
        self._last_mic = time.monotonic()
        self._open_all()
        threading.Thread(target=self._watchdog, daemon=True, name="audio-watchdog").start()

    def stop(self):
        self._running.clear()
        self._close_streams()

    def close(self):
        self.stop()
        try:
            self.p.terminate()
        except Exception:
            pass

    # -- watchdog: reopen a dead stream, keep the timeline going meanwhile --
    def _watchdog(self):
        next_try = 0.0
        stalled_since = None
        fed_until = 0.0
        while self._running.is_set():
            time.sleep(0.1)
            now = time.monotonic()
            if now - self._last_mic < self.STALL_SEC:
                if stalled_since is not None:
                    gap = now - stalled_since
                    msg = f"Audio restored after {gap:.0f} s (device reconnected)"
                    print(f"[audio] {msg}", file=sys.stderr)
                    self.on_event("restored", msg)
                    stalled_since = None
                continue
            if stalled_since is None:
                stalled_since = self._last_mic
                fed_until = self._last_mic
                msg = (f"No audio from '{self.mic['name']}' for {self.STALL_SEC:.0f} s: "
                       "reconnecting the devices")
                print(f"[audio] {msg}", file=sys.stderr)
                self.on_event("stalled", msg)
            # silence for the time without audio, so timestamps stay right
            while fed_until + self.block / TARGET_SR <= now:
                self._feed(np.zeros(self.block, np.float32))
                fed_until += self.block / TARGET_SR
            if now >= next_try:
                next_try = now + self.RETRY_SEC
                self._reopen()

    def _reopen(self):
        if not self._reopen_lock.acquire(blocking=False):
            return
        try:
            self._close_streams()
            self._resolve()
            self._open_all()
            self.restarts += 1
            print(f"[audio] reopened: {self.describe()}", file=sys.stderr)
        except Exception as e:
            self.errors.append(f"reopen: {e}")
            print(f"[audio] reopen failed, retrying: {e}", file=sys.stderr)
        finally:
            self._reopen_lock.release()

    # -- system audio: bounded buffers the microphone mixes from --
    def _push_loop(self, i: int, x: np.ndarray):
        with self._lock:
            if i >= len(self._bufs):
                return
            buf = self._bufs[i]
            buf.append(x)
            while sum(len(b) for b in buf) > self.MAX_BUFFER_SEC * TARGET_SR:
                buf.popleft()

    def _take(self, n: int) -> np.ndarray:
        """Sum of the next n samples of every output (silence where one has none)."""
        out = np.zeros(n, np.float32)
        with self._lock:
            for buf in self._bufs:
                got = 0
                while got < n and buf:
                    chunk = buf[0]
                    k = min(n - got, len(chunk))
                    out[got:got + k] += chunk[:k]
                    got += k
                    if k == len(chunk):
                        buf.popleft()
                    else:
                        buf[0] = chunk[k:]
        return out

    def _feed(self, d: np.ndarray):
        with self._feed_lock:
            if self.loops:
                d = d + self._take(len(d)) * self.system_gain
            np.clip(d, -1.0, 1.0, out=d)
            self.callback(d[:, None], len(d), None, None)

    # -- microphone: the clock; every 100 ms block gets the system audio mixed in --
    def _push_mic(self, x: np.ndarray):
        self._last_mic = time.monotonic()
        pending = np.concatenate([self._mic_pending, x * self.mic_gain])
        while len(pending) >= self.block:
            d, pending = pending[:self.block].copy(), pending[self.block:]
            self._feed(d)
        self._mic_pending = pending


if __name__ == "__main__":
    import json
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(list_devices(), ensure_ascii=False, indent=2))
