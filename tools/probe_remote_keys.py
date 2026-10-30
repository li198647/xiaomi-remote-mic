#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Log every keyboard event that reaches Windows while the remote voice key is used."""
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

LLKHF_INJECTED = 0x10


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
OUT = sys.argv[2] if len(sys.argv) > 2 else "probe_remote_keys_last.txt"
lines = []
t0 = time.time()
hook_handle = None


def emit(msg):
    line = "%8.3f  %s" % (time.time() - t0, msg)
    lines.append(line)
    print(line, flush=True)


def alt_state():
    generic = bool(user32.GetAsyncKeyState(0x12) & 0x8000)
    left = bool(user32.GetAsyncKeyState(0xA4) & 0x8000)
    return "Alt=%d/LAlt=%d" % (generic, left)


def proc(code, wparam, lparam):
    if code == HC_ACTION:
        kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        kind = {WM_KEYDOWN: "down", WM_KEYUP: "up",
                WM_SYSKEYDOWN: "sysdown", WM_SYSKEYUP: "sysup"}.get(wparam, str(wparam))
        inject = "INJ" if (kb.flags & LLKHF_INJECTED) else "HW "
        emit("key %-6s vk=0x%02X scan=0x%02X flags=0x%X %s  %s"
             % (kind, kb.vkCode, kb.scanCode, kb.flags, inject, alt_state()))
    return user32.CallNextHookEx(hook_handle, code, wparam, lparam)


def main():
    global hook_handle
    thread_id = kernel32.GetCurrentThreadId()
    hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, HOOKPROC(proc),
                                           None, 0)
    if not hook_handle:
        raise SystemExit("hook failed err=%d" % ctypes.get_last_error())
    emit("hook installed, monitoring %.0f s" % DURATION)
    msg = wt.MSG()
    end = time.time() + DURATION
    while time.time() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == WM_KEYDOWN:
                pass
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)
    user32.UnhookWindowsHookEx(hook_handle)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    emit("saved " + OUT)


if __name__ == "__main__":
    main()
