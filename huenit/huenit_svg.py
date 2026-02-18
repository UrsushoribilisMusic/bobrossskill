#!/usr/bin/env python3
"""
huenit_svg.py
-------------
Draw an SVG file with the Huenit robot arm.

Usage:
    python3 huenit_svg.py logo.svg
    python3 huenit_svg.py logo.svg --size 80   # max dimension in mm (default 80)
    python3 huenit_svg.py logo.svg --feed 250  # drawing feed rate mm/min

Supported SVG elements: <path>, <circle>, <ellipse>, <rect>, <line>,
                         <polyline>, <polygon>
All shapes should be on a flat layer (no nested transforms).
Bezier curves are approximated with line segments (CURVE_STEPS).

Requires prior calibration: python3 huenit_draw.py calibrate
"""

import sys, os, re, math, time, threading, argparse, json
import xml.etree.ElementTree as ET
import serial

# ── Config ────────────────────────────────────────────────────────────────────
PORT          = os.environ.get("HUENIT_PORT", "/dev/cu.usbserial-310")
BAUD          = 115200
TRAVEL_FEED   = 800
DEFAULT_FEED  = 250    # slower than text for detail
DEFAULT_SIZE  = 80.0   # mm, max dimension
CURVE_STEPS   = 20     # line segments per bezier curve
CIRCLE_STEPS  = 48     # line segments per full circle/ellipse

SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
CALIBRATION_FILE = os.path.join(SCRIPT_DIR, "calibration.json")
READY_FLAG       = "/tmp/huenit_ready.flag"

Z_UP        = 6.0
TILT_SLOPE  = 0.0     # mm of Z correction per mm of Y travel (loaded from calibration)

OK_PAT = re.compile(rb"\bok\b", re.I)


def check_ready():
    if not os.path.exists(READY_FLAG):
        print("  ❌ Robot not calibrated this session.")
        print("     Run:  python3 huenit_draw.py calibrate")
        sys.exit(1)


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
        self.send("M400", wait_ok=True, timeout=60.0)

    def close(self):
        try:
            self.ser.close()
        except:
            pass


# ── Bezier helpers ────────────────────────────────────────────────────────────
def cubic_bezier(p0, p1, p2, p3, steps):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3*p0[0] + 3*u**2*t*p1[0] + 3*u*t**2*p2[0] + t**3*p3[0]
        y = u**3*p0[1] + 3*u**2*t*p1[1] + 3*u*t**2*p2[1] + t**3*p3[1]
        pts.append((x, y))
    return pts


def quadratic_bezier(p0, p1, p2, steps):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**2*p0[0] + 2*u*t*p1[0] + t**2*p2[0]
        y = u**2*p0[1] + 2*u*t*p1[1] + t**2*p2[1]
        pts.append((x, y))
    return pts


# ── SVG path parser ───────────────────────────────────────────────────────────
def tokenize_path(d):
    """Split path data into command letters and number strings."""
    return re.findall(
        r'[MmLlHhVvCcQqSsTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?',
        d
    )


