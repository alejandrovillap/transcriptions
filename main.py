#!/usr/bin/env python3
"""Real-time English/Spanish speech-to-text using a local ASR model (Whisper or Parakeet)."""

import argparse
import base64
import os
import signal
import subprocess
import sys
import threading
import queue
import re
from datetime import datetime, timedelta
from pathlib import Path

# Windows without Developer Mode: Hugging Face copies instead of symlinking; that's fine, don't warn.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np
import sounddevice as sd
import torch
import easyocr

from asr_engines import ENGINES, create_engine, engine_label
from events import events

try:
    from speaker_id import (
        SpeakerEmbedder, WordToken, AsrSegment, EmbeddingWindow,
        cluster_and_label, diarize_words,
    )
    _DIARIZATION_AVAILABLE = True
except ImportError:
    _DIARIZATION_AVAILABLE = False

# ── Config ───────────────────────────────────────────────────────────────────
MODEL_SIZE = "large-v3-turbo"
LANGUAGE = "auto"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
SAMPLE_RATE = 16000
VAD_THRESHOLD = 0.5
SILENCE_DURATION_MS = 1500
MIN_SPEECH_DURATION_MS = 250
# Continuous audio (a podcast, a video, people talking over each other) may never pause
# for SILENCE_DURATION_MS. Then a phrase is cut anyway: at the first short pause after
# SOFT_CHUNK_SEC, or at the least-speech moment of the last CUT_SEARCH_SEC once it
# reaches MAX_CHUNK_SEC. Keeps live text flowing and the ASR input short.
SOFT_CHUNK_SEC = 15.0
MAX_CHUNK_SEC = 25.0
CUT_SEARCH_SEC = 4.0
SCREENSHOT_HELPER = Path(__file__).parent / "screenshot_helper.py"
SUPPORTED_LANGS = ("en", "es")
OCR_LANGS = ["es", "en"]
BEAM_SIZE = 5

# ── Diarization config ──────────────────────────────────────────────────────
PHRASE_GAP_SEC = 0.4       # gap between words to force a phrase split
EMBED_CHUNK_SEC = 3.0      # max phrase duration before forced split (at word boundary)
MIN_PHRASE_AUDIO_SEC = 1.0 # min audio duration for reliable speaker embedding

# ── Globals ──────────────────────────────────────────────────────────────────
audio_queue: queue.Queue[np.ndarray] = queue.Queue()
transcribe_queue: queue.Queue = queue.Queue()      # items: (t_start, audio)
embed_queue: queue.Queue = queue.Queue()            # items: (t_start, t_end, audio)
stop_event = threading.Event()
screenshot_dir: Path | None = None
ocr_queue: queue.Queue[str] = queue.Queue()
pending_injections: queue.Queue[str] = queue.Queue()
audio_chunks: list[np.ndarray] = []
save_audio_flag = False
mic_muted = threading.Event()   # set via --control-stdin MUTE/UNMUTE
live_frames = None   # autoframes.LiveFrames once auto frames are switched on (--auto-frames / AUTO ON)


def load_models(engine_name="whisper", asr_model=None):
    """Load the ASR engine and Silero VAD."""
    if engine_name == "whisper":
        size = asr_model or MODEL_SIZE
        print(f"Loading Whisper model '{size}' (device={DEVICE}, compute={COMPUTE_TYPE})...")
        events.emit("status", state="loading", detail=f"Loading Whisper {size}")
    else:
        label = engine_label(engine_name, asr_model)
        print(f"Loading {label} (device=cpu)... (first run downloads the model)")
        events.emit("status", state="loading", detail=f"Loading {label}")
    asr_engine = create_engine(engine_name, device=DEVICE, model_size=MODEL_SIZE,
                               compute_type=COMPUTE_TYPE, beam_size=BEAM_SIZE, asr_model=asr_model)
    print("Whisper model loaded." if engine_name == "whisper" else f"{asr_engine.label} loaded.")

    print("Loading Silero VAD...")
    events.emit("status", state="loading", detail="Loading Silero VAD")
    vad_model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
    )
    print("Silero VAD loaded.")
    return asr_engine, vad_model


def audio_callback(indata, frames, time_info, status):
    """sounddevice callback — push mono float32 frames into audio_queue."""
    if status:
        print(f"[audio] {status}", file=sys.stderr)
    data = indata[:, 0].copy()
    if mic_muted.is_set():
        # Feed silence (not nothing) so timestamps stay aligned with the wall clock
        # and a phrase in progress ends normally.
        data = np.zeros_like(data)
    audio_queue.put(data)
    if events.enabled:
        events.emit_throttled("level", 0.08, "level", rms=float(np.sqrt(np.mean(data * data))))
    if save_audio_flag:
        audio_chunks.append(data)


def flush_pending_injections(output_file, session_data):
    """Write pending injection markers (screenshots, selections) to transcript."""
    while True:
        try:
            marker = pending_injections.get_nowait()
        except queue.Empty:
            break
        t_rel = (datetime.now() - session_data["start_time"]).total_seconds()
        wall_time = datetime.now().strftime("%H:%M:%S")
        sys.stdout.write(f"\r\033[K[{wall_time}] {marker}\n")
        sys.stdout.flush()
        output_file.write(f"[{wall_time}] {marker}\n")
        output_file.flush()
        session_data["injections"].append((t_rel, marker))
        if marker.startswith("[screenshot: "):
            events.emit("injection", kind="screenshot", time=wall_time,
                        path=marker[len("[screenshot: "):-1])
        elif marker.startswith("[selection: "):
            events.emit("injection", kind="selection", time=wall_time,
                        text=marker[len("[selection: "):-1])


def pick_language(info, fallback):
    """Choose 'en' or 'es' for a segment from Whisper's language probabilities.

    Whisper may guess another language (pt, it, gl...) on short phrases; only
    English and Spanish are allowed. Returns (lang, confident)."""
    probs = dict(getattr(info, "all_language_probs", None) or [])
    if not probs and info.language in SUPPORTED_LANGS:
        probs = {info.language: info.language_probability}
    en, es = probs.get("en", 0.0), probs.get("es", 0.0)
    if en + es <= 0:
        return fallback, False
    lang = "es" if es >= en else "en"
    return lang, max(en, es) / (en + es) >= LANG_CONFIDENT


