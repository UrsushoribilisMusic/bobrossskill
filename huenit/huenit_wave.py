#!/usr/bin/env python3
"""
huenit_wave.py
--------------
Scripted movement: up/down → right → up/down → left (back to start).
Uses relative G-code moves over serial.
"""

import sys, time, re, threading, os
import serial

PORT = os.environ.get("HUENIT_PORT", "/dev/cu.usbserial-310")
BAUD = 115200
FEED = 800        # mm/min — moderate speed
MOVE_MM = 30.0    # distance per move segment

OK_PAT = re.compile(rb"\bok\b", re.I)


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

    def close(self):
        try:
            self.ser.close()
        except:
            pass


def move(g, **axes):
    """Send a relative G1 move and wait for completion."""
    parts = " ".join(f"{k}{v:+.2f}" for k, v in axes.items())
    cmd = f"G1 {parts} F{FEED}"
    print(f"  → {cmd}")
    g.send(cmd, wait_ok=True)
    g.send("M400", wait_ok=True, timeout=15.0)  # wait for motion to finish


def main():
    d = MOVE_MM
    print(f"HUENIT Wave — Port: {PORT} | Move: {d} mm | Feed: {FEED} mm/min")

    g = GCodeIO(PORT, BAUD)
    try:
        # Setup: metric, relative mode
        g.send("G21", wait_ok=True)
        g.send("G91", wait_ok=True)

        print("\n1) Up")
        move(g, Z=d)

        print("2) Down")
        move(g, Z=-d)

        print("3) Right")
        move(g, X=d)

        print("4) Up")
        move(g, Z=d)

        print("5) Down")
        move(g, Z=-d)

        print("6) Left (back to start)")
        move(g, X=-d)

        print("\n✅ Done — arm back at starting position.")

    finally:
        g.send("G90", wait_ok=True)  # back to absolute
        g.close()


if __name__ == "__main__":
    main()