def parse_path_d(d):
    """
    Parse SVG path d attribute.
    Returns list of ('move'|'line', x, y) in SVG coordinate space.
    """
    tokens = tokenize_path(d)
    segments = []
    i = 0
    cmd = None
    cx, cy = 0.0, 0.0   # current position
    sx, sy = 0.0, 0.0   # subpath start
    last_ctrl = None     # for S/T smooth continuations

    def next_nums(n):
        nonlocal i
        result = []
        for _ in range(n):
            if i < len(tokens) and re.match(r'[-+]?(?:\d+\.?\d*|\.\d+)', tokens[i]):
                result.append(float(tokens[i]))
                i += 1
            else:
                result.append(0.0)
        return result

    while i < len(tokens):
        t = tokens[i]
        if re.match(r'[MmLlHhVvCcQqSsTtAaZz]', t):
            cmd = t
            i += 1
            last_ctrl = None

        if cmd is None:
            i += 1
            continue

        if cmd in ('M', 'm'):
            x, y = next_nums(2)
            if cmd == 'm':
                x += cx; y += cy
            segments.append(('move', x, y))
            sx, sy = x, y
            cx, cy = x, y
            cmd = 'L' if cmd == 'M' else 'l'

        elif cmd in ('L', 'l'):
            x, y = next_nums(2)
            if cmd == 'l':
                x += cx; y += cy
            segments.append(('line', x, y))
            cx, cy = x, y

        elif cmd in ('H', 'h'):
            x, = next_nums(1)
            if cmd == 'h':
                x += cx
            segments.append(('line', x, cy))
            cx = x

        elif cmd in ('V', 'v'):
            y, = next_nums(1)
            if cmd == 'v':
                y += cy
            segments.append(('line', cx, y))
            cy = y

        elif cmd in ('C', 'c'):
            x1, y1, x2, y2, x, y = next_nums(6)
            if cmd == 'c':
                x1 += cx; y1 += cy
                x2 += cx; y2 += cy
                x  += cx; y  += cy
            pts = cubic_bezier((cx, cy), (x1, y1), (x2, y2), (x, y), CURVE_STEPS)
            for px, py in pts[1:]:
                segments.append(('line', px, py))
            last_ctrl = (x2, y2)
            cx, cy = x, y

        elif cmd in ('S', 's'):
            x2, y2, x, y = next_nums(4)
            if cmd == 's':
                x2 += cx; y2 += cy
                x  += cx; y  += cy
            x1 = 2*cx - last_ctrl[0] if last_ctrl else cx
            y1 = 2*cy - last_ctrl[1] if last_ctrl else cy
            pts = cubic_bezier((cx, cy), (x1, y1), (x2, y2), (x, y), CURVE_STEPS)
            for px, py in pts[1:]:
                segments.append(('line', px, py))
            last_ctrl = (x2, y2)
            cx, cy = x, y

        elif cmd in ('Q', 'q'):
            x1, y1, x, y = next_nums(4)
            if cmd == 'q':
                x1 += cx; y1 += cy
                x  += cx; y  += cy
            pts = quadratic_bezier((cx, cy), (x1, y1), (x, y), CURVE_STEPS)
            for px, py in pts[1:]:
                segments.append(('line', px, py))
            last_ctrl = (x1, y1)
            cx, cy = x, y

        elif cmd in ('T', 't'):
            x, y = next_nums(2)
            if cmd == 't':
                x += cx; y += cy
            x1 = 2*cx - last_ctrl[0] if last_ctrl else cx
            y1 = 2*cy - last_ctrl[1] if last_ctrl else cy
            pts = quadratic_bezier((cx, cy), (x1, y1), (x, y), CURVE_STEPS)
            for px, py in pts[1:]:
                segments.append(('line', px, py))
            last_ctrl = (x1, y1)
            cx, cy = x, y

        elif cmd in ('A', 'a'):
            # Approximate arc as line to endpoint (good enough for simple logos)
            rx, ry, xrot, large, sweep, x, y = next_nums(7)
            if cmd == 'a':
                x += cx; y += cy
            segments.append(('line', x, y))
            cx, cy = x, y

        elif cmd in ('Z', 'z'):
            if abs(cx - sx) > 0.01 or abs(cy - sy) > 0.01:
                segments.append(('line', sx, sy))
            cx, cy = sx, sy
            cmd = None  # wait for next explicit command

    return segments


# ── SVG shape helpers ─────────────────────────────────────────────────────────
def circle_to_segments(cx, cy, r):
    """Approximate a circle as a closed polygon."""
    segs = []
    for i in range(CIRCLE_STEPS + 1):
        angle = 2 * math.pi * i / CIRCLE_STEPS
        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)
        segs.append(('move' if i == 0 else 'line', x, y))
    return segs


def ellipse_to_segments(cx, cy, rx, ry):
    segs = []
    for i in range(CIRCLE_STEPS + 1):
        angle = 2 * math.pi * i / CIRCLE_STEPS
        x = cx + rx * math.cos(angle)
        y = cy + ry * math.sin(angle)
        segs.append(('move' if i == 0 else 'line', x, y))
    return segs


def rect_to_segments(x, y, w, h):
    return [
        ('move', x,     y),
        ('line', x + w, y),
        ('line', x + w, y + h),
        ('line', x,     y + h),
        ('line', x,     y),
    ]


