#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Observe a real MiVoice session: Alt state, foreground window and CABLE audio."""
import array
import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_cable_loopback as cable

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
OUT = os.path.join(HERE, "diagnose_mivoice_last.txt")
lines = []


def emit(msg):
    line = "%8.3f  %s" % (time.time() - t0, msg)
    lines.append(line)
    print(line, flush=True)


def foreground():
    h = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, buf, 512)
    return int(h or 0), buf.value


def pressed(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


t0 = time.time()
dev, name = cable.find_in("CABLE Output")
if dev is None:
    raise SystemExit("CABLE Output not found")
box = {}
th = threading.Thread(target=cable.rec, args=(dev, DURATION, box), daemon=True)
th.start()
emit("monitor started; record device=%s" % name)
last = None
while time.time() - t0 < DURATION:
    h, title = foreground()
    state = (pressed(0x12), pressed(0xA4), h, title)
    if state != last:
        emit("Alt=%d LAlt=%d foreground=%d %r" % state)
        last = state
    time.sleep(0.05)
th.join()
emit("recorder finished")
if box.get("e_rec"):
    emit(box["e_rec"])
data = box.get("data", b"")
rms, peak = cable.stats(data)
emit("audio overall: bytes=%d RMS=%d peak=%d" % (len(data), rms, peak))
# Report 0.5-second windows that contain non-trivial signal.
window = int(cable.RATE * cable.BLOCK * 0.5)
for pos in range(0, len(data), window):
    chunk = data[pos:pos + window]
    if len(chunk) < 2:
        continue
    r, p = cable.stats(chunk)
    if p > 100:
        emit("audio %6.2fs: RMS=%5d peak=%5d" % (pos / float(cable.RATE * cable.BLOCK), r, p))
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("saved: " + OUT, flush=True)
