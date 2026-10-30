#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_alt_hold.py — 模拟「按住 Alt」的几种发送方式，验证哪种能让 WorkBuddy 持续响应

背景：MiVoice 用 SendInput 发 VK_MENU(wScan=0) 来「按住」Alt 触发 WorkBuddy 语音输入，
      但 WorkBuddy(Electron/Chromium) 可能识别不了，表现为「只绿一下就停」。

本脚本会：
  1. 自动把 WorkBuddy 窗口切到前台（标题含 WorkBuddy 即可）
  2. 倒计时 3 秒让你准备
  3. 用指定方式「按住 Ctrl」——注意默认用 Ctrl 做安全试探（不会触发菜单）
     也可 --key alt 真按 Alt

用法：
  python test_alt_hold.py                 # 用 VK_LMENU + 扫描码 0x38 按住 Alt 5 秒
  python test_alt_hold.py --key ctrl      # 改用 Ctrl（安全，不会弹菜单）
  python test_alt_hold.py --mode plain    # 老方式：VK_MENU + wScan=0
  python test_alt_hold.py --mode lmenu    # VK_LMENU + wScan=0x38（推荐）
  python test_alt_hold.py --mode scancode # 纯扫描码 + KEYEVENTF_SCANCODE
  python test_alt_hold.py --secs 8        # 按住 8 秒

观察方法：跑起来后盯着 WorkBuddy 的麦克风指示
  持续亮/变绿直到倒计时结束 → 该方式有效
  只闪一下 → 该方式无效
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

VK_MENU = 0x12
VK_LMENU = 0xA4
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
SCAN_ALT = 0x38
SCAN_CTRL = 0x1D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


def send(vk, scan, flags):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = scan
    inp.u.ki.dwFlags = flags
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = None
    n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    return n == 1


def find_window(part):
    part = part.lower()
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if part in buf.value.lower():
            found.append((hwnd, buf.value))
            return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else (None, None)


def key_params(key, mode):
    """返回 (按下参数, 抬起参数)，每个都是 (vk, scan, flags)"""
    if key == "alt":
        if mode == "plain":
            return (VK_MENU, 0, 0), (VK_MENU, 0, KEYEVENTF_KEYUP)
        if mode == "lmenu":
            return (VK_LMENU, SCAN_ALT, 0), (VK_LMENU, SCAN_ALT, KEYEVENTF_KEYUP)
        if mode == "scancode":
            return (0, SCAN_ALT, KEYEVENTF_SCANCODE), \
                   (0, SCAN_ALT, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
    if key == "ctrl":
        if mode == "plain":
            return (VK_CONTROL, 0, 0), (VK_CONTROL, 0, KEYEVENTF_KEYUP)
        if mode == "lmenu":
            return (VK_LCONTROL, SCAN_CTRL, 0), (VK_LCONTROL, SCAN_CTRL, KEYEVENTF_KEYUP)
        if mode == "scancode":
            return (0, SCAN_CTRL, KEYEVENTF_SCANCODE), \
                   (0, SCAN_CTRL, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)
    raise SystemExit("未知组合: key=%s mode=%s" % (key, mode))


def main(argv):
    key = "alt"
    if "--key" in argv:
        key = argv[argv.index("--key") + 1].lower()
    mode = "lmenu"
    if "--mode" in argv:
        mode = argv[argv.index("--mode") + 1].lower()
    secs = 5.0
    if "--secs" in argv:
        secs = float(argv[argv.index("--secs") + 1])

    down_p, up_p = key_params(key, mode)

    print("=" * 60)
    print("按键: %s   发送方式: %s" % (key.upper(), mode))
    print("按下参数: vk=0x%02X scan=0x%02X flags=0x%X" % down_p)
    print("时长: %.1f 秒" % secs)
    print("=" * 60)

    hwnd, title = find_window("workbuddy")
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        print("已切到前台: %s" % title[:50])
    else:
        print("! 没找到 WorkBuddy 窗口，将在当前焦点窗口上按键")

    for i in range(3, 0, -1):
        print("  %d 秒后开始按住……" % i, end="\r")
        sys.stdout.flush()
        time.sleep(1)

    print("\n>>> 现在开始按住，盯着 WorkBuddy 的麦克风！")
    ok = send(*down_p)
    if not ok:
        print("SendInput 按下失败 err=%d" % ctypes.get_last_error())
        return 1

    t0 = time.time()
    try:
        while time.time() - t0 < secs:
            left = secs - (time.time() - t0)
            bar = "#" * int(30 * (1 - left / secs))
            print("  按住中 %4.1fs  [%-30s]" % (left, bar), end="\r")
            sys.stdout.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass

    send(*up_p)
    print("\n<<< 已松开")
    print("=" * 60)
    print("如果你刚才看到 WorkBuddy 的麦克风【持续】亮着 %.0f 秒 → 这种方式有效" % secs)
    print("如果只闪了一下 → 这种方式无效，换 --mode 再试")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
