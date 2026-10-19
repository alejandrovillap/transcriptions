"""Speaker embedding and diarization utilities."""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class WordToken:
    text: str
    t_start: float
    t_end: float
    speaker: int = 0


@dataclass
class AsrSegment:
    t_start: float
    t_end: float
    words: list = field(default_factory=list)


@dataclass
class EmbeddingWindow:
    vector: np.ndarray
    t_start: float
    t_end: float


class SpeakerEmbedder:
    """Compute speaker embeddings using SpeechBrain ECAPA-TDNN (192-dim)."""

    def __init__(self, device="cpu"):
        try:
            from speechbrain.inference.speaker import EncoderClassifier
        except ImportError:
            from speechbrain.pretrained import EncoderClassifier
        kwargs = {}
        try:
            # Windows needs admin / Developer Mode for symlinks: copy the model files instead.
            from speechbrain.utils.fetching import LocalStrategy
            kwargs["local_strategy"] = LocalStrategy.COPY
        except ImportError:
            pass
        self.model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": device},
            **kwargs,
        )

    def embed(self, audio: np.ndarray) -> np.ndarray:
        """Return L2-normalised 192-dim embedding from float32 mono audio."""
        import torch

        tensor = torch.from_numpy(audio).unsqueeze(0)
        with torch.no_grad():
            embedding = self.model.encode_batch(tensor)
        vec = embedding.squeeze().cpu().numpy()
        vec = vec / (np.linalg.norm(vec) + 1e-8)
        return vec.astype(np.float32)


# Embeddings of very short phrases (padded to ~1 s) are noisy: alone they form spurious
# one-window "speakers". They are left out of clustering and joined to the nearest speaker.
RELIABLE_WINDOW_SEC = 1.5
# In automatic mode a "speaker" with less speech than this is merged into the closest one.
MIN_SPEAKER_SEC = 4.0
# ...and two speakers whose centroids are closer than this (cosine distance) are one voice.
MERGE_CENTROID_DIST = 0.45
# Speaker turns this short (seconds and words) between two turns of one speaker are
# relabelled to that speaker.
MIN_TURN_SEC = 1.0
MIN_TURN_WORDS = 2


def _centroids(vectors, labels):
    cents = {}
    for lbl in set(labels):
        c = vectors[[i for i, l in enumerate(labels) if l == lbl]].mean(axis=0)
        cents[lbl] = c / (np.linalg.norm(c) + 1e-8)
    return cents


def _nearest(vec, cents):
    return max(cents, key=lambda l: float(vec @ cents[l]))


def cluster_and_label(windows, num_speakers=None, threshold=0.6):
    """Cluster embedding windows; return 1-indexed labels ordered by first appearance.

    Only windows of at least RELIABLE_WINDOW_SEC are clustered; shorter ones get the
    speaker with the nearest centroid. In automatic mode (num_speakers None) speakers
    with less than MIN_SPEAKER_SEC of speech are merged into the nearest one."""
    if not windows:
        return []
    if len(windows) == 1:
        return [1]

    from sklearn.cluster import AgglomerativeClustering

    vectors = np.array([w.vector for w in windows])
    durs = np.array([w.t_end - w.t_start for w in windows])
    fit_idx = [i for i, d in enumerate(durs) if d >= RELIABLE_WINDOW_SEC]
    if len(fit_idx) < 2:
        fit_idx = list(range(len(windows)))

    if num_speakers is not None:
        n = min(num_speakers, len(fit_idx))
        clustering = AgglomerativeClustering(
            n_clusters=n, metric="cosine", linkage="average",
        )
    else:
        clustering = AgglomerativeClustering(
            n_clusters=None, distance_threshold=threshold,
            metric="cosine", linkage="average",
        )
    fit_labels = list(clustering.fit_predict(vectors[fit_idx]))

    if num_speakers is None:
        # Merge the smallest under-MIN_SPEAKER_SEC speaker into its nearest one, repeatedly.
        while len(set(fit_labels)) > 1:
            speech = {l: durs[[fit_idx[k] for k, x in enumerate(fit_labels) if x == l]].sum()
                      for l in set(fit_labels)}
            small = min(speech, key=speech.get)
            if speech[small] >= MIN_SPEAKER_SEC:
                break
            cents = _centroids(vectors[fit_idx], fit_labels)
            del_c = cents.pop(small)
            target = _nearest(del_c, cents)
            fit_labels = [target if l == small else l for l in fit_labels]
        while len(set(fit_labels)) > 1:
            cents = _centroids(vectors[fit_idx], fit_labels)
            ks = sorted(cents)
            dist, a, b = min((1 - float(cents[a] @ cents[b]), a, b)
                             for i, a in enumerate(ks) for b in ks[i + 1:])
            if dist >= MERGE_CENTROID_DIST:
                break
            fit_labels = [a if l == b else l for l in fit_labels]

    raw_labels = [None] * len(windows)
    for k, i in enumerate(fit_idx):
        raw_labels[i] = fit_labels[k]
    cents = _centroids(vectors[fit_idx], fit_labels)
    for i in range(len(windows)):
        if raw_labels[i] is None:
            raw_labels[i] = _nearest(vectors[i], cents)

    # Renumber by first appearance in time
    first_time = {}
    for i, w in enumerate(windows):
        lbl = raw_labels[i]
        if lbl not in first_time:
            first_time[lbl] = w.t_start
    sorted_labels = sorted(first_time, key=lambda l: first_time[l])
    label_map = {old: new + 1 for new, old in enumerate(sorted_labels)}

    return [label_map[l] for l in raw_labels]


def speaker_for_time(t, windows, labels):
    """Determine speaker at time *t* by majority vote of covering windows."""
    covering = [labels[i] for i, w in enumerate(windows)
                if w.t_start <= t <= w.t_end]
    if covering:
        return max(set(covering), key=covering.count)
    # Fallback: nearest window
    nearest = min(range(len(windows)),
                  key=lambda i: min(abs(t - windows[i].t_start),
                                    abs(t - windows[i].t_end)))
    return labels[nearest]


def diarize_words(segments, windows, labels):
    """Assign speaker labels to every WordToken in *segments* (in-place)."""
    for seg in segments:
        for word in seg.words:
            t_mid = (word.t_start + word.t_end) / 2
            word.speaker = speaker_for_time(t_mid, windows, labels)
    smooth_turns([w for seg in segments for w in seg.words])


def smooth_turns(words):
    """Relabel very short turns (MIN_TURN_SEC / MIN_TURN_WORDS) sandwiched between two
    turns of the same speaker: one or two words flipping speaker are almost always noise."""
    changed = True
    while changed:
        changed = False
        runs = []  # [start_idx, end_idx_exclusive, speaker]
        for i, w in enumerate(words):
            if runs and runs[-1][2] == w.speaker:
                runs[-1][1] = i + 1
            else:
                runs.append([i, i + 1, w.speaker])
        # One pass relabels every short turn; a relabelled turn joins its neighbours, so
        # the next run is skipped and re-checked on the next pass (linear per pass).
        k = 1
        while k < len(runs) - 1:
            a, b, _ = runs[k]
            dur = words[b - 1].t_end - words[a].t_start
            if runs[k - 1][2] == runs[k + 1][2] and (b - a <= MIN_TURN_WORDS or dur < MIN_TURN_SEC):
                for w in words[a:b]:
                    w.speaker = runs[k - 1][2]
                changed = True
                k += 2
            else:
                k += 1