# ── Vocabulary hints (--vocab-file) ────────────────────────────────────────
VOCAB_RE = re.compile(r'^\s*[-*]\s*"(.+?)"\s*(?:→|->)\s*"(.+?)"')
TERM_RE = re.compile(r'^\s*[-*]\s+(?!")(.+?)\s*$')    # glossary: "- Term" or "- Term — what it is"
MAX_HOTWORDS_CHARS = 400
vocab_path: Path | None = None       # project notes: '"heard" → "meant"' corrections
glossary_path: Path | None = None    # meetings vault glossary: terms and corrections (--glossary-file)
_vocab_cache = {"key": None, "hotwords": None, "rules": []}


def _load_vocab():
    """Read the notes and the glossary again whenever one of them changes. Correct spellings
    (glossary terms first, then the "meant" side of corrections) become Whisper hotwords;
    corrections become replace rules for engines without hotwords (--vocab-correct)."""
    files = [p for p in (vocab_path, glossary_path) if p is not None]
    key = []
    for p in files:
        try:
            key.append((str(p), p.stat().st_mtime))
        except OSError:
            key.append((str(p), None))
    key = tuple(key)
    if key == _vocab_cache["key"]:
        return
    terms, meant_terms, rules = [], [], []
    for p, (_, mtime) in zip(files, key):
        if mtime is None:
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = VOCAB_RE.match(line)
            if m:
                heard, meant = m.group(1).strip(), m.group(2).strip()
                if meant and meant not in meant_terms:
                    meant_terms.append(meant)
                if heard and heard != meant:
                    rules.append((re.compile(r"(?<!\w)" + re.escape(heard) + r"(?!\w)", re.IGNORECASE), meant))
            elif p == glossary_path and (t := TERM_RE.match(line)) and not t.group(1).startswith("<!--"):
                term = re.split(r"\s+[—–-]\s+|:\s", t.group(1), maxsplit=1)[0].strip()
                if term and term not in terms:
                    terms.append(term)
    all_terms = terms + [t for t in meant_terms if t not in terms]
    hw = ", ".join(all_terms)
    if len(hw) > MAX_HOTWORDS_CHARS:   # cut at a term boundary, never in the middle of a term
        hw = hw[:MAX_HOTWORDS_CHARS].rsplit(",", 1)[0]
    changed = _vocab_cache["key"] is not None or all_terms or rules
    _vocab_cache.update(key=key, hotwords=hw or None, rules=rules)
    if changed:
        sys.stdout.write(f"\r\033[K[vocab] {len(all_terms)} term(s), {len(rules)} correction(s) loaded\n")
        sys.stdout.flush()


def current_hotwords() -> str | None:
    """Correct spellings from the notes and the glossary, passed to Whisper as hotwords."""
    _load_vocab()
    return _vocab_cache["hotwords"]


# ── Vocabulary corrections (--vocab-correct) ─────────────────────────────────
# For engines without hotwords: replace '"heard" → "meant"' phrases in the output.
vocab_correct = False


def current_corrections() -> list:
    """[(compiled regex, replacement)] from the notes and the glossary, re-read on change."""
    _load_vocab()
    return _vocab_cache["rules"]


def apply_corrections(text: str) -> str:
    for rx, meant in current_corrections():
        text = rx.sub(meant, text)
    return text


# ── Language detection config ──────────────────────────────────────────────
# In auto mode every phrase is detected separately, so switching between English
# and Spanish works. Short, ambiguous phrases fall back to the previous language.
LANG_CONFIDENT = 0.75       # en/es probability ratio needed to trust a phrase's own detection


# ── Language guard for engines that choose the language themselves ─────────────
# Parakeet has no language option and, on noisy or echoey audio, may answer in English
# for Spanish speech. With a fixed --lang, each phrase's output language is checked with
# function words, and phrases in the wrong language are redone with Whisper.
_FUNCTION_WORDS = {
    "en": set("the and of to is that it was for with you this are have not be they what "
              "but on at we there his from or an by so would if when which".split()),
    "es": set("el la los las de que y en un una es por con para no se lo como pero más "
              "del al su sus le ya muy está son fue hay también porque cuando esto".split()),
}
LANG_GUARD_MIN_HITS = 4   # function words needed before trusting the guess


def guess_lang(text):
    """'en' / 'es' when the text clearly reads as one of them, else None."""
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    hits = {k: sum(w in v for w in words) for k, v in _FUNCTION_WORDS.items()}
    best = max(hits, key=hits.get)
    other = min(hits, key=hits.get)
    if hits[best] >= LANG_GUARD_MIN_HITS and hits[best] >= 2 * max(hits[other], 1):
        return best
    return None


class LangFallback:
    """Whisper loaded in the background, only when a fixed language is used with an engine
    that ignores it; get() waits for it the first time it is needed."""

    def __init__(self):
        self._engine = None
        self._ready = threading.Event()
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True

        def load():
            try:
                print(f"[lang] Loading Whisper {MODEL_SIZE} for wrong-language phrases (background)")
                self._engine = create_engine("whisper", device=DEVICE, model_size=MODEL_SIZE,
                                             compute_type=COMPUTE_TYPE, beam_size=BEAM_SIZE)
            except Exception as e:
                print(f"[lang] Whisper fallback unavailable: {e}", file=sys.stderr)
            finally:
                self._ready.set()

        threading.Thread(target=load, daemon=True).start()

    def get(self):
        if not self._started:
            return None
        self._ready.wait()
        return self._engine


lang_fallback = LangFallback()