def polyline_to_segments(points_str, close=False):
    nums = [float(v) for v in re.findall(r'[-+]?(?:\d+\.?\d*|\.\d+)', points_str)]
    pts = list(zip(nums[0::2], nums[1::2]))
    if not pts:
        return []
    segs = [('move', pts[0][0], pts[0][1])]
    for px, py in pts[1:]:
        segs.append(('line', px, py))
    if close and len(pts) > 1:
        segs.append(('line', pts[0][0], pts[0][1]))
    return segs


# ── SVG file parser ───────────────────────────────────────────────────────────
def parse_svg(filepath):
    """Parse all drawable elements from SVG. Returns list of ('move'|'line', x, y)."""
    tree = ET.parse(filepath)
    root = tree.getroot()

    all_segments = []
    count = {'path': 0, 'circle': 0, 'other': 0}

    for elem in root.iter():
        # Strip namespace
        tag = elem.tag
        if '}' in tag:
            tag = tag.split('}', 1)[1]

        if tag == 'path':
            d = elem.get('d', '')
            if d:
                segs = parse_path_d(d)
                if segs:
                    all_segments.extend(segs)
                    count['path'] += 1

        elif tag == 'circle':
            cx = float(elem.get('cx', 0))
            cy = float(elem.get('cy', 0))
            r  = float(elem.get('r',  0))
            if r > 0:
                all_segments.extend(circle_to_segments(cx, cy, r))
                count['circle'] += 1

        elif tag == 'ellipse':
            cx = float(elem.get('cx', 0))
            cy = float(elem.get('cy', 0))
            rx = float(elem.get('rx', 0))
            ry = float(elem.get('ry', 0))
            if rx > 0 and ry > 0:
                all_segments.extend(ellipse_to_segments(cx, cy, rx, ry))
                count['other'] += 1

        elif tag == 'rect':
            x = float(elem.get('x', 0))
            y = float(elem.get('y', 0))
            w = float(elem.get('width',  0))
            h = float(elem.get('height', 0))
            if w > 0 and h > 0:
                all_segments.extend(rect_to_segments(x, y, w, h))
                count['other'] += 1

        elif tag == 'line':
            x1 = float(elem.get('x1', 0))
            y1 = float(elem.get('y1', 0))
            x2 = float(elem.get('x2', 0))
            y2 = float(elem.get('y2', 0))
            all_segments.extend([('move', x1, y1), ('line', x2, y2)])
            count['other'] += 1

        elif tag == 'polyline':
            pts = elem.get('points', '')
            if pts:
                all_segments.extend(polyline_to_segments(pts, close=False))
                count['other'] += 1

        elif tag == 'polygon':
            pts = elem.get('points', '')
            if pts:
                all_segments.extend(polyline_to_segments(pts, close=True))
                count['other'] += 1

    total = count['path'] + count['circle'] + count['other']
    print(f"  📄 Parsed {total} element(s): {count['path']} path(s), "
          f"{count['circle']} circle(s), {count['other']} other")

    return all_segments


# ── Scale and center ──────────────────────────────────────────────────────────
def transform_segments(segments, size_mm):
    """
    Scale to fit within size_mm, center at (0,0), flip Y axis
    (SVG Y is down; arm Y is up).
    """
    xs = [x for _, x, _ in segments]
    ys = [y for _, _, y in segments]
    if not xs:
        return []

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    w = x_max - x_min
    h = y_max - y_min

    if max(w, h) == 0:
        return []

    scale    = size_mm / max(w, h)
    x_center = (x_min + x_max) / 2
    y_center = (y_min + y_max) / 2

    result = []
    for kind, x, y in segments:
        arm_x =  (x - x_center) * scale
        arm_y = -(y - y_center) * scale   # flip Y
        result.append((kind, arm_x, arm_y))

    print(f"  📐 SVG size: {w:.1f}×{h:.1f}px → {w*scale:.1f}×{h*scale:.1f}mm "
          f"(scale {scale:.3f})")
    return result


