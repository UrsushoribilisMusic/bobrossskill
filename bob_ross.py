#!/usr/bin/env python3
"""
bob_ross.py
-----------
Bob Ross mode: the Huenit robot arm draws/writes while narrating poetically.

Usage:
    python bob_ross.py write "OpenClaw"
    python bob_ross.py sketch "a happy little bear"    # AI-generated SVG
    python bob_ross.py svg path/to/file.svg
    python bob_ross.py draw square
    python bob_ross.py check          # readiness check only
    python bob_ross.py calibrate      # interactive calibration

Options:
    --size 80          drawing size mm (default: 80)
    --feed 400         feed rate mm/min
    --buyer "Alice"    personalise narration with visitor name
    --pyro             pyrography (wood burning) mode — slow feed
    --engine kokoro    TTS engine: kokoro (local) | voxtral (Mistral API) | system
    --no-voice         skip all narration
    --dry-run          narrate without moving the arm (test/demo mode)
    --direct           skip preview, draw immediately
    --calibrate        run calibration before drawing

Stop at any time with Ctrl+C or SIGTERM.
"""

import sys, os, json, subprocess, threading, time, signal, argparse, re
import urllib.request, urllib.error
from datetime import datetime

# ── Infisical vault (loads ANTHROPIC_API_KEY into env) ────────────────────────
try:
    _vault_path = os.path.join(os.path.expanduser("~"), "agentic-fleet-hub", "vault")
    sys.path.insert(0, _vault_path)
    from vault import load_secrets as _vault_load
    _vault_load(["ANTHROPIC_API_KEY"])
except Exception:
    pass  # fall back to env vars already set

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
HUENIT_DIR  = os.path.join(SCRIPT_DIR, "huenit")
VOICE_DIR   = os.path.join(SCRIPT_DIR, "voice")

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_URL       = "http://localhost:11434/api/generate"
OLLAMA_MODEL     = "MichelRosselli/apertus:8b-instruct-2509-q4_k_m"
PORT             = os.environ.get("HUENIT_PORT", "/dev/cu.usbserial-310")
CALIBRATION_FILE = os.path.join(HUENIT_DIR, "calibration.json")

# Seconds between live commentary phrases while drawing
COMMENTARY_INTERVAL = 6

# Log file
LOG_FILE = os.path.join(SCRIPT_DIR, "bob_ross.log")

# ── JOB LOCK ──────────────────────────────────────────────────────────────────
import tempfile, ctypes
_TMP      = tempfile.gettempdir()
_LOCK_FILE = os.path.join(_TMP, "robot_ross_running.lock")

def _is_stale_lock(lock_path):
    """Return True if the lock file belongs to a dead process."""
    try:
        with open(lock_path) as f:
            content = f.read()
        pid = int([l.split("=")[1] for l in content.splitlines() if l.startswith("pid=")][0])
    except Exception:
        return True  # unreadable → stale
    if sys.platform == "win32":
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        return True
    else:
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True

def _acquire_lock(action):
    if os.path.exists(_LOCK_FILE):
        if not _is_stale_lock(_LOCK_FILE):
            print("❌ Another Robot Ross job is already running. Use Ctrl+C to stop it.")
            sys.exit(1)
        os.remove(_LOCK_FILE)
    with open(_LOCK_FILE, "w") as f:
        f.write(f"pid={os.getpid()}\naction={action}\n")

def _release_lock():
    try:
        os.remove(_LOCK_FILE)
    except OSError:
        pass


def log(event, detail=""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {event}"
    if detail:
        line += f" — {detail}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


# ── Shared state ──────────────────────────────────────────────────────────────
stop_flag      = threading.Event()
_draw_proc     = None
_draw_proc_lock = threading.Lock()


# ── Voice ─────────────────────────────────────────────────────────────────────
_dual_voice = False
_tts_engine = "kokoro"   # set from --engine at startup


def speak(text):
    """Speak text via speak.py. Blocks until done."""
    if stop_flag.is_set():
        return
    print(f"  🗣  {text}")
    cmd = [sys.executable, os.path.join(VOICE_DIR, "speak.py"), text,
           "--engine", _tts_engine]
    if _dual_voice:
        cmd.append("--dual")
    try:
        subprocess.run(cmd, timeout=120)
    except subprocess.TimeoutExpired:
        print("  ⚠  speak.py timed out")
    except Exception as e:
        print(f"  ⚠  speak.py error: {e}")


def warning_tone():
    """Three Ping beeps to warn humans to step away."""
    import platform
    for _ in range(3):
        if stop_flag.is_set():
            return
        try:
            if platform.system() == "Windows":
                import winsound
                winsound.Beep(1000, 200)
            elif platform.system() == "Darwin":
                subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"],
                               capture_output=True, timeout=3)
            else:
                subprocess.run(["beep"], capture_output=True, timeout=3)
        except Exception:
            pass
        time.sleep(0.5)