def transcription_worker(asr_engine, output_file, session_data, enable_diarization, lang_mode):
    """Daemon thread: transcribe speech segments and stream results to console."""
    # Language state: in auto mode, current_lang is the last confidently detected language
    auto = lang_mode == "auto"
    current_lang = "es" if auto else lang_mode
    last_emitted = None

    while True:
        try:
            item = transcribe_queue.get(timeout=0.5)
        except queue.Empty:
            flush_pending_injections(output_file, session_data)
            continue

        if item is None:  # sentinel → exit
            transcribe_queue.task_done()
            break

        seg_t_start, segment_audio = item
        duration = len(segment_audio) / SAMPLE_RATE
        sys.stdout.write(f"\r\033[K[transcribing {duration:.1f}s...] ")
        sys.stdout.flush()
        events.emit("transcribing", t=round(seg_t_start, 2), duration=round(duration, 1),
                    queued=transcribe_queue.qsize())

        try:
            def run(language):
                return asr_engine.transcribe(
                    segment_audio,
                    language=language,
                    word_timestamps=enable_diarization,
                    hotwords=current_hotwords() if asr_engine.supports_hotwords else None,
                )

            correct = vocab_correct and (vocab_path is not None or glossary_path is not None)
            if auto and asr_engine.name == "whisper":
                segments = run(None)
                info = asr_engine.last_info
                seg_lang, confident = pick_language(info, current_lang)
                if not confident:
                    seg_lang = current_lang
                if seg_lang != info.language:
                    # Whisper picked another language (or an ambiguous one): redo in en/es.
                    segments = run(seg_lang)
                elif confident:
                    current_lang = seg_lang
                if seg_lang != last_emitted:
                    last_emitted = seg_lang
                    sys.stdout.write(f"\r\033[K[lang] {seg_lang}\n")
                    sys.stdout.flush()
                    events.emit("lang", lang=seg_lang)
            elif not auto and asr_engine.languages is None and asr_engine.name != "whisper":
                # Fixed language with an engine that picks it by itself (Parakeet): keep its
                # output unless it came out in the other language, then redo with Whisper.
                segments = list(run(None))
                got = guess_lang(" ".join(seg.text for seg in segments))
                if got and got != current_lang:
                    fallback = lang_fallback.get()
                    if fallback is not None:
                        print(f"\r\033[K[lang] {asr_engine.name} answered in {got}; "
                              f"redoing {duration:.1f}s with {fallback.label} ({current_lang})")
                        events.emit("lang_fallback", got=got, want=current_lang,
                                    duration=round(duration, 1))
                        segments = fallback.transcribe(
                            segment_audio, language=current_lang,
                            word_timestamps=enable_diarization, hotwords=current_hotwords())
            else:
                # Fixed language, or an engine that detects it by itself (Parakeet).
                segments = run(None if auto else current_lang)

            # Wall-clock timestamp derived from sample-accurate time
            wall_time = session_data["start_time"] + timedelta(seconds=seg_t_start)
            timestamp = wall_time.strftime("%H:%M:%S")

            first = True
            has_text = False
            asr_words = []
            seg_texts = []

            for seg in segments:
                text = seg.text.strip()
                if correct and text:
                    text = apply_corrections(text)
                if text:
                    if first:
                        sys.stdout.write(f"\r\033[K[{timestamp}] ")
                        output_file.write(f"[{timestamp}] ")
                        first = False
                    sys.stdout.write(text + " ")
                    sys.stdout.flush()
                    output_file.write(text + " ")
                    has_text = True
                    seg_texts.append(text)
                if events.enabled:
                    events.emit("transcribe_progress", t=round(seg_t_start, 2),
                                done=round(min(seg.end, duration), 1), duration=round(duration, 1),
                                text=text)

                # Collect word-level data for diarization
                if enable_diarization and seg.words:
                    for w in seg.words:
                        word_text = w.word.strip()
                        if correct and word_text:
                            word_text = apply_corrections(word_text)
                        if word_text:
                            asr_words.append(WordToken(
                                text=word_text,
                                t_start=seg_t_start + w.start,
                                t_end=seg_t_start + w.end,
                            ))

            if has_text:
                sys.stdout.write("\n")
                sys.stdout.flush()
                output_file.write("\n")
                output_file.flush()
                events.emit("segment", time=timestamp, text=" ".join(seg_texts),
                            t=round(seg_t_start, 2), duration=round(duration, 1))

                if enable_diarization and asr_words:
                    session_data["segments"].append(AsrSegment(
                        t_start=seg_t_start,
                        t_end=seg_t_start + duration,
                        words=asr_words,
                    ))

                    # Group words into phrases: split on gaps OR max duration
                    phrases = [[asr_words[0]]]
                    for w in asr_words[1:]:
                        prev = phrases[-1][-1]
                        phrase_dur = w.t_end - phrases[-1][0].t_start
                        gap = w.t_start - prev.t_end
                        if gap > PHRASE_GAP_SEC or phrase_dur > EMBED_CHUNK_SEC:
                            phrases.append([w])
                        else:
                            phrases[-1].append(w)

                    for phrase in phrases:
                        abs_start = phrase[0].t_start
                        abs_end = phrase[-1].t_end
                        rel_start = abs_start - seg_t_start
                        rel_end = abs_end - seg_t_start
                        # Pad short phrases for better embedding quality
                        phrase_dur = rel_end - rel_start
                        if phrase_dur < MIN_PHRASE_AUDIO_SEC:
                            pad = (MIN_PHRASE_AUDIO_SEC - phrase_dur) / 2
                            rel_start = max(0, rel_start - pad)
                            rel_end = min(duration, rel_end + pad)
                        s = int(rel_start * SAMPLE_RATE)
                        e = int(rel_end * SAMPLE_RATE)
                        if e > s:
                            embed_queue.put((abs_start, abs_end, segment_audio[s:e].copy()))
            else:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
                events.emit("segment_empty")

        except Exception as e:
            print(f"\r\033[K[transcription error] {e}", file=sys.stderr)
            events.emit("error", where="transcription", message=str(e))
        finally:
            transcribe_queue.task_done()
            events.emit("chunk_done", t=round(seg_t_start, 2), queued=transcribe_queue.qsize())
        flush_pending_injections(output_file, session_data)