# ── Draw ──────────────────────────────────────────────────────────────────────
def draw_segments(g, segments, z_up, draw_feed):
    """Execute segments as G-code. Pen starts and ends UP at (0,0)."""
    is_up  = True
    cur_x  = 0.0
    cur_y  = 0.0
    moves  = 0
    lines  = 0

    for kind, x, y in segments:
        dx = x - cur_x
        dy = y - cur_y

        if kind == 'move':
            if not is_up:
                g.send(f"G1 Z{z_up:.2f} F{TRAVEL_FEED}", wait_ok=True)
                g.wait_motion()
                is_up = True
            if abs(dx) > 0.01 or abs(dy) > 0.01:
                dz = TILT_SLOPE * dy
                z_comp = f" Z{dz:.3f}" if abs(dz) > 0.001 else ""
                g.send(f"G1 X{dx:.3f} Y{dy:.3f}{z_comp} F{TRAVEL_FEED}", wait_ok=True)
                g.wait_motion()
            moves += 1

        elif kind == 'line':
            if is_up:
                g.send(f"G1 Z{-z_up:.2f} F{TRAVEL_FEED}", wait_ok=True)
                g.wait_motion()
                is_up = False
            if abs(dx) > 0.01 or abs(dy) > 0.01:
                dz = TILT_SLOPE * dy
                z_comp = f" Z{dz:.3f}" if abs(dz) > 0.001 else ""
                g.send(f"G1 X{dx:.3f} Y{dy:.3f}{z_comp} F{draw_feed}", wait_ok=True)
                g.wait_motion()
            lines += 1

        cur_x, cur_y = x, y

    # Lift pen
    if not is_up:
        g.send(f"G1 Z{z_up:.2f} F{TRAVEL_FEED}", wait_ok=True)
        g.wait_motion()

    # Return to center (0, 0)
    dx = -cur_x
    dy = -cur_y
    if abs(dx) > 0.01 or abs(dy) > 0.01:
        dz = TILT_SLOPE * dy
        z_comp = f" Z{dz:.3f}" if abs(dz) > 0.001 else ""
        g.send(f"G1 X{dx:.3f} Y{dy:.3f}{z_comp} F{TRAVEL_FEED}", wait_ok=True)
        g.wait_motion()

    print(f"  📊 {moves} pen-up moves, {lines} draw moves")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Draw an SVG with the Huenit robot arm")
    parser.add_argument("svg", help="Path to SVG file")
    parser.add_argument("--size", type=float, default=DEFAULT_SIZE,
                        help=f"Max dimension in mm (default {DEFAULT_SIZE})")
    parser.add_argument("--feed", type=float, default=DEFAULT_FEED,
                        help=f"Drawing feed rate mm/min (default {DEFAULT_FEED})")
    args = parser.parse_args()

    if not os.path.exists(args.svg):
        print(f"  ❌ SVG file not found: {args.svg}")
        sys.exit(1)

    check_ready()

    global Z_UP, TILT_SLOPE
    if os.path.exists(CALIBRATION_FILE):
        with open(CALIBRATION_FILE) as f:
            cal = json.load(f)
        Z_UP       = cal.get("z_up", Z_UP)
        TILT_SLOPE = cal.get("tilt_slope", 0.0)
        tilt_info  = f", tilt={TILT_SLOPE:.4f} mm/mm" if TILT_SLOPE != 0 else ""
        print(f"  📐 Calibration: Z_UP = {Z_UP:.1f}mm{tilt_info}")

    print(f"HUENIT SVG — {os.path.basename(args.svg)} @ max {args.size}mm | Port: {PORT}")

    segments = parse_svg(args.svg)
    if not segments:
        print("  ❌ No drawable elements found in SVG.")
        sys.exit(1)

    segments = transform_segments(segments, args.size)

    g = GCodeIO(PORT, BAUD)
    try:
        g.send("G21", wait_ok=True)
        g.send("G91", wait_ok=True)
        draw_segments(g, segments, Z_UP, args.feed)
        print("\n  ✅ Done! (pen is up — safe to remove paper)")
    finally:
        g.send("G90", wait_ok=True)
        g.close()


if __name__ == "__main__":
    main()
