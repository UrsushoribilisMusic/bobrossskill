# Robot Ross — Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           INPUT MODES                                   │
│                                                                         │
│   🎤 Voice Showcase          📟 Headless / Telegram / Scripts           │
│   chat_ross.py               bob_ross.py <action> <content> [flags]    │
│        │                              │                                 │
└────────┼──────────────────────────────┼─────────────────────────────────┘
         │                              │
         ▼                              │
┌────────────────────┐                  │
│   listen.py        │                  │
│   Whisper STT      │                  │
│   VAD 16kHz        │                  │
│   --lang en        │                  │
└────────┬───────────┘                  │
         │ transcript                   │
         ▼                              │
┌────────────────────────┐              │
│   Conversation Brain   │              │
│   (--brain flag)       │              │
│                        │              │
│   haiku   → Haiku 4.5  │              │
│   apertus → Ollama     │              │
│   mistral → Ollama     │              │
│                        │              │
│   JSON: {action,       │              │
│          subject/file, │              │
│          text}         │              │
└────────┬───────────────┘              │
         │                              │
         ▼                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         bob_ross.py — Orchestrator                      │
│                                                                         │
│  Actions:  write | draw | svg | sketch | check | calibrate             │
│  Flags:    --size --feed --buyer --pyro --engine --dry-run --direct    │
│                                                                         │
│  1. Readiness check (port + calibration.json + session flag + Ollama)  │
│  2. Job lock (PID-based, stale detection)                              │
│  3. SVG generation (sketch only) ────────────► Claude Haiku 4.5       │
│  4. Narration generation ─────────────────────► Apertus 8B (Ollama)   │
│  5. Warning tone (3 beeps)                                             │
│  6. speak(intro) ─────────────────────────────► speak.py              │
│  7. run_draw() + commentary thread ────────────► huenit scripts        │
│     [or sleep 20s if --dry-run]                                        │
│  8. speak(outro)                                                       │
│  9. Release lock                                                       │
└──────┬────────────────────────┬────────────────────────────────────────┘
       │                        │
       ▼                        ▼
┌─────────────────┐   ┌────────────────────────────────────────────────┐
│   speak.py      │   │  Drawing pipeline (huenit/)                    │
│   TTS Service   │   │                                                │
│                 │   │  write   → huenit_write.py                     │
│  --engine:      │   │            text calligraphy, auto-scale        │
│   kokoro  ──►  │   │            multi-line wrap                     │
│    Kokoro 82M   │   │                                                │
│    WAV→speaker  │   │  draw    → huenit_draw.py                      │
│                 │   │            shapes: circle, square, triangle    │
│   voxtral ──►  │   │            interactive calibration             │
│    Mistral API  │   │            Q=quit at any calibration step      │
│    WAV→speaker  │   │                                                │
│                 │   │  svg     → huenit_svg.py  ◄── ALL paths        │
│   system  ──►  │   │            Bezier: C Q S T arcs                │
│    PowerShell   │   │            CURVE_STEPS = 20                    │
│    say / espeak │   │            greedy path-sort by proximity       │
└─────────────────┘   │            y-offset for drawings >80mm        │
                       │            G-code stream over serial          │
                       │                                                │
                       │  sketch  → quickdraw_compose.py               │
                       │            Apertus 8B parses prompt           │
                       │            Google Quick Draw API (345 cats)   │
                       │            → SVG → huenit_svg.py              │
                       └───────────────────┬────────────────────────────┘
                                           │
                                    pyserial
                                           │
                                           ▼
                               ┌──────────────────────┐
                               │   HUENIT ROBOT ARM   │
                               │   HUENIT_PORT env    │
                               │   (COM3 / usbserial) │
                               │                      │
                               │   G-code dialect:    │
                               │     G21  mm units    │
                               │     G91  relative    │
                               │     G1 X Y F<feed>   │
                               │     M3/M5 pen dn/up  │
                               │     M400  wait idle  │
                               │                      │
                               │   Pyrography mode:   │
                               │     --pyro           │
                               │     feed ~45 mm/min  │
                               │     wood burning     │
                               └──────────────────────┘
```

## Voice showcase detail (`chat_ross.py`)

```
Visitor speaks
     │
     ▼
listen.py — Whisper VAD
  SAMPLE_RATE  = 16000 Hz
  SILENCE_S    = 0.8s   (stop after 0.8s silence)
  MAX_RECORD_S = 30s    (hard cap; 8s for confirmations)
  threshold    = 0.015 RMS (tunable via --threshold)
     │
     ▼
Conversation brain (--brain flag)
  haiku   → Claude Haiku 4.5 via Anthropic API
  apertus → Apertus 8B via Ollama :11434
  mistral → mistral:latest via Ollama :11434

  Response JSON:
    {"action": "speak",  "text": "..."}
    {"action": "sketch", "subject": "...", "text": "..."}
    {"action": "svg",    "file": "bear.svg", "text": "..."}
    {"action": "ask_name", "text": "..."}
     │
     ├─ speak  → speak.py (Voxtral/Kokoro)
     │
     ├─ sketch/svg → CONFIRMATION STEP
     │     speak "Shall I draw X?"
     │     listen 8s for yes/no
     │     yes → start_draw() in background thread
     │         → bob_ross.py sketch --direct --engine <e> [--calibrate on 1st draw]
     │         → chat_ross MUTES its own TTS while bob_ross narrates
     │
     └─ ask_name → listen → store buyer name → personalise narration

Audio rule: while is_drawing() → chat_ross speak() is silenced (force=True to override)
Quit:       press Q (msvcrt non-blocking, does not steal stdin from calibration)
            or say "goodbye" / "bye" / "exit"
```

## Technology stack

| Layer | Technology |
|---|---|
| Robot control | pyserial → G-code → Huenit arm |
| SVG rendering | huenit_svg.py (Bezier C/Q/S/T, greedy path sort) |
| AI sketch | Claude Haiku 4.5 → SVG (Anthropic API) |
| Narration | Apertus 8B via Ollama (local, Swiss model) |
| Conversation | Claude Haiku 4.5 or Apertus 8B or Mistral |
| STT | openai-whisper (local, base model) |
| TTS primary | Kokoro 82M (local neural, `am_michael` voice) |
| TTS cloud | Voxtral mini (Mistral API, `fr_marie_neutral` voice) |
| TTS fallback | PowerShell SAPI (Windows) / say (macOS) / espeak (Linux) |
| Secrets | Infisical vault → env var fallback |
