---
name: voice
description: Speak text aloud using macOS text-to-speech with the Evan voice.
metadata: {"openclaw": {"emoji": "🔊", "requires": {"os": ["darwin"]}}}
---

# Voice

Speak text aloud on macOS using the built-in `say` command via `speak.py`.

## Usage

```bash
python3 speak.py "Your text here"
```

## Notes

- Uses the macOS **Evan** voice at rate 160.
- If no argument is provided, speaks a default message.
- Requires macOS (uses the built-in `say` command).
