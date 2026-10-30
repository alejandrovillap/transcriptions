"""Speaker labels using WHERE the sound came from (Windows build).

The recorder captures the microphone (you) and the call audio (everyone else, through the
WASAPI loopback) separately before mixing them, and stores their energy for every 100 ms
block (session_data["block_src"] = [(mic_rms, sys_rms), ...]). That separates "me" from
"others" far more reliably than voice embeddings alone, which over-split a two-person call
into a dozen speakers:

  - a phrase while the call audio is silent and the microphone isn't  -> me (one speaker)
  - a phrase while the call audio carries speech                       -> others; only these
    are clustered by voice (num_speakers - 1 of them when the count is fixed)
  - the rest (both silent: very short or unclear)                      -> nearest speaker

Returns None when there is no usable source data (system audio was off, a continued session,
an old recording), and the caller falls back to plain clustering.
"""

from __future__ import annotations

import numpy as np

BLOCK_SEC = 0.1          # one entry of block_src per 100 ms block (MixedInput blocksize 1600 @ 16 kHz)
MIN_FLOOR = 0.002        # absolute floor for "silent"
FLOOR_FACTOR = 3.0       # source "active" above FLOOR_FACTOR × its background level (5th percentile)
MIN_OTHERS_WINDOWS = 2   # need at least this much evidence of the call audio to use sources


def _floors(src: np.ndarray) -> tuple[float, float]:
    mic, sys_ = src[:, 0], src[:, 1]
    mic_f = max(MIN_FLOOR, FLOOR_FACTOR * float(np.percentile(mic, 5)))
    sys_f = max(MIN_FLOOR, FLOOR_FACTOR * float(np.percentile(sys_, 5)))
    return mic_f, sys_f


def classify_windows(windows, block_src, offset_sec: float = 0.0) -> list[str | None]:
    """'me' / 'others' / None (unclear) for every embedding window."""
    src = np.asarray(block_src, dtype=np.float32)
    if src.ndim != 2 or len(src) < 10:
        return [None] * len(windows)
    mic_f, sys_f = _floors(src)
    out = []
    for w in windows:
        a = int((w.t_start - offset_sec) / BLOCK_SEC)
        b = max(a + 1, int(np.ceil((w.t_end - offset_sec) / BLOCK_SEC)))
        seg = src[max(0, a):min(len(src), b)]
        if not len(seg):
            out.append(None)
            continue
        mic, sys_ = float(seg[:, 0].mean()), float(seg[:, 1].mean())
        if sys_ > sys_f:
            out.append("others")          # the call carries speech (even if the mic hears it too)
        elif mic > mic_f:
            out.append("me")              # only the microphone
        else:
            out.append(None)
    return out


def labels_with_sources(windows, block_src, num_speakers=None, threshold: float = 0.6):
    """1-indexed speaker labels (by first appearance) for *windows*, or None."""
    if not block_src or not windows:
        return None
    from speaker_id import cluster_and_label

    kinds = classify_windows(windows, block_src)
    me = [i for i, k in enumerate(kinds) if k == "me"]
    others = [i for i, k in enumerate(kinds) if k == "others"]
    if len(others) < MIN_OTHERS_WINDOWS:
        return None                       # no call audio worth trusting: plain clustering

    labels: list = [None] * len(windows)
    for i in me:
        labels[i] = "me"
    n_others = None
    if num_speakers:
        n_others = max(1, num_speakers - (1 if me else 0))
    sub = cluster_and_label([windows[i] for i in others], n_others, threshold) if len(others) > 1 else [1]
    for i, lbl in zip(others, sub):
        labels[i] = f"o{lbl}"

    # Unclear windows: nearest speaker centroid.
    vecs = np.array([w.vector for w in windows])
    cents = {}
    for lbl in set(l for l in labels if l is not None):
        c = vecs[[i for i, l in enumerate(labels) if l == lbl]].mean(axis=0)
        cents[lbl] = c / (np.linalg.norm(c) + 1e-8)
    for i, l in enumerate(labels):
        if l is None:
            labels[i] = max(cents, key=lambda k: float(vecs[i] @ cents[k]))

    # Renumber 1..n by first appearance (same convention as speaker_id.cluster_and_label).
    order: dict = {}
    for w, l in sorted(zip(windows, labels), key=lambda x: x[0].t_start):
        order.setdefault(l, len(order) + 1)
    return [order[l] for l in labels]


def summary(windows, block_src) -> str:
    kinds = classify_windows(windows, block_src)
    return (f"{kinds.count('me')} me / {kinds.count('others')} others / "
            f"{kinds.count(None)} unclear windows")
