#!/usr/bin/env python3
"""
chat_ross.py — Robot Ross interactive showcase.

Robot Ross greets visitors, listens to what they want, and draws on request.
Uses Whisper (STT) → Claude Haiku (brain) → Kokoro/Voxtral (TTS) → bob_ross sketch (arm).

Usage:
    python chat_ross.py                        # full showcase, pen drawing
    python chat_ross.py --no-obs               # skip OBS
    python chat_ross.py --pyro                 # pyrography mode
    python chat_ross.py --size 80 --feed 400   # drawing parameters
    python chat_ross.py --whisper small        # better STT accuracy
    python chat_ross.py --threshold 0.03       # raise mic threshold (noisy room)
    python chat_ross.py --no-arm               # conversation only, no drawing (test mode)
"""

import sys, os, argparse, subprocess, threading, json, re, time, urllib.request

# ── Infisical vault (loads ANTHROPIC_API_KEY, MISTRAL_API_KEY into env) ───────
try:
    _vault_path = os.path.join(os.path.expanduser("~"), "agentic-fleet-hub", "vault")
    sys.path.insert(0, _vault_path)
    from vault import load_secrets as _vault_load
    _vault_load(["ANTHROPIC_API_KEY", "MISTRAL_API_KEY"])
except Exception:
    pass  # fall back to env vars already set (e.g. launch_local.bat)

import anthropic

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR     = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR      = os.path.join(SCRIPT_DIR, "voice")
BOB_ROSS_PY    = os.path.join(SCRIPT_DIR, "bob_ross.py")
SVG_DIR        = SCRIPT_DIR   # SVG files live in repo root

sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, VOICE_DIR)

# ── Pre-made SVG catalogue ─────────────────────────────────────────────────────
# Shown to Claude so it can recommend them for fast draws (no AI generation wait)
def _discover_svgs():
    svgs = {}
    for f in os.listdir(SVG_DIR):
        if f.lower().endswith(".svg"):
            name = os.path.splitext(f)[0].lower()
            svgs[name] = f
    return svgs   # {"bear": "bear.svg", "casacucu": "CasaCucu.svg", ...}

# ── Order log helpers ──────────────────────────────────────────────────────────
def _get_recent_drawings(n=5):
    try:
        import order_log
        wb = order_log._load()
        ws = wb.active
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            order_type = str(row[2] or "").strip()
            content    = str(row[3] or "").strip()
            if not content:
                continue
            # Strip file extensions so filenames don't look like draw commands
            content = os.path.splitext(content)[0].replace("_", " ")
            rows.append(content)
        return rows[-n:]
    except Exception:
        return []

# ── Quit flag (set by keyboard watcher thread) ────────────────────────────────
_quit_flag = threading.Event()

def _keyboard_watcher():
    """Background thread: press Q to quit (no Enter needed, never blocks stdin)."""
    try:
        import msvcrt
        while not _quit_flag.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch().lower()
                if ch == 'q':
                    _quit_flag.set()
                    break
            time.sleep(0.05)
    except ImportError:
        # Non-Windows fallback — just skip the watcher (use Ctrl+C instead)
        pass

# ── Drawing state ──────────────────────────────────────────────────────────────
_draw_lock        = threading.Lock()
_draw_thread      = None
_draw_subject     = None
_first_draw_done  = False   # calibrate on first draw of the session

def is_drawing():
    with _draw_lock:
        return _draw_thread is not None and _draw_thread.is_alive()

def current_subject():
    with _draw_lock:
        return _draw_subject if (_draw_thread and _draw_thread.is_alive()) else None

def start_draw(subject, buyer=None, size=80, feed=None, pyro=False, no_obs=True, no_arm=False, engine="kokoro"):
    global _draw_thread, _draw_subject, _first_draw_done

    cmd = [sys.executable, BOB_ROSS_PY, "sketch", subject, "--direct",
           "--engine", engine]
    if buyer:
        cmd += ["--buyer", buyer]
    if no_obs:
        cmd.append("--no-obs")
    if pyro:
        cmd.append("--pyro")
    cmd += ["--size", str(size)]
    if feed:
        cmd += ["--feed", str(feed)]
    if no_arm:
        cmd.append("--dry-run")
    if not _first_draw_done and not no_arm:
        cmd.append("--calibrate")

    def _run():
        global _draw_subject, _first_draw_done
        subprocess.run(cmd)
        _first_draw_done = True
        with _draw_lock:
            _draw_subject = None

    with _draw_lock:
        _draw_subject = subject
        _draw_thread  = threading.Thread(target=_run, daemon=True)
        _draw_thread.start()

