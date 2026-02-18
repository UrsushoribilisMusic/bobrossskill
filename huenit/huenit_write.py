#!/usr/bin/env python3
"""
huenit_write.py
---------------
Write text with the Huenit robot arm using a simple stroke font.

Usage:
    python3 huenit_write.py "Hello"
    python3 huenit_write.py "OpenClaw" --size 8 --sound sounds/ready.mp3

Options:
    --size N        Letter height in mm (default 10)
    --spacing N     Space between letters in mm (default 2)
    --sound FILE    Play an mp3 before drawing starts
    --feed N        Drawing feed rate mm/min (default 400)
"""

import sys, os, time, re, math, threading, json, argparse, subprocess
import serial

# ── Config ────────────────────────────────────────────────────────────────────
PORT = os.environ.get("HUENIT_PORT", "/dev/cu.usbserial-310")
BAUD = 115200
DEFAULT_DRAW_FEED = 400
TRAVEL_FEED = 800
Z_UP = 3.0  # default, overridden by calibration

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CALIBRATION_FILE = os.path.join(SCRIPT_DIR, "calibration.json")
READY_FLAG = "/tmp/huenit_ready.flag"

OK_PAT = re.compile(rb"\bok\b", re.I)


def check_ready():
    if not os.path.exists(READY_FLAG):
        print("  ❌ Robot not calibrated this session.")
        print("     Run:  python3 huenit_draw.py calibrate")
        sys.exit(1)

# ── Stroke Font ───────────────────────────────────────────────────────────────
# Each letter is defined on a unit grid (0-1 width, 0-1 height).
# A letter is a list of strokes. Each stroke is a list of (x, y) points.
# Multiple strokes = pen lifts between them.
# Origin is bottom-left. Y goes up.

