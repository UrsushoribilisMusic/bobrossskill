# Voxtral Integration — Robot Ross

> Context document for Mistral partnership discussion · April 2026

---

## The Project

**Robot Ross** is a live showcase robot arm that draws on paper while having a voice conversation with visitors. It runs at events and exhibitions — no reliable internet, no keyboard, no screen. The entire interaction is voice-only:

1. Visitor walks up and speaks to the robot
2. Robot listens, understands, responds in Bob Ross style
3. Visitor confirms what they want drawn
4. Robot narrates while the arm draws in real time

The full pipeline runs on a Windows laptop (showcase) or Mac Mini (studio):

```
Visitor speaks
    → STT (transcribe)
    → Brain (Apertus 8B / Claude Haiku — decides what to do)
    → TTS (speak response)
    → Robot arm draws
    → TTS (narrates while drawing)
```

---

## Current Voxtral Usage

We are already using Voxtral for TTS in the showcase loop:

```python
# speak.py — voice/speak.py in the repo
VOXTRAL_MODEL = "voxtral-mini-tts-latest"
VOXTRAL_VOICE = "fr_marie_neutral"

client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
response = client.audio.speech.complete(
    model=VOXTRAL_MODEL,
    input=text,
    voice_id=VOXTRAL_VOICE,
    response_format="wav",
)
```

The audio path today:

```
chat_ross.py  →  speak.py --engine voxtral  →  Mistral API  →  WAV  →  speaker
chat_ross.py  →  listen.py                  →  Whisper (local, openai-whisper)
bob_ross.py   →  speak.py --engine kokoro   →  Kokoro 82M (local)  →  speaker
```

**Voxtral is used for the conversation voice** (warm, natural).
**Kokoro is used for the drawing narration** (local fallback, different character).
**Whisper handles STT** — currently local via `openai-whisper`.

---

## Why We Want to Go Fully Local with Voxtral

### 1. Showcase environments have no reliable internet

Events, trade shows, university labs — we cannot depend on API availability. A failed API call mid-conversation kills the demo. We need everything to run offline.

### 2. Latency matters for live conversation

The showcase loop is:

```
speak question → 0.8s pause → listen (VAD) → transcribe → brain → TTS response → arm starts
```

API round-trips for TTS add 1–3s per utterance. For a live robot conversation this is noticeable. Local Voxtral would bring TTS latency to the same level as Kokoro (~0.5s).

### 3. Single model vendor simplifies the audio stack

Today we run three separate audio components:
- Whisper (STT, local, ~145MB)
- Voxtral (TTS conversation, cloud)
- Kokoro (TTS narration, local, ~300MB)

If Voxtral ran locally and supported both STT and TTS, we could consolidate to one model vendor, one loading pattern, one API style — and potentially one preloaded model serving both directions.

### 4. Model swapping overhead is a real problem

The brain (Apertus 8B, ~5.1GB) runs on Ollama. When we also run Mistral (4.4GB) locally for conversation, Ollama swaps models on every call — adding 10–20s of latency. We need the audio models to be **separate from the LLM runtime**, preloaded and resident in memory for the duration of the showcase session.

---

## What We Are Asking For

### TTS (already works via API — need local)

| Need | Detail |
|---|---|
| Local inference | Run `voxtral-mini-tts` on CPU or GPU without API calls |
| Voice preloading | Load `fr_marie_neutral` (or equivalent) once at startup, keep resident |
| Low first-token latency | Target <500ms for short phrases (1–2 sentences) |
| WAV output | We play directly via `winsound` / `afplay` — no streaming required |
| Python SDK | `pip install mistralai` should work locally, or a lightweight local wrapper |

### STT (currently using Whisper — want to migrate to Voxtral)

| Need | Detail |
|---|---|
| Local inference | Run Voxtral STT on-device, no API |
| VAD-friendly | We do our own silence detection (RMS threshold), pass a short audio buffer |
| Short utterance optimised | Commands are 1–10 words; "yes", "draw a bear", "goodbye" |
| Language forcing | We pass `--lang en` to Whisper today; need equivalent |
| Python interface | `transcribe(audio_array, language="en") → str` |
| Latency | Target <1s for a 2–3s audio clip on a modern laptop CPU |

### Nice to have

- **Streaming TTS**: for longer narration phrases during drawing, streaming playback would reduce perceived latency
- **Voice cloning / custom voice**: the "Robot Ross" character could benefit from a consistent branded voice across TTS roles
- **Batch preload**: ability to pre-render the 5 commentary phrases at session start, play cached WAV during drawing

---

## Repo

The full open-source skill is at:
**https://github.com/UrsushoribilisMusic/bobrossskill**

Relevant files:
- `voice/speak.py` — TTS wrapper (Voxtral + Kokoro + system fallback)
- `voice/listen.py` — STT wrapper (Whisper, VAD-based)
- `chat_ross.py` — main showcase loop
- `ARCHITECTURE.md` — full system diagram

---

## Current Pain Points Summary

| Pain point | Root cause | Voxtral local would fix |
|---|---|---|
| Demo fails without internet | Voxtral TTS is cloud-only | ✅ |
| 1–3s TTS latency in conversation | API round-trip | ✅ |
| Two separate audio libraries (Whisper + Kokoro + Voxtral) | No single local solution | ✅ if STT also local |
| Voice inconsistency (Kokoro narrates, Voxtral converses) | Different engines for different roles | ✅ unified voice |
| Cold start on first TTS call | No preload API | ✅ with preload support |