def embed_worker(embedder, session_data):
    """Daemon thread: compute speaker embeddings for phrase audio.
    Exits when it receives None sentinel from the queue."""
    count = 0
    while True:
        item = embed_queue.get()
        if item is None:
            embed_queue.task_done()
            break
        t_start, t_end, audio = item
        count += 1
        try:
            vector = embedder.embed(audio)
            session_data["windows"].append(EmbeddingWindow(
                vector=vector, t_start=t_start, t_end=t_end,
            ))
            sys.stdout.write(f"\r\033[K[embedding] {count} phrases")
            sys.stdout.flush()
            events.emit_throttled("embedding", 1.0, "embedding", count=count)
        except Exception as e:
            print(f"[embed error] {e}", file=sys.stderr)
        finally:
            embed_queue.task_done()
    if count:
        sys.stdout.write(f"\r\033[K[embedding] done ({count} phrases)\n")
        sys.stdout.flush()


def vad_loop(vad_model):
    """Main-thread VAD loop: detect speech segments and enqueue for transcription."""
    vad_chunk_samples = 512  # 32ms at 16kHz
    silence_chunks = int((SILENCE_DURATION_MS / 1000) * SAMPLE_RATE / vad_chunk_samples)
    min_speech_chunks = int((MIN_SPEECH_DURATION_MS / 1000) * SAMPLE_RATE / vad_chunk_samples)

    soft_chunks = int(SOFT_CHUNK_SEC * SAMPLE_RATE / vad_chunk_samples)
    max_chunks = int(MAX_CHUNK_SEC * SAMPLE_RATE / vad_chunk_samples)
    search_chunks = int(CUT_SEARCH_SEC * SAMPLE_RATE / vad_chunk_samples)

    buffer = np.array([], dtype=np.float32)
    speech_buffer = np.array([], dtype=np.float32)
    speech_probs: list[float] = []   # one VAD probability per window in speech_buffer
    is_speaking = False
    silent_count = 0
    speech_count = 0

    # Sample-accurate timing
    samples_processed = 0
    speech_start_sample = 0

    while not stop_event.is_set():
        # Drain audio_queue into buffer
        try:
            chunk = audio_queue.get(timeout=0.1)
            buffer = np.concatenate([buffer, chunk])
        except queue.Empty:
            continue

        # Process all complete VAD windows
        while len(buffer) >= vad_chunk_samples:
            window = buffer[:vad_chunk_samples]
            buffer = buffer[vad_chunk_samples:]

            tensor = torch.from_numpy(window)
            speech_prob = vad_model(tensor, SAMPLE_RATE).item()
            is_speech = speech_prob >= VAD_THRESHOLD

            samples_processed += vad_chunk_samples

            if is_speech:
                if not is_speaking:
                    is_speaking = True
                    silent_count = 0
                    speech_count = 0
                    speech_start_sample = samples_processed - vad_chunk_samples
                    events.emit("speech_start")
                speech_count += 1
                silent_count = 0
                speech_buffer = np.concatenate([speech_buffer, window])
                speech_probs.append(speech_prob)
                # Show recording status with duration
                duration = len(speech_buffer) / SAMPLE_RATE
                sys.stdout.write(f"\r\033[K● speech [{duration:.1f}s]")
                sys.stdout.flush()
                events.emit_throttled("speech", 0.25, "speech", duration=round(duration, 1))
            else:
                if is_speaking:
                    silent_count += 1
                    speech_buffer = np.concatenate([speech_buffer, window])
                    speech_probs.append(speech_prob)

                    # Re-read each window so the pause can be changed while recording
                    silence_chunks = int((SILENCE_DURATION_MS / 1000) * SAMPLE_RATE / vad_chunk_samples)
                    if silent_count >= silence_chunks:
                        sys.stdout.write("\r\033[K")
                        sys.stdout.flush()
                        # End of speech segment
                        queued = speech_count >= min_speech_chunks
                        t_start = speech_start_sample / SAMPLE_RATE
                        if queued:
                            transcribe_queue.put((t_start, speech_buffer.copy()))
                        events.emit("speech_end", queued=queued, t=round(t_start, 2),
                                    duration=round(len(speech_buffer) / SAMPLE_RATE, 1))
                        speech_buffer = np.array([], dtype=np.float32)
                        speech_probs = []
                        is_speaking = False
                        silent_count = 0
                        speech_count = 0
                        continue

            # Long phrase without a real pause: cut it (see SOFT_CHUNK_SEC / MAX_CHUNK_SEC).
            if is_speaking:
                n_win = len(speech_probs)
                cut = None
                if n_win >= max_chunks:
                    region = speech_probs[n_win - search_chunks:]
                    cut = n_win - search_chunks + int(np.argmin(region)) + 1
                elif n_win >= soft_chunks and not is_speech:
                    cut = n_win
                if cut:
                    cut_samples = cut * vad_chunk_samples
                    t_start = speech_start_sample / SAMPLE_RATE
                    transcribe_queue.put((t_start, speech_buffer[:cut_samples].copy()))
                    events.emit("speech_end", queued=True, t=round(t_start, 2),
                                duration=round(cut_samples / SAMPLE_RATE, 1), forced=True)
                    speech_buffer = speech_buffer[cut_samples:]
                    speech_probs = speech_probs[cut:]
                    speech_start_sample += cut_samples
                    speech_count = sum(p >= VAD_THRESHOLD for p in speech_probs)
                    silent_count = 0
                    if not speech_probs:
                        is_speaking = False

    # Flush remaining speech buffer on shutdown
    if len(speech_buffer) > 0 and speech_count >= min_speech_chunks:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        t_start = speech_start_sample / SAMPLE_RATE
        transcribe_queue.put((t_start, speech_buffer.copy()))
        events.emit("speech_end", queued=True, t=round(t_start, 2),
                    duration=round(len(speech_buffer) / SAMPLE_RATE, 1))