FONT = {
    'A': [
        [(0, 0), (0.5, 1), (1, 0)],
        [(0.2, 0.4), (0.8, 0.4)],
    ],
    'B': [
        [(0, 0), (0, 1), (0.7, 1), (0.9, 0.85), (0.9, 0.65), (0.7, 0.5), (0, 0.5)],
        [(0.7, 0.5), (0.9, 0.35), (0.9, 0.15), (0.7, 0), (0, 0)],
    ],
    'C': [
        [(1, 0.85), (0.7, 1), (0.3, 1), (0, 0.7), (0, 0.3), (0.3, 0), (0.7, 0), (1, 0.15)],
    ],
    'D': [
        [(0, 0), (0, 1), (0.6, 1), (0.9, 0.75), (0.9, 0.25), (0.6, 0), (0, 0)],
    ],
    'E': [
        [(0.8, 0), (0, 0), (0, 0.5), (0.6, 0.5)],
        [(0, 0.5), (0, 1), (0.8, 1)],
    ],
    'F': [
        [(0, 0), (0, 0.5), (0.6, 0.5)],
        [(0, 0.5), (0, 1), (0.8, 1)],
    ],
    'G': [
        [(1, 0.85), (0.7, 1), (0.3, 1), (0, 0.7), (0, 0.3), (0.3, 0), (0.7, 0), (1, 0.3), (1, 0.5), (0.5, 0.5)],
    ],
    'H': [
        [(0, 0), (0, 1)],
        [(0, 0.5), (0.8, 0.5)],
        [(0.8, 0), (0.8, 1)],
    ],
    'I': [
        [(0.2, 0), (0.6, 0)],
        [(0.4, 0), (0.4, 1)],
        [(0.2, 1), (0.6, 1)],
    ],
    'J': [
        [(0.2, 1), (0.8, 1)],
        [(0.6, 1), (0.6, 0.2), (0.4, 0), (0.2, 0), (0, 0.2)],
    ],
    'K': [
        [(0, 0), (0, 1)],
        [(0.8, 1), (0, 0.4), (0.8, 0)],
    ],
    'L': [
        [(0, 1), (0, 0), (0.7, 0)],
    ],
    'M': [
        [(0, 0), (0, 1), (0.5, 0.5), (1, 1), (1, 0)],
    ],
    'N': [
        [(0, 0), (0, 1), (0.8, 0), (0.8, 1)],
    ],
    'O': [
        [(0.3, 0), (0.7, 0), (1, 0.3), (1, 0.7), (0.7, 1), (0.3, 1), (0, 0.7), (0, 0.3), (0.3, 0)],
    ],
    'P': [
        [(0, 0), (0, 1), (0.7, 1), (0.9, 0.85), (0.9, 0.6), (0.7, 0.45), (0, 0.45)],
    ],
    'Q': [
        [(0.3, 0), (0.7, 0), (1, 0.3), (1, 0.7), (0.7, 1), (0.3, 1), (0, 0.7), (0, 0.3), (0.3, 0)],
        [(0.6, 0.3), (1, 0)],
    ],
    'R': [
        [(0, 0), (0, 1), (0.7, 1), (0.9, 0.85), (0.9, 0.6), (0.7, 0.45), (0, 0.45)],
        [(0.5, 0.45), (0.9, 0)],
    ],
    'S': [
        [(0.9, 0.85), (0.7, 1), (0.3, 1), (0, 0.8), (0, 0.6), (0.3, 0.5), (0.7, 0.5), (1, 0.4), (1, 0.2), (0.7, 0), (0.3, 0), (0.1, 0.15)],
    ],
    'T': [
        [(0, 1), (1, 1)],
        [(0.5, 1), (0.5, 0)],
    ],
    'U': [
        [(0, 1), (0, 0.2), (0.2, 0), (0.6, 0), (0.8, 0.2), (0.8, 1)],
    ],
    'V': [
        [(0, 1), (0.5, 0), (1, 1)],
    ],
    'W': [
        [(0, 1), (0.25, 0), (0.5, 0.6), (0.75, 0), (1, 1)],
    ],
    'X': [
        [(0, 0), (0.8, 1)],
        [(0, 1), (0.8, 0)],
    ],
    'Y': [
        [(0, 1), (0.4, 0.5), (0.4, 0)],
        [(0.8, 1), (0.4, 0.5)],
    ],
    'Z': [
        [(0, 1), (0.8, 1), (0, 0), (0.8, 0)],
    ],
    ' ': [],  # space: no strokes, just advance
    '-': [
        [(0.1, 0.5), (0.6, 0.5)],
    ],
    '.': [
        [(0.2, 0.05), (0.2, 0), (0.3, 0), (0.3, 0.05), (0.2, 0.05)],
    ],
    '!': [
        [(0.3, 1), (0.3, 0.3)],
        [(0.3, 0.05), (0.3, 0), (0.35, 0), (0.35, 0.05), (0.3, 0.05)],
    ],
    '0': [
        [(0.3, 0), (0.7, 0), (1, 0.3), (1, 0.7), (0.7, 1), (0.3, 1), (0, 0.7), (0, 0.3), (0.3, 0)],
    ],
    '1': [
        [(0.2, 0.8), (0.5, 1), (0.5, 0)],
        [(0.2, 0), (0.8, 0)],
    ],
    '2': [
        [(0, 0.8), (0.2, 1), (0.7, 1), (0.9, 0.8), (0.9, 0.6), (0, 0), (0.9, 0)],
    ],
    '3': [
        [(0, 0.85), (0.3, 1), (0.7, 1), (0.9, 0.8), (0.9, 0.6), (0.6, 0.5)],
        [(0.6, 0.5), (0.9, 0.4), (0.9, 0.2), (0.7, 0), (0.3, 0), (0, 0.15)],
    ],
}


def get_letter_width(ch):
    """Return the normalized width of a character (0-1 scale)."""
    ch = ch.upper()
    if ch == ' ':
        return 0.5
    if ch == 'I' or ch == '!' or ch == '.' or ch == '1':
        return 0.6
    if ch == 'M' or ch == 'W':
        return 1.0
    return 0.9


def calculate_text_width(text, size, spacing):
    """Return total width of text string in mm."""
    total = 0.0
    for ch in text:
        total += size * get_letter_width(ch.upper()) + spacing
    if text:
        total -= spacing  # no trailing spacing after last character
    return total


