"""
transcriber.py — Wraps faster-whisper for local, offline transcription.

Supports English, Chinese, Korean, Japanese with automatic language detection.
Runs entirely on-device — no internet, no API keys.
"""

import logging
import time
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Languages we care about — Whisper language codes
SUPPORTED_LANGUAGES = {"en", "zh", "ko", "ja"}

# Map Whisper codes → display names
LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Chinese",
    "ko": "Korean",
    "ja": "Japanese",
}

# Repetition detection — if any single token repeats more than this many times
# in a row, the result is a hallucination loop and should be discarded
MAX_REPEATED_TOKEN = 4


def _is_repetition_loop(text: str) -> bool:
    """Detect Whisper hallucination loops like 'ってってってってって'."""
    if len(text) < 6:
        return False
    # Check for repeated substrings of length 1-6
    for n in range(1, 7):
        sub = text[:n]
        repeat_count = 0
        i = 0
        while i + n <= len(text):
            if text[i:i+n] == sub:
                repeat_count += 1
                i += n
            else:
                break
        if repeat_count >= MAX_REPEATED_TOKEN and repeat_count * n > len(text) * 0.6:
            return True
    return False


@dataclass
class TranscriptResult:
    text:       str
    language:   str          # ISO code: en / zh / ko / ja
    confidence: float        # 0–1 average segment confidence
    duration_s: float        # how long transcription took


class Transcriber:
    """
    Wraps faster-whisper WhisperModel for chunk-based transcription.

    model_size options (tradeoff guide):
        tiny    — fastest, least accurate   (~75 MB)
        base    — good for English          (~145 MB)
        small   — recommended for CJK test  (~465 MB)
        medium  — best balance              (~1.5 GB)
        large-v3— highest accuracy          (~3 GB)

    device: "cpu" always works; "cuda" for NVIDIA GPU; "mps" NOT supported
            by faster-whisper yet — Apple Silicon uses "cpu" (still fast via
            Accelerate framework).
    """

    def __init__(
        self,
        model_size:   str  = "small",
        models_dir:   Path = Path("backend/models"),
        device:       str  = "cpu",
        compute_type: str  = "int8",    # int8 = fastest on CPU, good accuracy
    ):
        self.model_size   = model_size
        self.models_dir   = models_dir
        self.device       = device
        self.compute_type = compute_type
        self._model       = None

    def load(self):
        """Download (first run) and load the Whisper model into memory."""
        from faster_whisper import WhisperModel

        logger.info(
            f"Loading Whisper '{self.model_size}' "
            f"[device={self.device}, compute={self.compute_type}] …"
        )
        t0 = time.time()

        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=str(self.models_dir),
        )
        logger.info(f"Whisper loaded in {time.time() - t0:.1f}s")

    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str] = None,   # None = auto-detect
    ) -> Optional[TranscriptResult]:
        """
        Transcribe a float32 PCM chunk (16 kHz mono).

        Returns None if the chunk is silence / unintelligible.
        """
        if self._model is None:
            raise RuntimeError("Call load() before transcribe()")

        if len(audio) < 1600:   # < 100ms — too short
            return None

        t0 = time.time()

        # Constrain language detection to our supported set
        lang_hint = language if language in SUPPORTED_LANGUAGES else None

        segments, info = self._model.transcribe(
            audio,
            language=lang_hint,
            beam_size=5,
            best_of=5,
            temperature=0.0,
            vad_filter=False,       # we do our own VAD upstream
            word_timestamps=False,
            condition_on_previous_text=False,  # FIXED: was True, caused repetition loops
            suppress_blank=True,
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            repetition_penalty=1.3,            # penalise repeated tokens
        )

        # Collect all segments
        results  = list(segments)
        elapsed  = time.time() - t0

        if not results:
            logger.debug("Whisper: no speech detected in chunk")
            return None

        # Concatenate text, compute mean confidence
        text_parts   = []
        confidences  = []
        for seg in results:
            text_parts.append(seg.text.strip())
            confidence = min(1.0, max(0.0, np.exp(seg.avg_logprob)))
            confidences.append(confidence)

        text = " ".join(t for t in text_parts if t)
        if not text:
            return None

        # Drop hallucination loops
        if _is_repetition_loop(text):
            logger.warning(f"Whisper: repetition loop detected, discarding: {text[:50]!r}")
            return None

        detected_lang = info.language if hasattr(info, "language") else "en"
        avg_conf      = float(np.mean(confidences)) if confidences else 0.0

        logger.info(
            f"[{LANGUAGE_NAMES.get(detected_lang, detected_lang)}] "
            f"{text!r}  (conf={avg_conf:.2f}, took={elapsed:.2f}s)"
        )

        return TranscriptResult(
            text=text,
            language=detected_lang,
            confidence=avg_conf,
            duration_s=elapsed,
        )