# ── TTS ────────────────────────────────────────────────────────────────────────
_tts_engine = "voxtral"   # set from args at startup

def speak(text, force=False):
    """Speak text. Skips if bob_ross is currently narrating (unless force=True)."""
    if not force and is_drawing():
        print(f"  💬 (muted while drawing) {text}")
        return
    print(f"  🗣  {text}")
    try:
        subprocess.run(
            [sys.executable, os.path.join(VOICE_DIR, "speak.py"), text, "--engine", _tts_engine],
            timeout=120)
    except Exception as e:
        print(f"  ⚠  speak.py error: {e}")

# ── Claude Haiku conversation brain ───────────────────────────────────────────
SYSTEM_PROMPT = """\
You are Robot Ross — a friendly robot arm that draws on paper while narrating \
in the warm, peaceful style of Bob Ross. You are at a live showcase, talking \
to visitors face-to-face.

CAPABILITIES
- Custom sketch: you can draw ANYTHING a visitor requests (AI generates the SVG, \
takes about 30 seconds). This is your main showcase feature.
- Pre-made designs (instant, no wait): {svg_list}
- Pyrography (wood burning) mode is available for special requests.

RECENT DRAWINGS
{recent}

CURRENT STATUS
{status}

PERSONALITY
- Warm, encouraging, gently poetic — like Bob Ross but for drawing robots.
- Keep responses SHORT (1-3 sentences) — you are speaking out loud.
- If you don't know something, say so warmly. Never make up facts.
- If a visitor asks what you can draw: mention the custom sketch feature first, \
then the pre-made options.
- If asked for a recommendation: suggest something from the pre-made list or \
something you've drawn recently that went well.

RESPONSE FORMAT
Always respond with a single JSON object. No markdown, no explanation outside the JSON.

To speak only:
  {{"action": "speak", "text": "..."}}

To draw a custom sketch (AI-generated):
  {{"action": "sketch", "subject": "a happy little bear in the forest", "text": "..."}}
  (text = what you say AS you start drawing — keep it short)

To draw a pre-made SVG (instant):
  {{"action": "svg", "file": "bear.svg", "text": "..."}}

To ask for the visitor's name (optional, personalises narration):
  {{"action": "ask_name", "text": "..."}}
"""

def _build_system(svgs, recent, drawing_subject):
    svg_list = ", ".join(svgs.keys()) if svgs else "none loaded"
    recent_str = "\n".join(f"  - {r}" for r in recent) if recent else "  (none yet this session)"
    if drawing_subject:
        status = f"Currently drawing: {drawing_subject} — arm is busy."
    else:
        status = "Idle — ready to draw."
    return SYSTEM_PROMPT.format(svg_list=svg_list, recent=recent_str, status=status)

OLLAMA_URL    = "http://localhost:11434/api/generate"
OLLAMA_MODELS = {
    "apertus": "MichelRosselli/apertus:8b-instruct-2509-q4_k_m",
    "mistral":  "mistral:latest",
}

# Shorter system prompt for local models (avoid context overflow)
LOCAL_SYSTEM_PROMPT = """\
You are Robot Ross, a friendly robot arm that draws on paper in the style of Bob Ross.
Available pre-made SVGs: {svg_list}.
You can sketch anything custom (takes ~30s).
Recent drawings: {recent}.
Status: {status}

OUTPUT RULES — follow exactly:
- Output ONE raw JSON object. No markdown. No code fences. No alternatives. No explanation.
- To speak: {{"action":"speak","text":"..."}}
- To draw custom: {{"action":"sketch","subject":"...","text":"..."}}
- To draw pre-made: {{"action":"svg","file":"bear.svg","text":"..."}}
- text must be short (1-2 sentences, spoken aloud).
Output only the JSON object and nothing else."""

def _build_local_system(svgs, recent, drawing_subject):
    svg_list   = ", ".join(svgs.keys()) if svgs else "none"
    recent_str = ", ".join(r.split(": ", 1)[-1] for r in recent) if recent else "none"
    status     = f"drawing {drawing_subject}" if drawing_subject else "idle"
    return LOCAL_SYSTEM_PROMPT.format(svg_list=svg_list, recent=recent_str, status=status)