# ── Serial / G-code ──────────────────────────────────────────────────────────
class GCodeIO:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.05)
        self.buf = bytearray()
        self.lock = threading.Lock()
        self._rx = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx.start()

    def _rx_loop(self):
        while self.ser.is_open:
            try:
                n = self.ser.in_waiting
                if n:
                    d = self.ser.read(n)
                    if d:
                        with self.lock:
                            self.buf.extend(d)
                else:
                    time.sleep(0.005)
            except:
                break

    def send(self, line, wait_ok=True, timeout=10.0):
        self.ser.write((line.strip() + "\n").encode("ascii", "ignore"))
        self.ser.flush()
        if not wait_ok:
            return
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.01)
            with self.lock:
                if OK_PAT.search(self.buf):
                    self.buf.clear()
                    return
        print(f"  ⚠ timeout waiting for ok on: {line}")

    def wait_motion(self):
        self.send("M400", wait_ok=True, timeout=30.0)

    def close(self):
        try:
            self.ser.close()
        except:
            pass


# ── Drawing primitives ────────────────────────────────────────────────────────
class Pen:
    def __init__(self, g, z_up, draw_feed):
        self.g = g
        self.z_up = z_up
        self.draw_feed = draw_feed
        self.is_up = False
        self.cursor_x = 0.0  # track relative position
        self.cursor_y = 0.0

    def up(self):
        if not self.is_up:
            self.g.send(f"G1 Z{self.z_up:.2f} F{TRAVEL_FEED}", wait_ok=True)
            self.g.wait_motion()
            self.is_up = True

    def down(self):
        if self.is_up:
            self.g.send(f"G1 Z{-self.z_up:.2f} F{TRAVEL_FEED}", wait_ok=True)
            self.g.wait_motion()
            self.is_up = False

    def move_to_abs(self, x, y):
        """Move to absolute position (relative to text start). Pen should be up."""
        dx = x - self.cursor_x
        dy = y - self.cursor_y
        if abs(dx) > 0.01 or abs(dy) > 0.01:
            self.g.send(f"G1 X{dx:.3f} Y{dy:.3f} F{TRAVEL_FEED}", wait_ok=True)
            self.g.wait_motion()
        self.cursor_x = x
        self.cursor_y = y

    def draw_to_abs(self, x, y):
        """Draw to absolute position. Pen should be down."""
        dx = x - self.cursor_x
        dy = y - self.cursor_y
        if abs(dx) > 0.01 or abs(dy) > 0.01:
            self.g.send(f"G1 X{dx:.3f} Y{dy:.3f} F{self.draw_feed}", wait_ok=True)
            self.g.wait_motion()
        self.cursor_x = x
        self.cursor_y = y


# ── Text rendering ────────────────────────────────────────────────────────────
def render_text(pen, text, size, spacing):
    """Render text string. Letters are drawn in X direction, Y is up."""
    cursor_x = 0.0

    for ch in text:
        ch_upper = ch.upper()
        strokes = FONT.get(ch_upper, None)
        if strokes is None:
            print(f"  ⚠ Unknown character '{ch}', skipping")
            cursor_x += size * 0.5 + spacing
            continue

        w = get_letter_width(ch_upper)

        if not strokes:
            # Space or empty character
            cursor_x += size * w + spacing
            continue

        print(f"  ✍ '{ch_upper}'")

        for stroke in strokes:
            if len(stroke) < 2:
                continue

            # Move to first point (pen up)
            px, py = stroke[0]
            pen.up()
            pen.move_to_abs(cursor_x + px * size, py * size)

            # Draw through remaining points
            pen.down()
            for px, py in stroke[1:]:
                pen.draw_to_abs(cursor_x + px * size, py * size)

        pen.up()
        cursor_x += size * w + spacing

    # Return to start
    pen.move_to_abs(0, 0)


