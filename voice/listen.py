#!/usr/bin/env python3
"""
listen.py — Microphone input + Whisper speech-to-text for Robot Ross.

Records until silence is detected, then transcribes with Whisper.
Model is downloaded on first use (~145MB for 'base').

Usage (standalone test):
    python listen.py                     # record and print transcript
    python listen.py --model small       # better accuracy, slower
    python listen.py --threshold 0.03    # raise threshold for noisy rooms
    python listen.py --lang es           # force Spanish

As a module:
    from listen import WhisperListener
    listener = WhisperListener(model="base")
    text = listener.listen()
    print(text)
"""

import sys, os, argparse
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SAMPLE_RATE       = 16000   # Whisper expects 16kHz
CHUNK_S           = 0.4     # seconds per audio chunk for VAD
SILENCE_S         = 0.8     # stop after this many seconds of silence
MIN_SPEECH_S      = 0.5     # ignore clips shorter than this (avoid false triggers)
MAX_RECORD_S      = 30      # hard cap on recording length
DEFAULT_THRESHOLD = 0.015   # RMS energy threshold for speech vs silence
DEFAULT_MODEL     = "base"


class WhisperListener:
    def __init__(self, model_size=DEFAULT_MODEL, language=None, threshold=DEFAULT_THRESHOLD):
        self.language  = language
        self.threshold = threshold
        self._model    = None
        self._model_size = model_size

    def _load(self):
        if self._model is None:
            import whisper
            print(f"  [Whisper] Loading model '{self._model_size}' "
                  f"(downloads ~145MB on first run)...", flush=True)
            self._model = whisper.load_model(self._model_size)
            print(f"  [Whisper] Model ready.", flush=True)
        return self._model

    def listen(self, prompt="  🎤 Listening...", max_seconds=None):
        """
        Record from microphone until silence, return transcript string.
        Returns "" if nothing was captured or timeout reached.
        max_seconds: override MAX_RECORD_S for this call (e.g. short confirmations).
        """
        import sounddevice as sd

        model = self._load()

        chunk_samples     = int(SAMPLE_RATE * CHUNK_S)
        silence_needed    = int(SILENCE_S / CHUNK_S)
        min_speech_chunks = int(MIN_SPEECH_S / CHUNK_S)
        max_chunks        = int((max_seconds or MAX_RECORD_S) / CHUNK_S)

        if prompt:
            print(prompt, flush=True)

        audio_chunks    = []
        silence_count   = 0
        speech_started  = False

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
            for _ in range(max_chunks):
                chunk, _ = stream.read(chunk_samples)
                chunk = chunk.flatten()
                rms = float(np.sqrt(np.mean(chunk ** 2)))

                if rms >= self.threshold:
                    if not speech_started:
                        print("  🔴 Recording...", flush=True)
                    speech_started = True
                    silence_count  = 0
                    audio_chunks.append(chunk)
                elif speech_started:
                    audio_chunks.append(chunk)   # include trailing silence
                    silence_count += 1
                    if silence_count >= silence_needed:
                        break

        if not speech_started or len(audio_chunks) < min_speech_chunks:
            return ""

        audio  = np.concatenate(audio_chunks)
        kwargs = {"fp16": False, "language": self.language} if self.language else {"fp16": False}
        result = model.transcribe(audio, **kwargs)
        text   = result["text"].strip()
        print(f"  💬 Heard: {text}", flush=True)
        return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Robot Ross — Whisper speech-to-text test")
    parser.add_argument("--model",     default=DEFAULT_MODEL, help="Whisper model size (tiny/base/small/medium)")
    parser.add_argument("--lang",      default=None,          help="Force language (e.g. en, es, fr)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"RMS silence threshold (default {DEFAULT_THRESHOLD}; raise for noisy rooms)")
    args = parser.parse_args()

    listener = WhisperListener(model_size=args.model, language=args.lang, threshold=args.threshold)
    print("Speak something (Ctrl+C to quit)...")
    while True:
        try:
            text = listener.listen()
            if text:
                print(f"Transcript: {text}\n")
        except KeyboardInterrupt:
            print("\nDone.")
            break