def finalize_diarization(session_data, output_path, num_speakers, cluster_threshold,
                         raw_text=None, prefix_text=""):
    """Post-process: cluster embeddings, label words by speaker, rewrite transcript.

    raw_text: if given, written to .raw.txt (instead of renaming the provisional file).
    prefix_text: kept verbatim before the diarized lines (earlier part of a continued
    session that has no speaker data)."""
    segments = session_data["segments"]
    windows = session_data["windows"]
    injections = session_data["injections"]
    start_time = session_data["start_time"]

    if not windows:
        print("[diarization] No embedding windows collected, skipping.")
        return
    if not segments:
        print("[diarization] No ASR segments collected, skipping.")
        return

    print(f"[diarization] Clustering {len(windows)} embedding windows...")
    events.emit("diarization", state="clustering", windows=len(windows))
    labels = cluster_and_label(windows, num_speakers, cluster_threshold)

    n_speakers = len(set(labels))
    print(f"[diarization] Found {n_speakers} speaker(s).")
    events.emit("diarization", state="labeling", speakers=n_speakers)

    # Assign speaker labels to every word
    diarize_words(segments, windows, labels)

    # Collect all words in order
    all_words = []
    for seg in segments:
        all_words.extend(seg.words)

    if not all_words:
        print("[diarization] No words to label.")
        return

    # Group consecutive same-speaker words into items
    items = []  # (kind, time, speaker_or_0, text)
    current_speaker = all_words[0].speaker
    current_words = [all_words[0]]
    current_t = all_words[0].t_start

    for word in all_words[1:]:
        if word.speaker == current_speaker:
            current_words.append(word)
        else:
            text = " ".join(w.text for w in current_words)
            items.append(("speech", current_t, current_speaker, text))
            current_speaker = word.speaker
            current_words = [word]
            current_t = word.t_start

    text = " ".join(w.text for w in current_words)
    items.append(("speech", current_t, current_speaker, text))

    # Add injection markers (screenshots, selections)
    for t_rel, marker in injections:
        items.append(("injection", t_rel, 0, marker))

    # Sort everything by time
    items.sort(key=lambda x: x[1])

    # Build output lines
    lines = []
    for kind, t, speaker, content in items:
        wall = start_time + timedelta(seconds=t)
        time_str = wall.strftime("%H:%M:%S")
        if kind == "speech":
            lines.append(f"[{time_str}] [Speaker {speaker}] {content}")
        else:
            lines.append(f"[{time_str}] {content}")

    # Keep provisional as .raw.txt, write diarized to original path
    raw_path = output_path.with_suffix(".raw.txt")
    if raw_text is None:
        output_path.rename(raw_path)
    else:
        raw_path.write_text(raw_text, encoding="utf-8")
    output_path.write_text(prefix_text + "\n".join(lines) + "\n", encoding="utf-8")
    print(f"[diarization] Raw transcript kept: {raw_path}")
    print(f"[diarization] Diarized transcript: {output_path}")
    events.emit("diarization", state="done", speakers=n_speakers, path=str(output_path))


def ocr_worker():
    """Daemon thread: OCR screenshots and save text to .txt files."""
    reader = easyocr.Reader(OCR_LANGS, gpu=False, verbose=False)
    while True:
        try:
            image_path = ocr_queue.get(timeout=0.5)
        except queue.Empty:
            continue

        if image_path is None:  # sentinel → exit
            ocr_queue.task_done()
            break

        try:
            if not Path(image_path).exists():
                print(f"[OCR skip] file not found: {image_path}", file=sys.stderr)
                continue
            results = reader.readtext(image_path, detail=0, paragraph=True)
            text = "\n".join(results)
            txt_path = Path(image_path).with_suffix(".txt")
            txt_path.write_text(text, encoding="utf-8")
            time_str = datetime.now().strftime("%H:%M:%S")
            sys.stdout.write(f"\r\033[K[{time_str}] [OCR done: {txt_path.name}]\n")
            sys.stdout.flush()
            events.emit("ocr", path=image_path, txt=str(txt_path), chars=len(text))
        except Exception as e:
            print(f"[OCR error] {e}", file=sys.stderr)
            events.emit("ocr_error", path=image_path, message=str(e))
        finally:
            ocr_queue.task_done()


def helper_reader(proc):
    """Read events from the helper subprocess (screenshots, selections)."""
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("SCREENSHOT:"):
            path = line[len("SCREENSHOT:"):]
            marker = f"[screenshot: {path}]"
            pending_injections.put(marker)
            ocr_queue.put(path)
            events.emit("screenshot", path=path)
        elif line.startswith("FRAME:"):
            path = line[len("FRAME:"):]
            if live_frames is not None:
                live_frames.put(path)
            else:   # stray frame after AUTO OFF
                try:
                    os.unlink(path)
                except OSError:
                    pass
        elif line.startswith("SELECTION:"):
            b64text = line[len("SELECTION:"):]
            try:
                text = base64.b64decode(b64text).decode("utf-8")
                marker = f"[selection: {text}]"
                pending_injections.put(marker)
            except Exception as e:
                print(f"[selection error] {e}", file=sys.stderr)



def start_live_frames(frames_dir: Path, session_start: datetime, level1: bool = True):
    from autoframes import LiveFrames   # imported on demand: OpenCV is only needed for auto frames
    def on_scene(scene, kind):
        if kind in ("new", "revisit", "drift"):
            wall = datetime.fromisoformat(scene.wall_start).strftime("%H:%M:%S")
            label = {"new": f"frame #{scene.id}", "revisit": f"frame #{scene.id} = #{scene.same_as}",
                     "drift": f"frame #{scene.id} updated"}[kind]
            sys.stdout.write(f"\r\033[K[{wall}] [{label}]\n")
            sys.stdout.flush()
        events.emit("frame_scene", kind=kind, id=scene.id, path=scene.path, last_path=scene.last_path,
                    same_as=scene.same_as, scroll_of=scene.scroll_of, t_start=round(scene.t_start, 2),
                    t_end=round(scene.t_end, 2), wall_start=scene.wall_start, wall_end=scene.wall_end,
                    changed=scene.changed, mask_frac=scene.mask_frac, cls=scene.cls,
                    cls_score=scene.cls_score)

    def on_stats(stats):
        events.emit_throttled("frame_stats", 1.0, "frame_stats", **stats)

    return LiveFrames(frames_dir, session_start, on_scene=on_scene, on_stats=on_stats, level1=level1)


