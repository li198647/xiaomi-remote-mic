#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_cable_loopback.py — VB-CABLE 虚拟声卡环回自检（纯 ctypes，零依赖）

原理：往「CABLE Input」（播放端）灌 1kHz 正弦波，
     同时从「CABLE Output」（录音端）录回来，比对电平。
     录到清晰正弦波 = 虚拟声卡链路健康。

用法：
  python test_cable_loopback.py            # 标准测试（48k 立体声，1kHz）
  python test_cable_loopback.py --list     # 列出播放/录音设备

输出判读：
  录制峰值 > 2000  → 链路健康
  录制峰值 < 200   → 链路断了（驱动/独占/静音）
"""

import array
import ctypes
import ctypes.wintypes as wt
import math
import struct
import sys
import threading
import time

winmm = ctypes.WinDLL("winmm", use_last_error=True)

CALLBACK_NULL = 0
WAVE_FORMAT_PCM = 1
WHDR_DONE = 0x00000001

RATE = 48000
CHANNELS = 2
BITS = 16
BLOCK = CHANNELS * BITS // 8


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wt.WORD), ("nChannels", wt.WORD),
        ("nSamplesPerSec", wt.DWORD), ("nAvgBytesPerSec", wt.DWORD),
        ("nBlockAlign", wt.WORD), ("wBitsPerSample", wt.WORD),
        ("cbSize", wt.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p), ("dwBufferLength", wt.DWORD),
        ("dwBytesRecorded", wt.DWORD), ("dwUser", ctypes.c_void_p),
        ("dwFlags", wt.DWORD), ("dwLoops", wt.DWORD),
        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_void_p),
    ]


class WAVEOUTCAPS(ctypes.Structure):
    _fields_ = [
        ("wMid", wt.WORD), ("wPid", wt.WORD), ("vDriverVersion", wt.UINT),
        ("szPname", wt.WCHAR * 32), ("dwFormats", wt.DWORD),
        ("wChannels", wt.WORD), ("wReserved1", wt.WORD),
        ("dwSupport", wt.DWORD),
    ]


class WAVEINCAPS(ctypes.Structure):
    _fields_ = [
        ("wMid", wt.WORD), ("wPid", wt.WORD), ("vDriverVersion", wt.UINT),
        ("szPname", wt.WCHAR * 32), ("dwFormats", wt.DWORD),
        ("wChannels", wt.WORD), ("wReserved1", wt.WORD),
    ]


def make_fmt():
    f = WAVEFORMATEX()
    f.wFormatTag = WAVE_FORMAT_PCM
    f.nChannels = CHANNELS
    f.nSamplesPerSec = RATE
    f.wBitsPerSample = BITS
    f.nBlockAlign = BLOCK
    f.nAvgBytesPerSec = RATE * BLOCK
    f.cbSize = 0
    return f


def list_out():
    out = []
    for i in range(winmm.waveOutGetNumDevs()):
        c = WAVEOUTCAPS()
        rc = winmm.waveOutGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c))
        out.append((i, rc, c.szPname))
    return out


def list_in():
    out = []
    for i in range(winmm.waveInGetNumDevs()):
        c = WAVEINCAPS()
        rc = winmm.waveInGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c))
        out.append((i, rc, c.szPname))
    return out


def find_out(keyword):
    kw = keyword.lower()
    for i, rc, name in list_out():
        if rc == 0 and kw in name.lower():
            return i, name
    return None, None


def find_in(keyword):
    kw = keyword.lower()
    for i, rc, name in list_in():
        if rc == 0 and kw in name.lower():
            return i, name
    return None, None


def tone_bytes(seconds, freq=1000, amp=8000):
    frames = int(RATE * seconds)
    out = bytearray()
    for i in range(frames):
        v = int(amp * math.sin(2 * math.pi * freq * i / RATE))
        out += struct.pack("<hh", v, v)
    return bytes(out)


def play(dev, data, box):
    h = wt.HANDLE()
    fmt = make_fmt()
    rc = winmm.waveOutOpen(ctypes.byref(h), dev, ctypes.byref(fmt), 0, 0, CALLBACK_NULL)
    if rc != 0:
        box["e"] = "waveOutOpen 失败 rc=%d" % rc
        return
    buf = ctypes.create_string_buffer(data, len(data))
    hdr = WAVEHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p).value
    hdr.dwBufferLength = len(data)
    winmm.waveOutPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    winmm.waveOutWrite(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    t0 = time.time()
    while not (hdr.dwFlags & WHDR_DONE) and time.time() - t0 < 12:
        time.sleep(0.02)
    winmm.waveOutUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    winmm.waveOutClose(h)


def rec(dev, seconds, box):
    h = wt.HANDLE()
    fmt = make_fmt()
    rc = winmm.waveInOpen(ctypes.byref(h), dev, ctypes.byref(fmt), 0, 0, CALLBACK_NULL)
    if rc != 0:
        box["e_rec"] = "waveInOpen 失败 rc=%d" % rc
        return
    total = int(RATE * BLOCK * seconds)
    buf = ctypes.create_string_buffer(total)
    hdr = WAVEHDR()
    hdr.lpData = ctypes.cast(buf, ctypes.c_void_p).value
    hdr.dwBufferLength = total
    winmm.waveInPrepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    winmm.waveInAddBuffer(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    winmm.waveInStart(h)
    t0 = time.time()
    while not (hdr.dwFlags & WHDR_DONE) and time.time() - t0 < seconds + 6:
        time.sleep(0.05)
    winmm.waveInStop(h)
    winmm.waveInReset(h)
    got = hdr.dwBytesRecorded or total
    box["data"] = bytes(buf.raw[:min(got, total)])
    winmm.waveInUnprepareHeader(h, ctypes.byref(hdr), ctypes.sizeof(hdr))
    winmm.waveInClose(h)


def stats(data):
    n = len(data) // 2
    if n == 0:
        return 0, 0
    a = array.array("h")
    a.frombytes(data[:n * 2])
    peak = max(max(a), -min(a))
    rms = int(math.sqrt(sum(v * v for v in a) / float(n)))
    return rms, peak


def main(argv):
    if "--list" in argv:
        print("播放设备（waveOut）：")
        for i, rc, name in list_out():
            print("  [%d] rc=%d  %r" % (i, rc, name))
        print("录音设备（waveIn）：")
        for i, rc, name in list_in():
            print("  [%d] rc=%d  %r" % (i, rc, name))
        return 0

    print("=" * 62)
    oi, oname = find_out("CABLE Input")
    ii, iname = find_in("CABLE Output")
    if oi is None:
        print("可用播放设备：")
        for i, rc, n in list_out():
            print("  [%d] rc=%d %r" % (i, rc, n))
        return 1
    if ii is None:
        print("可用录音设备：")
        for i, rc, n in list_in():
            print("  [%d] rc=%d %r" % (i, rc, n))
        return 1
    print("播放端: [%d] %s" % (oi, oname))
    print("录音端: [%d] %s" % (ii, iname))
    print("格式  : %d Hz / %d 声道 / %d bit" % (RATE, CHANNELS, BITS))
    print("=" * 62)

    secs = 2.0
    tone = tone_bytes(secs, 1000, 8000)
    box = {}

    print("正在灌入 1kHz 测试音并从对端录回……")
    th_rec = threading.Thread(target=rec, args=(ii, secs + 1.0, box))
    th_rec.start()
    time.sleep(0.2)
    th_play = threading.Thread(target=play, args=(oi, tone, box))
    th_play.start()
    th_rec.join()
    th_play.join()

    err = box.get("e_rec") or box.get("e")
    if err:
        print("失败: %s" % err)
        return 1
    data = box.get("data")
    if not data:
        print("失败: 没有录到数据")
        return 1

    rms, peak = stats(data)
    print("\n" + "=" * 62)
    print("录制结果  RMS: %d   峰值: %d   (发送峰值 8000)" % (rms, peak))
    if peak > 2000:
        print("结论: 【链路健康】虚拟声卡能正常把声音从 Input 传到 Output")
    elif peak > 200:
        print("结论: 【信号弱】有信号但衰减严重，可能音量被调低")
    else:
        print("结论: 【链路断开】Input 灌进去的声音没有出现在 Output")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
