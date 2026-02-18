#!/usr/bin/env python3
"""
bob_ross.py
-----------
Bob Ross mode: the Huenit robot arm draws/writes while narrating poetically.

Usage:
    python3 bob_ross.py write "OpenClaw"
    python3 bob_ross.py write "Hello" --size 15
    python3 bob_ross.py draw square
    python3 bob_ross.py draw circle 20
    python3 bob_ross.py check          # readiness check only

Stop at any time with Ctrl+C or SIGTERM.
"""

import sys
import os
import json
import subprocess
import threading
import time
import signal
import argparse
import urllib.request
import urllib.error
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR  = os.path.dirname(SCRIPT_DIR)
HUENIT_DIR  = os.path.join(SKILLS_DIR, "huenit")
VOICE_DIR   = os.path.join(SKILLS_DIR, "voice")

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_URL       = "http://localhost:11434/api/generate"
OLLAMA_MODEL     = "qwen2.5:7b"
PORT             = os.environ.get("HUENIT_PORT", "/dev/cu.usbserial-310")
CALIBRATION_FILE = os.path.join(HUENIT_DIR, "calibration.json")

# Seconds between live commentary phrases while drawing
COMMENTARY_INTERVAL = 6

# Log file
LOG_FILE = os.path.join(SCRIPT_DIR, "bob_ross.log")


