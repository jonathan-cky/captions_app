"""
main.py — Captioneer backend entry point.

Pipeline:
    AudioCapture (Swift) → VADChunker → Transcriber (Whisper) → CaptionServer (WS)

Run with:
    python main.py [--model small|medium|large-v3]
"""

import asyncio
import logging
import argparse
import signal
import sys
import threading
import queue
from pathlib import Path

# ── Local imports ─────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from audio_capture import AudioCapture
from vad            import VADChunker
from transcriber    import Transcriber
from ws_server      import CaptionServer

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("captioneer")

MODELS_DIR = Path(__file__).parent / "models"

# Maximum number of VAD chunks waiting to be transcribed.
# If Whisper falls behind, older chunks are dropped to stay real-time.
MAX_QUEUE_DEPTH = 2


# ── Pipeline thread (synchronous audio → VAD → Whisper) ──────────────────────

def run_pipeline(
    capture:     AudioCapture,
    chunker:     VADChunker,
    transcriber: Transcriber,
    server:      CaptionServer,
    stop_event:  threading.Event,
):
    """
    Runs in a background thread so it doesn't block the asyncio event loop.
    Uses a bounded queue to drop stale chunks and stay real-time.
    """
    logger.info("Pipeline thread started")

    chunk_queue = queue.Queue(maxsize=MAX_QUEUE_DEPTH)

    # Producer: VAD feeds chunks into the queue, dropping oldest if full
    def producer():
        for raw_samples in capture.stream():
            if stop_event.is_set():
                break
            server.broadcast_status("listening")
            for chunk in chunker.feed(raw_samples):
                if stop_event.is_set():
                    break
                # Drop oldest chunk if queue is full (stay real-time)
                if chunk_queue.full():
                    try:
                        chunk_queue.get_nowait()
                        logger.warning("audio_capture: dropping stale chunk to stay real-time")
                    except queue.Empty:
                        pass
                try:
                    chunk_queue.put_nowait(chunk)
                except queue.Full:
                    pass
        chunk_queue.put(None)  # sentinel

    producer_thread = threading.Thread(target=producer, daemon=True, name="vad-producer")
    producer_thread.start()

    # Consumer: Whisper transcribes chunks from the queue
    while True:
        try:
            chunk = chunk_queue.get(timeout=1.0)
        except queue.Empty:
            if stop_event.is_set():
                break
            continue

        if chunk is None:
            break

        server.broadcast_status("processing")

        result = transcriber.transcribe(chunk)

        if result and result.text.strip():
            server.broadcast_caption(
                text=result.text,
                language=result.language,
                confidence=result.confidence,
            )

    # Flush any remaining buffered audio on shutdown
    final_chunk = chunker.flush()
    if final_chunk is not None:
        result = transcriber.transcribe(final_chunk)
        if result and result.text.strip():
            server.broadcast_caption(result.text, result.language, result.confidence)

    server.broadcast_status("idle")
    logger.info("Pipeline thread exited")


# ── Async main ────────────────────────────────────────────────────────────────

async def main(model_size: str):
    server      = CaptionServer()
    stop_event  = threading.Event()

    # Start WebSocket server first so Electron can connect immediately
    await server.start()

    # Load Whisper model (blocking — done before pipeline starts)
    transcriber = Transcriber(model_size=model_size, models_dir=MODELS_DIR)
    transcriber.load()

    # Init VAD chunker
    chunker = VADChunker(models_dir=MODELS_DIR)

    # Init audio capture
    capture = AudioCapture()
    capture.start()

    # Graceful shutdown on Ctrl-C / SIGTERM
    loop = asyncio.get_running_loop()

    def _shutdown():
        logger.info("Shutdown signal received …")
        stop_event.set()
        capture.stop()
        loop.call_soon_threadsafe(loop.stop)

    loop.add_signal_handler(signal.SIGINT,  _shutdown)
    loop.add_signal_handler(signal.SIGTERM, _shutdown)

    # Run the synchronous pipeline in a thread pool
    pipeline_thread = threading.Thread(
        target=run_pipeline,
        args=(capture, chunker, transcriber, server, stop_event),
        daemon=True,
        name="pipeline",
    )
    pipeline_thread.start()

    logger.info("Captioneer is running. Press Ctrl-C to stop.")

    # Keep asyncio loop alive (WebSocket server runs here)
    try:
        await asyncio.Event().wait()   # wait forever
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()
        pipeline_thread.join(timeout=5)
        logger.info("Captioneer shut down cleanly.")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Captioneer — live caption backend")
    parser.add_argument(
        "--model",
        default="small",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Whisper model size (default: small)",
    )
    args = parser.parse_args()

    logger.info(f"Starting Captioneer with Whisper '{args.model}' model")
    asyncio.run(main(args.model))