# ── Readiness check ───────────────────────────────────────────────────────────
def readiness_check():
    issues = []

    # Robot arm port
    _port_ok = False
    if sys.platform == "win32":
        try:
            import serial
            s = serial.Serial(PORT, 115200, timeout=0.5)
            s.close()
            _port_ok = True
        except Exception:
            pass
    else:
        _port_ok = os.path.exists(PORT)
    if not _port_ok:
        issues.append(f"Robot arm port not found: {PORT}")

    # Calibration file
    if not os.path.exists(CALIBRATION_FILE):
        issues.append("No calibration file — run: python bob_ross.py calibrate")

    # Session ready flag
    _ready_flag = os.path.join(_TMP, "huenit_ready.flag")
    if not os.path.exists(_ready_flag):
        issues.append("Robot not calibrated this session — run: python bob_ross.py calibrate")

    # Ollama reachable + model available
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            model_base = OLLAMA_MODEL.split(":")[0].split("/")[-1].lower()
            if not any(model_base in m.lower() for m in models):
                issues.append(
                    f"Model '{OLLAMA_MODEL}' not in Ollama. "
                    f"Available: {', '.join(models) or 'none'}"
                )
    except Exception as e:
        issues.append(f"Ollama not reachable at localhost:11434 — {e}")

    # Huenit scripts
    for script in ("huenit_write.py", "huenit_draw.py", "huenit_svg.py"):
        if not os.path.exists(os.path.join(HUENIT_DIR, script)):
            issues.append(f"Missing huenit script: {script}")

    # Voice script
    if not os.path.exists(os.path.join(VOICE_DIR, "speak.py")):
        issues.append("Missing voice script: speak.py")

    return issues