def shift_session_times(data, delta):
    """Shift all relative times in session data by *delta* seconds (in place)."""
    for seg in data["segments"]:
        seg.t_start += delta
        seg.t_end += delta
        for w in seg.words:
            w.t_start += delta
            w.t_end += delta
    for win in data["windows"]:
        win.t_start += delta
        win.t_end += delta
    data["injections"] = [(t + delta, m) for t, m in data["injections"]]


def save_audio_recording(project_dir: Path, timestamp: str):
    """Save recorded audio chunks as MP3 (falls back to WAV if ffmpeg unavailable)."""
    import wave

    if not audio_chunks:
        print("[audio] No audio recorded.")
        return

    audio = np.concatenate(audio_chunks)
    duration = len(audio) / SAMPLE_RATE
    print(f"[audio] Saving {duration:.1f}s of audio...")

    wav_path = project_dir / f"recording_{timestamp}.wav"
    mp3_path = project_dir / f"recording_{timestamp}.mp3"

    # Write WAV
    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_int16.tobytes())

    # Convert to MP3 via ffmpeg
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", str(wav_path), "-b:a", "64k", str(mp3_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            wav_path.unlink()
            print(f"[audio] Saved: {mp3_path}")
        else:
            print(f"[audio] ffmpeg error: {result.stderr.strip()}", file=sys.stderr)
            print(f"[audio] WAV kept: {wav_path}")
    except FileNotFoundError:
        print(f"[audio] ffmpeg not found, WAV kept: {wav_path}")


