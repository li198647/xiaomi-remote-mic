# -*- coding: utf-8 -*-
"""实测：低级键盘钩子(WH_KEYBOARD_LL)回调触发的那一刻，Raw Input 的数据
**排队**了没有？

为什么要测这个
--------------
要把「遥控器 OK 键」变成「Ctrl+Enter」，必须先知道**这一下 Enter 是遥控器按的
还是实体键盘按的**。这���信息只有 Raw Input 有（带 hDevice），低级钩子没有。

而两条链路的到达顺序是实测出来的：**钩子同步先跑，WM_INPUT 异步后处理**。
于是有个关键问题：

  * 如果在钩子回调里 Raw Input 的数据**已经排队**（只是还没派发），
    那我们可以在钩子里同步查到设备身份 -> 方案很干净：
    「这一下是遥控器 -> 吞掉 + 发 Ctrl+Enter；是实体键盘 -> 原样放行」。
  * 如果还没排队，就只能走「先吞掉 -> 异步确认 -> 确认不是遥控器就补发按键」
    的复杂路线。

用 SendInput 自己注入一个键来测：注入和物理按键走同一条系统输入通路，
所以顺序问题一样，而且不用人按。

用法：
    python probe_raw_vs_hook.py
"""
import ctypes
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_mivoice() -> str:
    """自适应定位 mivoice 目录（本脚本有两个合法位置，两边都要能跑）：

      · <项目>\\tools\\          —— 放在仓库外面时（本机原来的位置）
      · <项目>\\mivoice\\tools\\  —— 仓库自带，clone 下来就在这儿
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.dirname(here)):
        if os.path.isfile(os.path.join(cand, "mivoice.py")):
            return cand
        sub = os.path.join(cand, "mivoice")
        if os.path.isfile(os.path.join(sub, "mivoice.py")):
            return sub
    return os.path.join(os.path.dirname(here), "mivoice")


MIVOICE_DIR = _find_mivoice()
sys.path.insert(0, MIVOICE_DIR)

import mivoice as M  # noqa: E402  复用它的 Win32 声明

u = M.user32
k = M.kernel32
wt = M.wt

WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEKEYBOARD = 1
RAWHDR_SIZE = 24          # x64 下 sizeof(RAWINPUTHEADER)

u.GetRawInputBuffer.restype = wt.UINT
u.GetRawInputBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.UINT), wt.UINT]

TRACE = []
T0 = time.perf_counter()
LOCK = threading.Lock()


def stamp():
    return (time.perf_counter() - T0) * 1000.0


def rec(row):
    with LOCK:
        TRACE.append(row)


def parse_buffer(buf, total):
    """把 GetRawInputBuffer 的内容拆成一条条记录。"""
    out = []
    off = 0
    raw = buf.raw
    while off + 8 <= total:
        dwtype = int.from_bytes(raw[off:off + 4], "little")
        dwsz = int.from_bytes(raw[off + 4:off + 8], "little")
        hdev = int.from_bytes(raw[off + 8:off + 16], "little")
        mk = vk = None
        if dwtype == RIM_TYPEKEYBOARD and off + 32 <= total:
            mk = int.from_bytes(raw[off + 24:off + 26], "little")
            vk = int.from_bytes(raw[off + 30:off + 32], "little")
        out.append({"type": dwtype, "hdev": hdev, "scan": mk, "vk": vk})
        if dwsz <= 0:
            break
        off += dwsz
    return out


HOOKPROC = ctypes.WINFUNCTYPE(M.LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


def _hook(nc, wp, lp):
    if nc == 0:
        kb = ctypes.cast(lp, ctypes.POINTER(M.KBDLLHOOKSTRUCT)).contents
        if wp in (WM_KEYDOWN, WM_SYSKEYDOWN):
            # ★ 关键：在钩子回调里立刻查 Raw Input 队列
            size = wt.UINT(0)
            got = u.GetRawInputBuffer(None, ctypes.byref(size), RAWHDR_SIZE)
            items = []
            if size.value:
                buf = ctypes.create_string_buffer(size.value)
                got = u.GetRawInputBuffer(buf, ctypes.byref(size), RAWHDR_SIZE)
                items = parse_buffer(buf, got)
            rec(("HOOK", stamp(), "vk=0x%02X scan=0x%02X flags=0x%X" %
                 (kb.vkCode, kb.scanCode, kb.flags),
                 "buffer_get=%s size=%s items=%s" %
                 (got, size.value, [(i["type"], hex(i["hdev"]), i["vk"]) for i in items])))
    return u.CallNextHookEx(None, nc, wp, lp)


_hook_ref = HOOKPROC(_hook)


def _wndproc(hwnd, msg, wp, lp):
    if msg == WM_INPUT:
        try:
            size = wt.UINT(0)
            hraw = wt.HANDLE(lp)
            if u.GetRawInputData(hraw, M.RID_INPUT, None, ctypes.byref(size), 24) != 0 \
                    and size.value:
                buf = ctypes.create_string_buffer(size.value)
                if u.GetRawInputData(hraw, M.RID_INPUT, buf, ctypes.byref(size), 24) != 0xFFFFFFFF:
                    raw = buf.raw
                    dt = int.from_bytes(raw[0:4], "little")
                    hdev = int.from_bytes(raw[8:16], "little")
                    mk = int.from_bytes(raw[24:26], "little")
                    vk = int.from_bytes(raw[30:32], "little")
                    rec(("WM_INPUT", stamp(), "type=%s vk=0x%02X scan=0x%02X" % (dt, vk, mk),
                         "hdev=0x%X" % hdev))
        except Exception as e:
            rec(("WM_INPUT-ERR", stamp(), repr(e), ""))
        return 0
    return u.DefWindowProcW(hwnd, msg, wp, lp)


_wndproc_ref = M.WNDPROC(_wndproc)


def main():
    hinst = k.GetModuleHandleW(None)
    wc = M.WNDCLASSW()
    wc.lpfnWndProc = _wndproc_ref
    wc.hInstance = hinst
    wc.lpszClassName = "ProbeRawWnd"
    u.RegisterClassW(ctypes.byref(wc))
    hwnd = u.CreateWindowExW(0, "ProbeRawWnd", "ProbeRaw", 0, 0, 0, 0, 0,
                              None, None, hinst, None)
    if not hwnd:
        print("窗口创建失败")
        return 2

    rid = M.RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, hwnd)
    ok = u.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(M.RAWINPUTDEVICE))
    print("Raw Input 注册:", "成功" if ok else "失败")

    hook = u.SetWindowsHookExW(M.WH_KEYBOARD_LL, _hook_ref, hinst, 0)
    print("低级钩子安装:", "成功" if hook else "失败 err=%s" % ctypes.get_last_error())

    def injector():
        time.sleep(1.2)
        rec(("INJECT", stamp(), "SendInput F13", ""))
        # 注入一个冷门键，避开其它程序
        inp = M.INPUT()
        inp.type = M.INPUT_KEYBOARD
        inp.ki = M.KEYBDINPUT(0x7D, 0x64, 0, 0, None)   # F13
        u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(M.INPUT))
        time.sleep(0.05)
        inp.ki = M.KEYBDINPUT(0x7D, 0x64, M.KEYEVENTF_KEYUP, 0, None)
        u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(M.INPUT))

    th = threading.Thread(target=injector, daemon=True)
    th.start()

    deadline = time.time() + 3.0
    msg = wt.MSG()
    while time.time() < deadline:
        while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)

    u.UnhookWindowsHookEx(hook)

    print()
    print("=" * 78)
    print("时间线（毫秒，从程序启动算起）")
    print("=" * 78)
    for row in TRACE:
        print("  %8.2f ms  %-10s %-42s %s" % (row[1], row[0], row[2], row[3]))

    print()
    hooks = [r for r in TRACE if r[0] == "HOOK"]
    wins = [r for r in TRACE if r[0] == "WM_INPUT"]
    if hooks and wins:
        gap = wins[0][1] - hooks[0][1]
        print("钩子 -> WM_INPUT 间隔: %.2f ms" % gap)
        inbuf = "有" if "items=[]" not in hooks[0][3] else "空"
        print("钩子回调时 Raw Input 缓冲区: %s" % inbuf)
        print()
        if inbuf == "有":
            print("==> 结论：钩子里就能同步查到设备身份，可以做「精确判定」方案")
        else:
            print("==> 结论：钩子里查不到设备，只能「先吞后确认 + 补发」方案")
    else:
        print("（没抓到事件，可能注入没生效）")


if __name__ == "__main__":
    main()