def _parse_json_reply(reply, history):
    history.append({"role": "assistant", "content": reply})
    # Walk the string and extract the first complete, valid JSON object
    start = reply.find('{')
    while start != -1:
        depth = 0
        for i, ch in enumerate(reply[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(reply[start:i + 1])
                    except Exception:
                        break  # malformed — try next '{'
        start = reply.find('{', start + 1)
    return {"action": "speak", "text": reply}

def _chat_haiku(client, history, user_text, system):
    history.append({"role": "user", "content": user_text})
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        system=system,
        messages=history,
    )
    return _parse_json_reply(response.content[0].text.strip(), history)

def _chat_ollama(model_key, history, user_text, system):
    history.append({"role": "user", "content": user_text})
    turns = "\n".join(
        f"{'Visitor' if m['role'] == 'user' else 'Robot Ross'}: {m['content']}"
        for m in history[-6:]
    )
    prompt = f"{system}\n\nConversation:\n{turns}\n\nRobot Ross JSON:"
    model  = OLLAMA_MODELS[model_key]
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    try:
        req = urllib.request.Request(
            OLLAMA_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read()).get("response", "")
            return _parse_json_reply(raw.strip(), history)
    except Exception as e:
        print(f"  ⚠  Ollama ({model_key}) error: {e}", file=sys.stderr)
        return {"action": "speak", "text": "Pardon me, I got a little lost in thought. Could you say that again?"}

def chat(client, history, user_text, svgs, args):
    recent  = _get_recent_drawings()
    subject = current_subject()
    if args.brain in ("apertus", "mistral"):
        system = _build_local_system(svgs, recent, subject)
        return _chat_ollama(args.brain, history, user_text, system)
    system = _build_system(svgs, recent, subject)
    return _chat_haiku(client, history, user_text, system)

# ── Main conversation loop ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Robot Ross conversational showcase")
    parser.add_argument("--whisper",   default="base",  help="Whisper model size (tiny/base/small/medium)")
    parser.add_argument("--threshold", type=float, default=0.015,
                        help="Mic RMS threshold for speech detection (default 0.015)")
    parser.add_argument("--lang",      default=None,    help="Force STT language (e.g. en, es, fr)")
    parser.add_argument("--size",      type=float, default=80,  help="Drawing size mm (default 80)")
    parser.add_argument("--feed",      type=float, default=None, help="Drawing feed rate mm/min")
    parser.add_argument("--pyro",      action="store_true", help="Pyrography (wood burning) mode")
    parser.add_argument("--no-obs",    action="store_true", help="Skip OBS recording")
    parser.add_argument("--no-arm",    action="store_true", help="Conversation only, no arm movement (test)")
    parser.add_argument("--engine",    default="voxtral", choices=["kokoro", "voxtral", "system"],
                        help="TTS engine (default: voxtral)")
    parser.add_argument("--brain",     default="haiku",   choices=["haiku", "apertus", "mistral"],
                        help="Conversation brain (default: haiku)")
    args = parser.parse_args()

    # ── Init ──────────────────────────────────────────────────────────────────
    print("Robot Ross Showcase — starting up...")
    svgs    = _discover_svgs()
    print(f"  Pre-made SVGs: {list(svgs.keys())}")

    from listen import WhisperListener
    listener = WhisperListener(model_size=args.whisper, language=args.lang, threshold=args.threshold)
    listener._load()   # download/load model now, not mid-conversation

    global _tts_engine
    _tts_engine = args.engine

    claude  = anthropic.Anthropic()
    history = []
    buyer   = None

    # ── Greeting ──────────────────────────────────────────────────────────────
    greeting = (
        "Hi there! I'm Robot Ross, and I love to draw. "
        "What would you like me to draw for you today?"
    )
    speak(greeting)
    history.append({"role": "assistant", "content": json.dumps({"action": "speak", "text": greeting})})

    # ── Conversation loop ──────────────────────────────────────────────────────
    print("\nReady. Speak to Robot Ross (Ctrl+C or press Q to quit).\n")

    kb_thread = threading.Thread(target=_keyboard_watcher, daemon=True)
    kb_thread.start()

    while not _quit_flag.is_set():
        try:
            user_text = listener.listen()
        except KeyboardInterrupt:
            _quit_flag.set()
            break

        if _quit_flag.is_set():
            break

        if not user_text:
            continue

        # Local exit commands (voice)
        if user_text.lower().strip() in ("quit", "exit", "stop", "goodbye", "bye"):
            speak("It was a pleasure! I hope to draw something for you again soon. Goodbye!")
            break

        # Busy guard — still respond if arm is drawing
        drawing_note = ""
        if is_drawing():
            drawing_note = f" (Note: arm is currently drawing '{current_subject()}')"

        response = chat(claude, history, user_text + drawing_note, svgs, args)
        action   = response.get("action", "speak")
        text     = response.get("text", "")

        if action == "ask_name":
            speak(text)
            name_text = listener.listen(prompt="  🎤 Waiting for name...")
            if name_text:
                buyer = name_text.strip().split()[0].capitalize()
                speak(f"Wonderful to meet you, {buyer}!")
                history.append({"role": "user",      "content": name_text})
                history.append({"role": "assistant",  "content": f"Great, I'll remember your name is {buyer}."})

        elif action == "sketch":
            subject = response.get("subject", user_text)
            if is_drawing():
                speak(f"I'm still working on {current_subject()}! Give me a moment to finish first.", force=True)
            else:
                speak(text or f"I'd love to draw {subject} for you.")
                speak(f"Shall I go ahead and draw {subject}?")
                time.sleep(0.8)
                confirm = listener.listen(prompt="  🎤 Waiting for confirmation...", max_seconds=8)
                if confirm and any(w in confirm.lower() for w in ("yes", "yeah", "sure", "go", "please", "ok", "yep", "do it")):
                    speak("Wonderful! Let me get started.")
                    start_draw(subject, buyer=buyer, size=args.size, feed=args.feed,
                               pyro=args.pyro, no_obs=args.no_obs, no_arm=args.no_arm,
                               engine=args.engine)
                else:
                    speak("No problem! What would you like me to draw instead?")
                    history.append({"role": "user",      "content": f"(confirmed: no) {confirm or 'no response'}"})
                    history.append({"role": "assistant",  "content": "I won't draw that. What would you like instead?"})

        elif action == "svg":
            file    = response.get("file", "")
            matched = None
            for name, fname in svgs.items():
                if name in file.lower() or fname.lower() == file.lower():
                    matched = fname
                    break
            if matched and not is_drawing():
                speak(text or f"I'd love to draw that for you.")
                speak(f"Shall I go ahead and draw {os.path.splitext(matched)[0]}?")
                time.sleep(0.8)
                confirm = listener.listen(prompt="  🎤 Waiting for confirmation...", max_seconds=8)
                if not (confirm and any(w in confirm.lower() for w in ("yes", "yeah", "sure", "go", "please", "ok", "yep", "do it"))):
                    speak("No problem! Just say the word when you're ready.")
                    history.append({"role": "user",      "content": f"(confirmed: no) {confirm or 'no response'}"})
                    history.append({"role": "assistant",  "content": "I won't draw that. What would you like instead?"})
                else:
                    svg_path = os.path.join(SVG_DIR, matched)
                    cmd = [sys.executable, BOB_ROSS_PY, "svg", svg_path,
                           "--engine", args.engine]
                    if buyer:
                        cmd += ["--buyer", buyer]
                    if args.no_obs:
                        cmd.append("--no-obs")
                    if args.pyro:
                        cmd.append("--pyro")
                    cmd += ["--size", str(args.size)]
                    if args.feed:
                        cmd += ["--feed", str(args.feed)]
                    if args.no_arm:
                        cmd.append("--dry-run")
                    def _run_svg(c=cmd):
                        subprocess.run(c)
                    t = threading.Thread(target=_run_svg, daemon=True)
                    t.start()
            elif is_drawing():
                speak(f"I'm still working on {current_subject()}! Ask me again in a moment.", force=True)
            else:
                # No pre-made SVG matched — fall back to AI sketch
                subject = response.get("subject", file) or user_text
                speak(text or f"I'll draw {subject} for you from scratch.")
                speak(f"Shall I go ahead and draw {subject}?")
                time.sleep(0.8)
                confirm = listener.listen(prompt="  🎤 Waiting for confirmation...", max_seconds=8)
                if confirm and any(w in confirm.lower() for w in ("yes", "yeah", "sure", "go", "please", "ok", "yep", "do it")):
                    speak("Wonderful! Let me get started.")
                    start_draw(subject, buyer=buyer, size=args.size, feed=args.feed,
                               pyro=args.pyro, no_obs=args.no_obs, no_arm=args.no_arm,
                               engine=args.engine)
                else:
                    speak("No problem! What would you like me to draw instead?")

        else:  # speak
            if text:
                speak(text)

    _quit_flag.set()  # ensure keyboard watcher exits too
    if not user_text or user_text.lower().strip() not in ("quit", "exit", "stop", "goodbye", "bye"):
        speak("It was lovely chatting with you. Goodbye!", force=True)
    print("Session ended.")


if __name__ == "__main__":
    main()
