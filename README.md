# Bob Ross Skill — OpenClaw

An OpenClaw skill that controls a Huenit robot arm to draw and write on paper, while narrating poetically in the style of Bob Ross.

## How it works

1. **Safety warning** — 3 beeps so humans step clear
2. **Intro** — Qwen (local LLM via Ollama) generates a Bob Ross-style intro: *"We got a lovely request for..."*
3. **Draw + live commentary** — the arm draws while the Evan voice narrates in the background
4. **Outro** — *"Finished, you can remove your piece now"*

## Skills

| Skill | Description |
|-------|-------------|
| `bob-ross` | Main orchestrator — handles narration, safety, and coordinates the arm |
| `huenit` | Low-level robot arm control (calibration, jogging) |
| `voice` | macOS TTS via the Evan voice (`say` command) |

## Requirements

- macOS (uses `say` for TTS and `afplay` for warning beeps)
- [Huenit robot arm](https://huenit.com) connected via USB serial
- [Ollama](https://ollama.ai) running locally with `qwen2.5:7b`
- [OpenClaw](https://openclaw.ai) installed

## Usage

```bash
# Full Bob Ross experience
python3 bob-ross/bob_ross.py write "OpenClaw"
python3 bob-ross/bob_ross.py draw square

# Silent test (no voice)
python3 bob-ross/bob_ross.py write "Hi" --no-voice

# Readiness check
python3 bob-ross/bob_ross.py check

# Calibrate pen height
python3 bob-ross/bob_ross.py calibrate
```

## Via Telegram (OpenClaw)

Once installed in your OpenClaw workspace, just message your bot:

> *"Bob Ross write OpenClaw"*
> *"Draw a circle Bob Ross style"*
> *"Stop the arm"*

## Logs

Events are logged with timestamps to `bob-ross/bob_ross.log`.

## Setup

1. Copy the three skill folders into `~/.openclaw/workspace/skills/`
2. Start Ollama: `ollama serve` (or set up as a launchd service)
3. Pull the model: `ollama pull qwen2.5:7b`
4. Calibrate: `python3 bob-ross/bob_ross.py calibrate`
5. Restart OpenClaw to pick up the new skills