def log(event, detail=""):
    """Append a timestamped log entry."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event}"
    if detail:
        line += f" — {detail}"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # never let logging break the main flow
    print(line)


# ── Shared state ──────────────────────────────────────────────────────────────
stop_flag = threading.Event()
_draw_proc = None
_draw_proc_lock = threading.Lock()


# ── Voice ─────────────────────────────────────────────────────────────────────
def speak(text):
    """Speak text via speak.py. Blocks until done."""
    if stop_flag.is_set():
        return
    print(f"  🗣  {text}")
    try:
        subprocess.run(
            ["python3", os.path.join(VOICE_DIR, "speak.py"), text],
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        print("  ⚠  speak.py timed out")
    except Exception as e:
        print(f"  ⚠  speak.py error: {e}")


def warning_tone():
    """Three Ping beeps to warn humans to step away."""
    ping = "/System/Library/Sounds/Ping.aiff"
    for _ in range(3):
        if stop_flag.is_set():
            return
        try:
            subprocess.run(["afplay", ping], capture_output=True, timeout=3)
        except Exception:
            # Fallback: use say with a short beep word
            subprocess.run(["say", "-v", "Evan", "beep"], capture_output=True, timeout=3)
        time.sleep(0.5)


# ── Readiness check ───────────────────────────────────────────────────────────
def readiness_check():
    """Return a list of issues. Empty list = all good."""
    issues = []

    # Robot arm port
    if not os.path.exists(PORT):
        issues.append(f"Robot arm port not found: {PORT}")

    # Calibration file
    if not os.path.exists(CALIBRATION_FILE):
        issues.append(
            "No calibration file found. "
            "Run: python3 ~/.openclaw/workspace/skills/huenit/huenit_draw.py calibrate"
        )

    # Session ready flag (set by calibrate, cleared on reboot)
    if not os.path.exists("/tmp/huenit_ready.flag"):
        issues.append(
            "Robot not calibrated this session. "
            "Run: python3 ~/.openclaw/workspace/skills/huenit/huenit_draw.py calibrate"
        )

    # Ollama reachable
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            if not any(OLLAMA_MODEL.split(":")[0] in m for m in models):
                issues.append(
                    f"Model '{OLLAMA_MODEL}' not found in Ollama. "
                    f"Available: {', '.join(models) or 'none'}"
                )
    except Exception as e:
        issues.append(f"Ollama not reachable at localhost:11434 — {e}")

    # Huenit scripts exist
    for script in ("huenit_write.py", "huenit_draw.py"):
        if not os.path.exists(os.path.join(HUENIT_DIR, script)):
            issues.append(f"Missing huenit script: {script}")

    # Voice script exists
    if not os.path.exists(os.path.join(VOICE_DIR, "speak.py")):
        issues.append("Missing voice script: speak.py")

    return issues


# ── Ollama narration ──────────────────────────────────────────────────────────
def generate_narration(action, content):
    """Ask Qwen to generate Bob Ross style narration. Returns dict or fallback."""
    if action == "write":
        text_lines = content.replace('\\n', '\n').split('\n')
        text_lines = [l for l in text_lines if l.strip()]
        if len(text_lines) > 1:
            action_desc = f"write {len(text_lines)} lines of text: {' / '.join(text_lines)}"
        else:
            action_desc = f"write the text '{content}'"
    elif action == "svg":
        action_desc = f"draw a vector illustration from the file '{os.path.basename(content)}'"
    else:
        action_desc = f"draw a {content}"

    prompt = (
        "You are Bob Ross, the gentle and poetic TV painter. "
        "But today, instead of painting, you are controlling a robot arm that draws on paper.\n\n"
        f"A request has come in to {action_desc}.\n\n"
        "Generate narration as a JSON object with exactly these keys:\n"
        '- "intro": 1-2 warm sentences welcoming the request. Start with "We got a lovely request..."\n'
        '- "commentary": a list of 5 short, poetic, Bob Ross-style phrases to say WHILE drawing. '
        "They do not need to match specific letters or shapes — just be encouraging and peaceful. "
        "Each phrase is 1 short sentence.\n"
        '- "outro": 1-2 warm sentences for when the drawing is done, '
        "telling the human they can remove their piece.\n\n"
        "Respond with ONLY the JSON object. No explanation, no markdown."
    )

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }).encode()

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            narration = json.loads(result["response"])
            # Validate expected keys
            if all(k in narration for k in ("intro", "commentary", "outro")):
                return narration
            print("  ⚠  Qwen response missing expected keys, using fallback")
    except Exception as e:
        print(f"  ⚠  Ollama error: {e} — using fallback narration")

    # Fallback narration
    return {
        "intro": (
            f"We got a lovely request for {content} today. "
            "Let's see what happy little marks we can make together."
        ),
        "commentary": [
            "Every stroke is a happy little decision.",
            "Nice and easy, we're doing beautifully.",
            "Let's add a little something right here.",
            "There are no mistakes, only happy little accidents.",
            "We're almost there. Isn't this something special.",
        ],
        "outro": (
            "And there we have it. Isn't that a lovely piece. "
            "You can remove your artwork now."
        ),
    }


# ── Commentary thread ─────────────────────────────────────────────────────────
def run_commentary(phrases, draw_done):
    """Fire commentary phrases at intervals while drawing. Runs in background thread."""
    time.sleep(3)  # give the arm a moment to start
    for phrase in phrases:
        if stop_flag.is_set() or draw_done.is_set():
            break
        speak(phrase)
        draw_done.wait(timeout=COMMENTARY_INTERVAL)


# ── Drawing ───────────────────────────────────────────────────────────────────
def run_draw(action, content, size=None):
    """
    Launch the appropriate huenit script as a subprocess.
    Monitors stop_flag and terminates + lifts pen if set.
    Returns True on success.
    """
    global _draw_proc

    if action == "write":
        cmd = ["python3", os.path.join(HUENIT_DIR, "huenit_write.py"), content]
        if size:
            cmd += ["--size", str(size)]
    elif action == "draw":
        cmd = ["python3", os.path.join(HUENIT_DIR, "huenit_draw.py"), content]
        if size:
            cmd.append(str(size))
    elif action == "svg":
        cmd = ["python3", os.path.join(HUENIT_DIR, "huenit_svg.py"), content]
        if size:
            cmd += ["--size", str(size)]
    else:
        print(f"  ⚠  Unknown action: {action}")
        return False

    with _draw_proc_lock:
        _draw_proc = subprocess.Popen(cmd)

    try:
        while _draw_proc.poll() is None:
            if stop_flag.is_set():
                _emergency_stop()
                return False
            time.sleep(0.1)
        return _draw_proc.returncode == 0
    finally:
        with _draw_proc_lock:
            _draw_proc = None


def _emergency_stop():
    """Terminate the draw process and lift the pen."""
    global _draw_proc
    with _draw_proc_lock:
        proc = _draw_proc
    if proc and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=3)

    # Lift pen via raw serial
    try:
        import serial
        s = serial.Serial(PORT, 115200, timeout=1)
        time.sleep(0.5)
        s.write(b"G21\nG91\nG1 Z5 F800\nM400\n")
        s.flush()
        time.sleep(1.5)
        s.write(b"G90\n")
        s.flush()
        s.close()
        print("  ✅ Pen lifted — arm safe.")
    except Exception as e:
        print(f"  ⚠  Could not lift pen via serial: {e}")


# ── Signal handler ────────────────────────────────────────────────────────────
def handle_stop(signum, frame):
    log("STOP", "signal received — aborting job")
    stop_flag.set()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Robot Ross mode for the Huenit robot arm")
    parser.add_argument(
        "action",
        choices=["write", "draw", "svg", "check", "calibrate"],
        help="write TEXT | draw SHAPE | svg FILE | check (readiness only) | calibrate",
    )
    parser.add_argument(
        "content",
        nargs="?",
        help="Text to write, shape name, or path to SVG file",
    )
    parser.add_argument("--size", type=float, help="Size in mm (letter height or shape size)")
    parser.add_argument("--no-voice", action="store_true", help="Skip all voice narration")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    # ── READINESS CHECK ───────────────────────────────────────────────────────
    print("[robot-ross] Checking system readiness...")
    issues = readiness_check()

    if args.action == "calibrate":
        log("CALIBRATE", "starting interactive calibration")
        subprocess.run(["python3", os.path.join(HUENIT_DIR, "huenit_draw.py"), "calibrate"])
        log("CALIBRATE", "calibration complete")
        sys.exit(0)

    if args.action == "check":
        if issues:
            log("CHECK", f"not ready — {'; '.join(issues)}")
            print("❌ System not ready:")
            for issue in issues:
                print(f"  · {issue}")
            sys.exit(1)
        else:
            log("CHECK", "all systems ready")
            print("✅ All systems ready.")
            sys.exit(0)

    if issues:
        log("ERROR", f"readiness check failed — {'; '.join(issues)}")
        print("❌ System not ready:")
        for issue in issues:
            print(f"  · {issue}")
        sys.exit(1)

    if not args.content:
        parser.error("content is required for write/draw")

    log("JOB START", f"action={args.action} content={args.content!r}" + (f" size={args.size}" if args.size else ""))

    # ── GENERATE NARRATION ────────────────────────────────────────────────────
    if not args.no_voice:
        log("NARRATION", "requesting from Qwen")
        narration = generate_narration(args.action, args.content)
        log("NARRATION", "ready" if narration.get("intro") else "using fallback")
    else:
        narration = {"intro": "", "commentary": [], "outro": ""}
        log("NARRATION", "skipped (--no-voice)")

    if stop_flag.is_set():
        return

    # ── WARNING TONE ──────────────────────────────────────────────────────────
    log("WARNING TONE", "playing — stand clear")
    warning_tone()

    if stop_flag.is_set():
        log("STOP", "aborted during warning tone")
        return

    # ── INTRO ─────────────────────────────────────────────────────────────────
    if not args.no_voice and narration["intro"]:
        log("VOICE INTRO", narration["intro"])
        speak(narration["intro"])

    if stop_flag.is_set():
        log("STOP", "aborted during intro")
        speak("Stopping before we start.")
        return

    # ── DRAW + LIVE COMMENTARY IN PARALLEL ───────────────────────────────────
    log("DRAW START", f"{args.action} {args.content!r}")
    draw_start = time.time()
    draw_done = threading.Event()

    if not args.no_voice and narration["commentary"]:
        t = threading.Thread(
            target=run_commentary,
            args=(narration["commentary"], draw_done),
            daemon=True,
        )
        t.start()

    success = run_draw(args.action, args.content, args.size)
    draw_done.set()
    elapsed = round(time.time() - draw_start, 1)

    if stop_flag.is_set():
        log("STOP", f"arm stopped mid-draw after {elapsed}s")
        speak("The arm has been stopped. Please check the paper.")
        return

    time.sleep(0.5)  # let any in-flight speech wrap up

    # ── OUTRO ─────────────────────────────────────────────────────────────────
    if not args.no_voice:
        if success:
            log("VOICE OUTRO", narration["outro"])
            speak(narration["outro"])
        else:
            log("ERROR", "draw subprocess failed")
            speak("Something went wrong with the arm. Please check the setup and try again.")

    status = "success" if success else "failed"
    log("JOB END", f"status={status} duration={elapsed}s")


if __name__ == "__main__":
    main()
