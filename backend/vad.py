"""
vad.py — Voice Activity Detection using Silero VAD (ONNX, fully local)

Reads a continuous stream of float32 PCM samples (16 kHz mono) and emits
complete speech chunks ready for Whisper transcription.
"""

import numpy as np
import onnxruntime as ort
import urllib.request
import os
import tempfile
import logging
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SAMPLE_RATE      = 16000
WINDOW_SIZE      = 512          # Silero VAD window (512 samples @ 16kHz = 32ms)
MIN_SPEECH_MS    = 250          # ignore speech bursts shorter than this
MIN_SILENCE_MS   = 800          # silence needed to trigger chunk flush
MAX_CHUNK_S      = 12.0         # force-flush if chunk grows beyond this (was 5.0)
SPEECH_THRESHOLD = 0.5          # VAD confidence threshold (0–1)

SILERO_MODEL_URL = (
    "https://github.com/snakers4/silero-vad/raw/v4.0/files/silero_vad.onnx"
)


class SileroVAD:
    """Wraps the Silero VAD ONNX model."""

    def __init__(self, model_path: Path):
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_path), sess_options=opts)
        self._reset_state()

    def _reset_state(self):
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def reset(self):
        self._reset_state()

    def __call__(self, window: np.ndarray) -> float:
        """Return speech probability for a 512-sample window."""
        x = window.astype(np.float32)[None, :]          # (1, 512)
        sr = np.array(SAMPLE_RATE, dtype=np.int64)
        out, h, c = self.session.run(
            None,
            {"input": x, "sr": sr, "h": self._h, "c": self._c},
        )
        self._h, self._c = h, c
        return float(out[0][0])


# ── Model download / cache ────────────────────────────────────────────────────

def ensure_silero_model(models_dir: Path) -> Path:
    """Download Silero VAD ONNX model if not already cached."""
    models_dir.mkdir(parents=True, exist_ok=True)
    dst = models_dir / "silero_vad.onnx"
    if not dst.exists():
        logger.info("Downloading Silero VAD model (one-time) …")
        urllib.request.urlretrieve(SILERO_MODEL_URL, dst)
        logger.info(f"Saved to {dst}")
    return dst


# ── VAD chunker ───────────────────────────────────────────────────────────────

class VADChunker:
    """
    Consumes a stream of raw float32 samples and yields speech chunks.

    Usage:
        chunker = VADChunker(models_dir)
        for chunk in chunker.feed(sample_generator):
            transcribe(chunk)
    """

    def __init__(self, models_dir: Path):
        model_path = ensure_silero_model(models_dir)
        self.vad   = SileroVAD(model_path)

        self._min_speech_samples  = int(SAMPLE_RATE * MIN_SPEECH_MS  / 1000)
        self._min_silence_samples = int(SAMPLE_RATE * MIN_SILENCE_MS / 1000)
        self._max_chunk_samples   = int(SAMPLE_RATE * MAX_CHUNK_S)

        self._buffer:         list[np.ndarray] = []   # current speech accumulator
        self._silence_frames: int              = 0    # consecutive silent windows
        self._speech_frames:  int              = 0    # consecutive speech windows
        self._in_speech:      bool             = False

    # ── Public API ────────────────────────────────────────────────────────────

    def feed(self, samples: np.ndarray) -> Generator[np.ndarray, None, None]:
        """
        Feed arbitrary-length sample arrays. Yields complete speech chunks.
        Call this continuously as audio arrives.
        """
        # Slide over in VAD window steps
        offset = 0
        while offset + WINDOW_SIZE <= len(samples):
            window = samples[offset : offset + WINDOW_SIZE]
            offset += WINDOW_SIZE
            chunk  = self._process_window(window)
            if chunk is not None:
                yield chunk

    def flush(self) -> Optional[np.ndarray]:
        """Force-emit whatever is buffered (call on shutdown)."""
        if self._buffer:
            chunk = self._collect()
            self.vad.reset()
            return chunk
        return None

    def reset(self):
        """Drop all buffered audio — call when switching sources."""
        self._buffer = []
        self._silence_frames = 0
        self._speech_frames = 0
        self._in_speech = False
        self.vad.reset()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _process_window(self, window: np.ndarray) -> Optional[np.ndarray]:
        prob = self.vad(window)
        is_speech = prob >= SPEECH_THRESHOLD

        if is_speech:
            self._silence_frames = 0
            self._speech_frames += 1

            if not self._in_speech:
                if self._speech_frames >= (self._min_speech_samples // WINDOW_SIZE):
                    self._in_speech = True
                    logger.debug("VAD: speech start")

            if self._in_speech:
                self._buffer.append(window)

                # Force-flush if chunk is getting too long
                total = sum(len(w) for w in self._buffer)
                if total >= self._max_chunk_samples:
                    logger.debug("VAD: force-flush (max duration reached)")
                    chunk = self._collect()
                    self._in_speech = False
                    self._speech_frames = 0
                    self.vad.reset()
                    return chunk

        else:
            self._speech_frames = 0
            if self._in_speech:
                self._buffer.append(window)          # keep trailing silence in chunk
                self._silence_frames += 1

                # Flush when we've had enough silence → end of utterance
                if self._silence_frames >= (self._min_silence_samples // WINDOW_SIZE):
                    logger.debug("VAD: silence detected — flushing chunk")
                    chunk = self._collect()
                    self._in_speech     = False
                    self._silence_frames = 0
                    self.vad.reset()
                    return chunk

        return None

    def _collect(self) -> np.ndarray:
        chunk = np.concatenate(self._buffer)
        self._buffer = []
        self._silence_frames = 0
        return chunk