# ── Ollama narration ──────────────────────────────────────────────────────────
def generate_narration(action, content, buyer=None):
    """Ask Apertus to generate Bob Ross style narration. Returns dict or fallback."""
    buyer_line = (f" The customer's name is {buyer}. Address them warmly by name in the intro."
                  if buyer else "")
    speakable = content.replace('\\n', ' ').replace('\n', ' ').strip()

    if action == "write":
        lines = [l for l in speakable.replace('\\n', '\n').split('\n') if l.strip()]
        action_desc = (f"write {len(lines)} lines of text: {' / '.join(lines)}"
                       if len(lines) > 1 else f"write the text '{speakable}'")
    elif action in ("svg", "sketch"):
        action_desc = f"draw a vector illustration of '{speakable}'"
    elif action == "pyro":
        action_desc = f"burn a wood pyrography of '{speakable}'"
    else:
        action_desc = f"draw a {speakable}"

    prompt = (
        "You are Bob Ross, the gentle and poetic TV painter. "
        "But today, instead of painting, you are controlling a robot arm that draws on paper.\n\n"
        f"A request has come in to {action_desc}.{buyer_line}\n\n"
        "Generate narration as a JSON object with exactly these keys:\n"
        '- "intro": 1-2 warm sentences welcoming the request. Start with "We got a lovely request..."\n'
        '- "commentary": a list of 5 short, poetic, Bob Ross-style phrases to say WHILE drawing. '
        "Each phrase is 1 short sentence.\n"
        '- "outro": 1-2 warm sentences for when the drawing is done.\n\n'
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
            OLLAMA_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read()).get("response", "")
            import re as _re
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                narration = json.loads(m.group())
                if all(k in narration for k in ("intro", "commentary", "outro")):
                    return narration
            print("  ⚠  Apertus response missing expected keys, using fallback")
    except Exception as e:
        print(f"  ⚠  Ollama error: {e} — using fallback narration")

    greeting = f"Good morning, {buyer}. " if buyer else ""
    return {
        "intro": (
            f"{greeting}We got a lovely request for {speakable} today. "
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
    time.sleep(3)
    for phrase in phrases:
        if stop_flag.is_set() or draw_done.is_set():
            break
        speak(phrase)
        draw_done.wait(timeout=COMMENTARY_INTERVAL)


# ── Drawing ───────────────────────────────────────────────────────────────────
def run_draw(action, content, size=None, pyro=False, feed=None):
    global _draw_proc

    pyro_feed = feed or (45 if pyro else None)

    if action == "write":
        cmd = [sys.executable, os.path.join(HUENIT_DIR, "huenit_write.py"), content]
        if size:
            cmd += ["--size", str(size)]
        if pyro_feed:
            cmd += ["--feed", str(pyro_feed)]
    elif action == "draw":
        cmd = [sys.executable, os.path.join(HUENIT_DIR, "huenit_draw.py"), content]
        if size:
            cmd.append(str(size))
    elif action in ("svg", "sketch"):
        cmd = [sys.executable, os.path.join(HUENIT_DIR, "huenit_svg.py"), content]
        if size:
            cmd += ["--size", str(size)]
        if pyro_feed:
            cmd += ["--feed", str(pyro_feed)]
        if pyro:
            cmd.append("--pyro")
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
    global _draw_proc
    with _draw_proc_lock:
        proc = _draw_proc
    if proc and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=3)
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


def handle_stop(signum, frame):
    log("STOP", "signal received — aborting job")
    stop_flag.set()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Robot Ross — Huenit arm drawing with Bob Ross narration")
    parser.add_argument("action",
        choices=["write", "draw", "svg", "sketch", "check", "calibrate"],
        help="write TEXT | draw SHAPE | svg FILE | sketch SUBJECT | check | calibrate")
    parser.add_argument("content", nargs="?",
        help="Text to write, shape name, SVG path, or sketch subject")
    parser.add_argument("--size",     type=float, default=80, help="Size in mm (default 80)")
    parser.add_argument("--feed",     type=float, default=None, help="Feed rate mm/min")
    parser.add_argument("--buyer",    default=None, help="Visitor/buyer name for personalised narration")
    parser.add_argument("--pyro",     action="store_true", help="Pyrography mode (slow feed for wood burning)")
    parser.add_argument("--engine",   default="kokoro", choices=["kokoro", "voxtral", "system"],
                        help="TTS engine (default: kokoro)")
    parser.add_argument("--no-voice", action="store_true", help="Skip all voice narration")
    parser.add_argument("--dry-run",  action="store_true", help="Narrate without moving the arm")
    parser.add_argument("--direct",   action="store_true", help="Skip preview, draw immediately")
    parser.add_argument("--calibrate-before", action="store_true",
                        help="Run calibration step before drawing")
    args = parser.parse_args()

    global _tts_engine
    _tts_engine = args.engine

    signal.signal(signal.SIGINT,  handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # ── CALIBRATE ─────────────────────────────────────────────────────────────
    if args.action == "calibrate":
        log("CALIBRATE", "starting interactive calibration")
        subprocess.run([sys.executable,
                        os.path.join(HUENIT_DIR, "huenit_draw.py"), "calibrate"])
        log("CALIBRATE", "calibration complete")
        sys.exit(0)

    # ── CHECK ─────────────────────────────────────────────────────────────────
    issues = readiness_check() if not args.dry_run else []
    if args.action == "check":
        if issues:
            print("❌ System not ready:")
            for issue in issues:
                print(f"  · {issue}")
            sys.exit(1)
        print("✅ All systems ready.")
        sys.exit(0)

    if issues:
        log("ERROR", f"readiness check failed — {'; '.join(issues)}")
        print("❌ System not ready:")
        for issue in issues:
            print(f"  · {issue}")
        sys.exit(1)

    if not args.content:
        parser.error("content is required for this action")

    # ── LOCK ──────────────────────────────────────────────────────────────────
    _acquire_lock(args.action)
    try:
        _run_job(args)
    finally:
        _release_lock()


def _run_job(args):
    action  = args.action
    content = args.content

    # ── SKETCH: generate SVG first ────────────────────────────────────────────
    if action == "sketch":
        log("SKETCH", f"generating SVG for '{content}'")
        from huenit.quickdraw_compose import compose_scene
        import tempfile
        svg_path = os.path.join(tempfile.gettempdir(), "bob_ross_sketch.svg")
        ok = compose_scene(content, svg_path)
        if not ok:
            print("  ⚠  SVG generation failed")
            sys.exit(1)
        action  = "svg"
        content = svg_path

    log("JOB START", f"action={action} content={content!r} size={args.size}")

    # ── NARRATION ─────────────────────────────────────────────────────────────
    if not args.no_voice:
        log("NARRATION", "requesting from Apertus")
        narration = generate_narration(args.action, args.content, buyer=args.buyer)
        log("NARRATION", "ready" if narration.get("intro") else "using fallback")
    else:
        narration = {"intro": "", "commentary": [], "outro": ""}

    if stop_flag.is_set():
        return

    # ── WARNING TONE ──────────────────────────────────────────────────────────
    if not args.dry_run:
        log("WARNING TONE", "playing — stand clear")
        warning_tone()

    if stop_flag.is_set():
        return

    # ── INTRO ─────────────────────────────────────────────────────────────────
    if not args.no_voice and narration["intro"]:
        log("VOICE INTRO", narration["intro"])
        speak(narration["intro"])

    if stop_flag.is_set():
        return

    # ── DRAW + COMMENTARY ─────────────────────────────────────────────────────
    log("DRAW START", f"{action} {content!r}")
    draw_start = time.time()
    draw_done  = threading.Event()

    if not args.no_voice and narration["commentary"]:
        threading.Thread(
            target=run_commentary,
            args=(narration["commentary"], draw_done),
            daemon=True,
        ).start()

    if args.dry_run:
        log("DRY RUN", "simulating draw — arm movement skipped")
        time.sleep(20)
        success = True
    else:
        success = run_draw(action, content, args.size, pyro=args.pyro, feed=args.feed)

    draw_done.set()
    elapsed = round(time.time() - draw_start, 1)

    if stop_flag.is_set():
        log("STOP", f"arm stopped after {elapsed}s")
        speak("The arm has been stopped. Please check the paper.")
        return

    time.sleep(0.5)

    # ── OUTRO ─────────────────────────────────────────────────────────────────
    if not args.no_voice:
        if success:
            log("VOICE OUTRO", narration["outro"])
            speak(narration["outro"])
        else:
            log("ERROR", "draw subprocess failed")
            speak("Something went wrong with the arm. Please check the setup and try again.")

    log("JOB END", f"status={'success' if success else 'failed'} duration={elapsed}s")


if __name__ == "__main__":
    main()