# ── Sound ─────────────────────────────────────────────────────────────────────
def play_sound(filepath):
    """Play an audio file using macOS afplay (or ffplay as fallback)."""
    abs_path = filepath
    if not os.path.isabs(filepath):
        abs_path = os.path.join(SCRIPT_DIR, filepath)

    if not os.path.exists(abs_path):
        print(f"  ⚠ Sound file not found: {abs_path}")
        return

    print(f"  🔊 Playing: {os.path.basename(abs_path)}")
    try:
        subprocess.run(["afplay", abs_path], check=True, timeout=30)
    except FileNotFoundError:
        try:
            subprocess.run(["ffplay", "-nodisp", "-autoexit", abs_path],
                           check=True, timeout=30, capture_output=True)
        except Exception as e:
            print(f"  ⚠ Could not play sound: {e}")
    except Exception as e:
        print(f"  ⚠ Sound error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Write text with the Huenit robot arm")
    parser.add_argument("text", help="Text to write")
    parser.add_argument("--size", type=float, default=10.0, help="Letter height in mm (default 10)")
    parser.add_argument("--spacing", type=float, default=2.0, help="Space between letters in mm (default 2)")
    parser.add_argument("--sound", type=str, default=None, help="MP3 to play before drawing")
    parser.add_argument("--feed", type=float, default=DEFAULT_DRAW_FEED, help="Draw feed rate mm/min (default 400)")
    parser.add_argument("--line-spacing", type=float, default=1.5, help="Line height multiplier (default 1.5x letter height)")
    args = parser.parse_args()

    check_ready()

    # Load calibration
    global Z_UP
    if os.path.exists(CALIBRATION_FILE):
        with open(CALIBRATION_FILE) as f:
            cal = json.load(f)
        Z_UP = cal.get("z_up", Z_UP)
        print(f"  📐 Calibration: Z_UP = {Z_UP:.1f}mm")
    else:
        print(f"  📐 No calibration — using Z_UP = {Z_UP:.1f}mm")

    # Support \n in text (literal backslash-n or real newline)
    lines = args.text.replace('\\n', '\n').split('\n')
    line_height = args.size * args.line_spacing
    preview = args.text.replace('\n', ' / ')
    print(f"HUENIT Write — '{preview}' @ {args.size}mm | {len(lines)} line(s) | Port: {PORT}")

    # Play sound before starting
    if args.sound:
        play_sound(args.sound)

    g = GCodeIO(PORT, BAUD)
    try:
        g.send("G21", wait_ok=True)
        g.send("G91", wait_ok=True)

        total_y_moved = 0.0

        for i, line in enumerate(lines):
            line_label = f"Line {i+1}/{len(lines)}" if len(lines) > 1 else "Centering"

            if not line.strip():
                # Empty line — just advance vertically
                if i < len(lines) - 1:
                    g.send(f"G1 Y{-line_height:.3f} F{TRAVEL_FEED}", wait_ok=True)
                    g.wait_motion()
                    total_y_moved += line_height
                continue

            total_width = calculate_text_width(line, args.size, args.spacing)
            offset = total_width / 2.0
            print(f"  ↔  {line_label}: width={total_width:.1f}mm, shifting left {offset:.1f}mm")

            g.send(f"G1 X{-offset:.3f} F{TRAVEL_FEED}", wait_ok=True)
            g.wait_motion()

            pen = Pen(g, Z_UP, args.feed)
            pen.is_up = True

            print(f"  ✍ {line_label}: {line}")
            render_text(pen, line, args.size, args.spacing)

            pen.up()
            g.send(f"G1 X{offset:.3f} F{TRAVEL_FEED}", wait_ok=True)
            g.wait_motion()

            if i < len(lines) - 1:
                g.send(f"G1 Y{-line_height:.3f} F{TRAVEL_FEED}", wait_ok=True)
                g.wait_motion()
                total_y_moved += line_height

        # Return to original Y position
        if total_y_moved > 0:
            g.send(f"G1 Y{total_y_moved:.3f} F{TRAVEL_FEED}", wait_ok=True)
            g.wait_motion()

        print(f"\n  ✅ Done! (pen is up — safe to remove paper)")

    finally:
        g.send("G90", wait_ok=True)
        g.close()


if __name__ == "__main__":
    main()
