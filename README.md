# Robot Ross Skill

> A Huenit robot arm that draws on paper while narrating in the warm, peaceful style of Bob Ross.

---

## What Is This?

**Robot Ross** is an OpenClaw skill that controls a Huenit robot arm to draw/write on paper, narrate the process in Bob Ross style via a local AI model (Apertus 8B), and optionally engage visitors in live voice conversation.

Three modes:
- **Headless** — `bob_ross.py` is called directly (Telegram, scripts, order automation)
- **Voice showcase** — `chat_ross.py` listens via microphone, chats, and draws on request
- **Pyrography** — `--pyro` flag slows the feed rate for wood burning

---

## Quick Start

### Prerequisites

```bash
# Core (all platforms)
pip install pyserial anthropic

# Voice showcase — STT
pip install openai-whisper sounddevice

# Voice showcase — Kokoro TTS (local neural voice)
pip install kokoro soundfile "numpy<2.0" espeakng-loader

# Voice showcase — Voxtral TTS (Mistral cloud voice, needs MISTRAL_API_KEY)
pip install mistralai

# Image tracing (optional)
pip install vtracer pillow pillow-heif
```

Ollama must be running with Apertus loaded:
```bash
ollama pull MichelRosselli/apertus:8b-instruct-2509-q4_k_m
```

### API keys

Keys are loaded from [Infisical](https://infisical.com/) vault if available, otherwise from environment variables:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # for AI sketch generation + Haiku conversation
export MISTRAL_API_KEY=...            # for Voxtral TTS (optional)
```

### Serial port

```bash
# Mac (default)
export HUENIT_PORT=/dev/cu.usbserial-310

# Windows
set HUENIT_PORT=COM3
```

---

## Calibrate

```bash
python bob_ross.py calibrate
```

Press **Q** at any calibration step to abort and save Z height only.

---

## Headless drawing (`bob_ross.py`)

```bash
# Write text
python bob_ross.py write "Hello World"

# Draw a shape
python bob_ross.py draw circle 40

# Draw an SVG file
python bob_ross.py svg path/to/file.svg --size 80

# AI-generated sketch
python bob_ross.py sketch "a lighthouse on a rocky coast" --size 80

# Pyrography (wood burning)
python bob_ross.py svg logo.svg --pyro --size 60

# Test narration without moving the arm
python bob_ross.py sketch "a happy little tree" --dry-run

# System check
python bob_ross.py check
```

Key flags:

| Flag | Description |
|---|---|
| `--size 80` | Drawing size in mm (default 80) |
| `--feed 400` | Feed rate mm/min |
| `--buyer "Alice"` | Personalise narration with visitor name |
| `--pyro` | Pyrography mode — slow feed for wood burning |
| `--engine kokoro\|voxtral\|system` | TTS engine (default: kokoro) |
| `--no-voice` | Skip all narration |
| `--dry-run` | Narrate without moving the arm |
| `--direct` | Skip preview, draw immediately |

---

## Voice showcase (`chat_ross.py`)

A full voice conversation loop: visitor speaks → Whisper transcribes → brain (Haiku or Apertus) decides → Voxtral/Kokoro speaks → arm draws.

```bash
# Mac
python chat_ross.py --brain apertus --engine voxtral --lang en

# Windows
set HUENIT_PORT=COM3 && python chat_ross.py --brain apertus --engine voxtral --no-obs --lang en

# Test without arm movement
python chat_ross.py --no-arm --brain apertus --engine voxtral
```

Key flags:

| Flag | Description |
|---|---|
| `--brain haiku\|apertus\|mistral` | Conversation LLM (default: haiku) |
| `--engine kokoro\|voxtral\|system` | TTS engine (default: voxtral) |
| `--lang en` | Force STT language |
| `--whisper base` | Whisper model size (tiny/base/small/medium) |
| `--threshold 0.03` | Mic RMS threshold (raise in noisy rooms) |
| `--size 80` | Drawing size mm |
| `--no-arm` | Conversation + narration only, no arm movement |
| `--no-obs` | Skip OBS recording |

Press **Q** to quit gracefully, or say "goodbye" / "bye".

**Conversation flow:**
1. Robot Ross greets the visitor
2. Visitor describes what to draw
3. Robot Ross repeats the request and asks for confirmation ("Shall I go ahead?")
4. On "yes" → calibrates (first draw only) → draws while narrating
5. Conversation TTS mutes while arm is narrating — no audio overlap

---

## Pre-made SVGs

Drop any `.svg` file in the repo root. `chat_ross.py` discovers them automatically and tells the brain about them so it can recommend them for instant draws (no AI generation wait).

---

## Pyrography mode

`--pyro` reduces the feed rate to ~45 mm/min for slow, controlled burns on wood. Works with any drawing command:

```bash
python bob_ross.py svg design.svg --pyro --size 60
python chat_ross.py --pyro --engine voxtral
```

---

## AI stack

| Role | Model | Runtime |
|---|---|---|
| Showcase conversation | Claude Haiku 4.5 | Anthropic API |
| Showcase conversation (local) | Apertus 8B | Ollama local |
| Bob Ross narration | Apertus 8B | Ollama local |
| AI sketch / SVG generation | Claude Haiku 4.5 | Anthropic API |
| Speech-to-text | Whisper base | Local |
| TTS (showcase) | Voxtral mini | Mistral API |
| TTS (narration) | Kokoro 82M | Local |
| TTS (fallback) | System (PowerShell/say/espeak) | Built-in |

*[Apertus](https://huggingface.co/MichelRosselli/apertus) is developed by MichelRosselli as part of an Innosuisse-funded Swiss AI project.*

---

## File structure

```
bobrossskill/
├── bob_ross.py             # Headless orchestrator (write/draw/svg/sketch/pyro)
├── chat_ross.py            # Voice showcase loop (STT → brain → TTS → draw)
├── ARCHITECTURE.md         # Full architecture diagram
├── huenit/
│   ├── huenit_svg.py       # SVG → G-code (Bezier-aware, path sort)
│   ├── huenit_write.py     # Text calligraphy (auto-scale, multi-line)
│   ├── huenit_draw.py      # Shapes + interactive calibration
│   ├── huenit_jog_control.py  # Manual jog utility
│   ├── huenit_wave.py      # Greeting gesture
│   └── calibration.json    # Saved Z/tilt calibration (auto-generated)
└── voice/
    ├── speak.py            # TTS: Kokoro / Voxtral / system fallback
    └── listen.py           # STT: Whisper with VAD silence detection
```