def setup_console():
    """UTF-8 output (phrases contain ñ, á, →, ●) and ANSI escapes on the Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def main():
    global SILENCE_DURATION_MS
    setup_console()

    parser = argparse.ArgumentParser(description="Real-time English/Spanish speech-to-text")
    parser.add_argument("project", help="Project name (isolated directory for outputs)")
    parser.add_argument("minutes", type=float, nargs="?", default=180,
                        help="Auto-stop after this many minutes (default: 180)")
    parser.add_argument("--save-audio", action="store_true", help="Save audio recording as MP3")
    parser.add_argument(
        "--lang", type=str, default=LANGUAGE,
        help="Language: en, es, or 'auto' to detect English/Spanish per phrase (default: auto)",
    )
    parser.add_argument(
        "--pause", type=float, default=SILENCE_DURATION_MS / 1000,
        help=f"Silence duration to end a phrase in seconds (default: {SILENCE_DURATION_MS / 1000})",
    )
    parser.add_argument(
        "--speakers", type=str, default=None,
        help="Enable diarization: number of speakers (e.g. 2) or 'auto'",
    )
    parser.add_argument(
        "--cluster-threshold", type=float, default=0.6,
        help="Cosine distance threshold for auto speaker detection (default: 0.6)",
    )
    # Used by the web UI (server.py); console mode doesn't need them.
    parser.add_argument("--events", default=None,
                        help="Write structured JSON-lines events to this file")
    parser.add_argument("--control-stdin", action="store_true",
                        help="Accept commands on stdin: SNAP, STOP, PAUSE <sec>, MUTE, UNMUTE")
    parser.add_argument("--capture-config", default=None,
                        help="JSON capture config for the screenshot helper (area / window)")
    parser.add_argument("--continue-session", default=None, metavar="TIMESTAMP",
                        help="Append to an existing transcript_<TIMESTAMP>.txt instead of starting a new one")
    parser.add_argument("--vocab-file", default=None,
                        help='Notes file with \'- "heard" → "meant"\' lines; the correct terms are '
                             "passed to Whisper as hints (re-read on change)")
    parser.add_argument("--glossary-file", default=None,
                        help='Project glossary (meetings vault): "- Term" lines and \'- "heard" → "meant"\' '
                             "corrections, used like --vocab-file (re-read on change)")
    parser.add_argument("--engine", choices=ENGINES, default="whisper",
                        help="Speech recognition engine: whisper (default, en/es detection per phrase, hotwords), "
                             "parakeet (Parakeet TDT v3, much faster on CPU, detects the language itself); "
                             "gigaam is Russian only")
    parser.add_argument("--asr-model", default=None,
                        help="Override the engine's model: a Whisper size, or an onnx-asr model name "
                             "(e.g. gigaam-v3-e2e-ctc)")
    parser.add_argument("--auto-frames", type=float, default=0, metavar="SEC",
                        help="Capture the screen every SEC seconds and keep the frames that show "
                             "something new (projects/<project>/frames/<session>/); 0 = off. "
                             "With --control-stdin it can also be toggled with AUTO ON/OFF")
    parser.add_argument("--no-frames-l1", action="store_true",
                        help="Auto frames without Level 1 (CLIP: drops faces, merges look-alike frames)")
    # Windows build (audio_sources.py)
    parser.add_argument("--system-audio", action="store_true",
                        help="Also record what you hear (the other participants of a call when you "
                             "use a headset), mixed with the microphone")
    parser.add_argument("--mic-device", default="",
                        help="Microphone name (or part of it); empty = the Windows default microphone")
    parser.add_argument("--output-device", default="",
                        help="Output whose audio is recorded with --system-audio: empty = all outputs, "
                             "'default' or a name")
    parser.add_argument("--vocab-correct", action="store_true",
                        help='Replace "heard" phrases from --vocab-file with the "meant" ones in the output '
                             "(useful with gigaam/parakeet, which have no hotwords)")
    args = parser.parse_args()
    if args.lang.lower() not in ("auto", *SUPPORTED_LANGS):
        parser.error(f"--lang must be auto, en or es (got {args.lang})")
    if args.events:
        events.open(args.events)
    global vocab_path, glossary_path
    if args.vocab_file:
        vocab_path = Path(args.vocab_file)
    if args.glossary_file:
        glossary_path = Path(args.glossary_file)
    SILENCE_DURATION_MS = int(args.pause * 1000)
    global vocab_correct
    vocab_correct = args.vocab_correct
    if args.engine == "gigaam":
        print("--engine gigaam recognizes Russian only; use whisper or parakeet for English/Spanish",
              file=sys.stderr)
        sys.exit(1)
    if args.engine == "parakeet" and args.lang.lower() != "auto":
        print(f"[lang] Parakeet detects the language by itself; with --lang {args.lang} phrases "
              f"that come out in another language are redone with Whisper")

    # Parse diarization settings
    enable_diarization = args.speakers is not None
    num_speakers = None
    if enable_diarization:
        if not _DIARIZATION_AVAILABLE:
            print("Diarization requires speaker_id.py and dependencies:", file=sys.stderr)
            print("  pip install speechbrain scikit-learn", file=sys.stderr)
            sys.exit(1)
        if args.speakers.lower() == "auto":
            num_speakers = None
        else:
            try:
                num_speakers = int(args.speakers)
            except ValueError:
                print(f"--speakers must be a number or 'auto', got: {args.speakers}",
                      file=sys.stderr)
                sys.exit(1)

    global screenshot_dir, save_audio_flag

    project_dir = Path("projects") / args.project
    output_dir = project_dir / "transcripts"
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir = project_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    save_audio_flag = args.save_audio
    prefix_text = None          # existing transcript content when continuing a session
    if args.continue_session:
        timestamp = args.continue_session
        output_path = output_dir / f"transcript_{timestamp}.txt"
        if not output_path.exists():
            print(f"Transcript to continue not found: {output_path}", file=sys.stderr)
            sys.exit(1)
        prefix_text = output_path.read_text(encoding="utf-8")
        if prefix_text and not prefix_text.endswith("\n"):
            prefix_text += "\n"
            output_path.write_text(prefix_text, encoding="utf-8")
        output_file = open(output_path, "a", encoding="utf-8")
        print(f"Continuing session {timestamp} ({len(prefix_text.splitlines())} existing lines)")
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"transcript_{timestamp}.txt"
        output_file = open(output_path, "w", encoding="utf-8")

    # Shared session state
    session_data = {
        "start_time": datetime.now(),
        "segments": [],     # list[AsrSegment]  — filled by transcription_worker
        "windows": [],      # list[EmbeddingWindow] — filled by embed_worker
        "injections": [],   # list[(t_relative, marker_text)]
    }
    events.emit("session", project=args.project, project_dir=str(project_dir.resolve()),
                transcript_path=str(output_path.resolve()), timestamp=timestamp,
                start_time=session_data["start_time"].isoformat(),
                diarization=enable_diarization, continued=prefix_text is not None)

    asr_engine, vad_model = load_models(args.engine, args.asr_model)
    if (args.vocab_file or args.glossary_file) and not asr_engine.supports_hotwords and not args.vocab_correct:
        print(f"[vocab] {args.engine} has no hotwords; vocab hints are ignored (add --vocab-correct "
              "to apply the notes' corrections to the output)")

    embedder = None
    if enable_diarization:
        print("Loading speaker embedding model (ECAPA-TDNN)...")
        events.emit("status", state="loading", detail="Loading speaker model")
        embedder = SpeakerEmbedder(device=DEVICE)
        print("Speaker model loaded.")
        if num_speakers:
            print(f"Diarization: {num_speakers} speaker(s)")
        else:
            print(f"Diarization: auto (threshold={args.cluster_threshold})")

    print(f"Project: {args.project} ({project_dir.resolve()})")
    if args.engine != "whisper":
        print(f"Engine: {asr_engine.label}")
    print(f"Language: {args.lang}")
    print(f"Silence threshold: {SILENCE_DURATION_MS}ms")

    # SIGINT handler
    def on_sigint(signum, frame):
        print("\nStopping...")
        events.emit("status", state="stopping")
        stop_event.set()

    signal.signal(signal.SIGINT, on_sigint)

    # Start transcription worker thread
    lang_mode = args.lang.lower()
    if lang_mode != "auto" and args.engine != "whisper":
        lang_fallback.start()
    worker = threading.Thread(
        target=transcription_worker,
        args=(asr_engine, output_file, session_data, enable_diarization, lang_mode),
        daemon=True,
    )
    worker.start()

    # Start embed worker if diarization enabled
    if enable_diarization:
        embed_thread = threading.Thread(
            target=embed_worker,
            args=(embedder, session_data),
            daemon=True,
        )
        embed_thread.start()

    # Start OCR worker thread
    ocr_thread = threading.Thread(target=ocr_worker, daemon=True)
    ocr_thread.start()

    # Start audio stream
    stream = None
    if args.system_audio or args.mic_device:
        # Windows build: microphone + what you hear (the others in a call with a headset).
        try:
            from audio_sources import MixedInput
            def on_audio_event(kind, message):
                # A lost / restored audio device must never go unnoticed (the UI shows a toast).
                if kind == "stalled":
                    events.emit("error", where="audio", message=message)
                else:
                    events.emit("audio_restored", message=message)

            stream = MixedInput(audio_callback, SAMPLE_RATE, 1600, mic=args.mic_device,
                                system=args.system_audio, output=args.output_device,
                                on_event=on_audio_event)
            print(f"Audio: {stream.describe()}")
            events.emit("audio_sources", description=stream.describe())
        except Exception as e:
            print(f"[audio] system audio unavailable ({e}); recording the default microphone only",
                  file=sys.stderr)
            events.emit("error", where="audio", message=f"System audio unavailable: {e}")
            stream = None
    if stream is None:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=1600,  # 100ms blocks
            callback=audio_callback,
        )
    stream.start()

    # Start native screenshot helper
    helper_cmd = [sys.executable, str(SCREENSHOT_HELPER), str(screenshot_dir.resolve())]
    if args.capture_config:
        helper_cmd.append(str(Path(args.capture_config).resolve()))
    screenshot_proc = subprocess.Popen(
        helper_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Keep Ctrl+C for main.py only; it stops the helper itself.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    # Drain the helper's log so a full stderr pipe can never block it.
    threading.Thread(target=lambda: [None for _ in screenshot_proc.stderr], daemon=True).start()
    helper_thread = threading.Thread(
        target=helper_reader, args=(screenshot_proc,), daemon=True
    )
    helper_thread.start()

    frames_dir = project_dir / "frames" / timestamp
    frames_interval = args.auto_frames or 1.0
    frames_lock = threading.Lock()

    def set_auto_frames(on: bool):
        global live_frames
        with frames_lock:
            if on and live_frames is None:
                live_frames = start_live_frames(frames_dir, session_data["start_time"],
                                                level1=not args.no_frames_l1)
            try:
                screenshot_proc.stdin.write(f"AUTO {frames_interval}\n" if on else "AUTO OFF\n")
                screenshot_proc.stdin.flush()
            except Exception as e:
                print(f"[frames] helper command failed: {e}", file=sys.stderr)
        print(f"\r\033[K[frames] auto {'on' if on else 'off'}")
        events.emit("auto_frames", on=on, interval=frames_interval, dir=str(frames_dir.resolve()))

    if args.auto_frames > 0:
        set_auto_frames(True)

    if args.control_stdin:
        def control_reader():
            for line in sys.stdin:
                cmd = line.strip().upper()
                if cmd == "SNAP":
                    try:
                        screenshot_proc.stdin.write("SNAP\n")
                        screenshot_proc.stdin.flush()
                    except Exception as e:
                        print(f"[control] snap failed: {e}", file=sys.stderr)
                elif cmd.startswith("PAUSE "):
                    global SILENCE_DURATION_MS
                    try:
                        SILENCE_DURATION_MS = int(max(0.2, min(10.0, float(cmd.split()[1]))) * 1000)
                        print(f"\r\033[K[pause] {SILENCE_DURATION_MS}ms")
                        events.emit("pause", seconds=SILENCE_DURATION_MS / 1000)
                    except ValueError:
                        pass
                elif cmd in ("MUTE", "UNMUTE"):
                    if cmd == "MUTE":
                        mic_muted.set()
                    else:
                        mic_muted.clear()
                    print(f"\r\033[K[mic] {'muted' if cmd == 'MUTE' else 'live'}")
                    events.emit("muted", muted=cmd == "MUTE")
                elif cmd in ("AUTO ON", "AUTO OFF"):
                    set_auto_frames(cmd == "AUTO ON")
                elif cmd == "STOP":
                    events.emit("status", state="stopping")
                    stop_event.set()
        threading.Thread(target=control_reader, daemon=True).start()
    # Auto-stop timer
    auto_stop_sec = args.minutes * 60
    auto_stop_timer = threading.Timer(auto_stop_sec, lambda: (
        print(f"\n[auto-stop] {args.minutes} min elapsed, finalizing..."),
        events.emit("status", state="stopping", detail="auto-stop"),
        stop_event.set(),
    ))
    auto_stop_timer.daemon = True
    auto_stop_timer.start()

    print(f"Listening on default microphone (auto-stop in {args.minutes} min, Ctrl+C to stop early)...")
    print(f"Ctrl+Shift+S: screenshot, Ctrl+Shift+W: selection")
    events.emit("status", state="listening", engine=args.engine)

    try:
        vad_loop(vad_model)
    finally:
        auto_stop_timer.cancel()
        screenshot_proc.terminate()
        if live_frames is not None:
            live_frames.close()
            print(f"[frames] {len(live_frames.store.scenes)} scene(s): {live_frames.store.json_path}")
        for leftover in (screenshot_dir / ".frames").glob("frame_*.jpg"):
            leftover.unlink(missing_ok=True)
        stream.stop()
        stream.close()
        events.emit("status", state="finalizing", detail="Transcribing remaining speech")
        # Signal workers to finish (sentinel after all real items)
        transcribe_queue.put(None)
        transcribe_queue.join()
        if enable_diarization:
            embed_queue.put(None)
            embed_queue.join()
        ocr_queue.put(None)
        ocr_queue.join()
        output_file.close()

        # Save audio recording
        if save_audio_flag:
            part = timestamp if prefix_text is None else \
                f"{timestamp}_part_{session_data['start_time']:%H%M%S}"
            save_audio_recording(project_dir, part)

        # Save session data for re-diarization
        if enable_diarization:
            import pickle
            session_path = project_dir / f"session_{timestamp}.pkl"
            raw_text = None
            prefix_for_output = ""
            diar_data = session_data
            if prefix_text is not None:
                # Continued session: the new raw lines are what was appended in this run.
                new_raw = output_path.read_text(encoding="utf-8")[len(prefix_text):]
                old_raw_path = output_path.with_suffix(".raw.txt")
                old_raw = old_raw_path.read_text(encoding="utf-8") if old_raw_path.exists() else prefix_text
                raw_text = old_raw + new_raw
                prev = None
                if session_path.exists():
                    try:
                        with open(session_path, "rb") as f:
                            prev = pickle.load(f)
                    except Exception as e:
                        print(f"[diarization] Could not load previous session data: {e}", file=sys.stderr)
                if prev:
                    # Re-cluster both parts together so speaker numbers stay consistent.
                    shift_session_times(session_data,
                                        (session_data["start_time"] - prev["start_time"]).total_seconds())
                    diar_data = {
                        "start_time": prev["start_time"],
                        "segments": prev["segments"] + session_data["segments"],
                        "windows": prev["windows"] + session_data["windows"],
                        "injections": prev["injections"] + session_data["injections"],
                    }
                else:
                    # Earlier part has no speaker data: keep it as is, diarize only the new part.
                    prefix_for_output = prefix_text
            with open(session_path, "wb") as f:
                pickle.dump({
                    "start_time": diar_data["start_time"],
                    "segments": diar_data["segments"],
                    "windows": diar_data["windows"],
                    "injections": diar_data["injections"],
                    "transcript_path": str(output_path),
                }, f)
            print(f"Session data saved: {session_path}")

            finalize_diarization(
                diar_data, output_path, num_speakers, args.cluster_threshold,
                raw_text=raw_text, prefix_text=prefix_for_output,
            )

        print(f"Transcript saved to: {output_path}")
        events.emit("finished", transcript_path=str(output_path.resolve()))
        events.close()


if __name__ == "__main__":
    main()
