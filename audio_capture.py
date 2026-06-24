"""
audio_capture.py — Launches the Swift AudioTap binary and streams
                   float32 PCM samples into Python as numpy arrays.

The Swift process writes raw f32-LE 16 kHz mono samples to stdout.
We read that pipe in chunks and yield numpy arrays to the VAD module.
"""

import subprocess
import numpy as np
import logging
import sys
import os
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)

SAMPLE_RATE  = 16000
DTYPE        = np.float32
# Read ~100ms of audio per iteration (keeps latency low)
READ_FRAMES  = 1600                              # samples
READ_BYTES   = READ_FRAMES * 4                   # float32 = 4 bytes


def find_audio_tap_binary() -> Path:
    """
    Locate the compiled AudioTap binary.
    Expected location: <project_root>/audio-tap/AudioTap  (compiled)
    Falls back to running via 'swift' directly for development.
    """
    here        = Path(__file__).parent   # project root
    binary_path = here / "audio-tap" / "AudioTap"
    if binary_path.exists():
        return binary_path

    # Development fallback: compile on the fly with swiftc
    swift_src = here / "audio-tap" / "AudioTap.swift"
    if swift_src.exists():
        logger.info("AudioTap binary not found — compiling from source …")
        out = binary_path
        result = subprocess.run(
            [
                "swiftc",
                str(swift_src),
                "-o", str(out),
                "-framework", "ScreenCaptureKit",
                "-framework", "AVFoundation",
                "-framework", "CoreAudio",
                "-framework", "CoreMedia",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to compile AudioTap.swift:\n{result.stderr}"
            )
        logger.info(f"Compiled AudioTap → {out}")
        return out

    raise FileNotFoundError(
        "Neither AudioTap binary nor AudioTap.swift found. "
        "Run: swiftc audio-tap/AudioTap.swift -o audio-tap/AudioTap "
        "-framework ScreenCaptureKit -framework AVFoundation "
        "-framework CoreAudio -framework CoreMedia"
    )


class AudioCapture:
    """
    Manages the AudioTap subprocess and yields float32 PCM chunks.

    Usage:
        cap = AudioCapture()
        for samples in cap.stream():
            vad_chunker.feed(samples)
    """

    def __init__(self):
        self._proc: subprocess.Popen | None = None

    def start(self):
        binary = find_audio_tap_binary()
        logger.info(f"Starting AudioTap: {binary}")

        self._proc = subprocess.Popen(
            [str(binary)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,   # Swift logs go to stderr, we relay them
            bufsize=0,                # unbuffered — critical for low latency
        )
        logger.info(f"AudioTap PID: {self._proc.pid}")

        # Relay Swift stderr to Python logger in a thread
        import threading
        def relay_stderr():
            for line in self._proc.stderr:
                logger.debug(f"[AudioTap] {line.decode().rstrip()}")
        threading.Thread(target=relay_stderr, daemon=True).start()

    def stop(self):
        if self._proc:
            logger.info("Stopping AudioTap …")
            self._proc.terminate()
            self._proc.wait()
            self._proc = None

    def stream(self) -> Generator[np.ndarray, None, None]:
        """
        Yields numpy float32 arrays of READ_FRAMES samples each (~100ms).
        Blocks until the subprocess ends or an error occurs.
        """
        if self._proc is None:
            raise RuntimeError("Call start() before stream()")

        stdout = self._proc.stdout
        while True:
            raw = stdout.read(READ_BYTES)
            if not raw:
                logger.warning("AudioTap stdout closed — capture ended")
                break
            if len(raw) < READ_BYTES:
                # Partial read at shutdown — pad with zeros
                raw = raw + b"\x00" * (READ_BYTES - len(raw))

            samples = np.frombuffer(raw, dtype=DTYPE).copy()
            yield samples

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()
