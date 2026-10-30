"""探针：量化「Raw Input 事件在高频按键下会丢多少」。

🔴 为什么要有这个探针（2026-10-30 实测发现的硬伤）：

   MiVoice 主程序里，遥控器的语音键(F5) 被按住时，系统自动重复约 30 次/秒。
   实测：按住 8.84 秒（约 265 次重复），主日志按 1 秒节流**只记到 1 条**
   「Raw 遥控器语音键 vk=0x74 到达」—— 也就是说 **Raw Input 事件大面积丢失**。

   而遥控器的 OK 键(Enter) 恰好是在「说完话之后」按的，
   正好落在事件丢失的高峰期 -> 设备归属永远判不出来。

   这个探针要量化两件事：
     1. 注入的按键（SendInput）会不会产生 Raw Input 回声？hDevice 是多少？
        （实测：会，hDevice = 0）—— 这一点直接决定「注入回声会不会污染
          看门狗兜底判据」，是个必须钉死的事实。
     2. 在 N 次/秒的按键洪水下，WM_INPUT 消息的**投递率**和
        **GetRawInputData 成功率**分别是多少。

   用法：双击 tools\\probe-raw-flood.bat
        或  python tools\\probe_raw_flood.py
"""

import ctypes as C
import ctypes.wintypes as wt
import sys
import time
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

user32 = C.WinDLL("user32", use_last_error=True)
kernel32 = C.WinDLL("kernel32", use_last_error=True)

WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEKEYBOARD = 1
RIDEV_INPUTSINK = 0x00000100
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

LPULONG_PTR = C.POINTER(wt.ULONG)
LRESULT = C.c_longlong if C.sizeof(C.c_void_p) == 8 else C.c_long
WNDPROC = C.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class RAWINPUTDEVICE(C.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", LPULONG_PTR)]


class INPUTunion(C.Structure):
    _fields_ = [("ki", KEYBDINPUT), ("padding", C.c_byte * 24)]


class INPUT(C.Structure):
    _fields_ = [("type", wt.DWORD), ("u", INPUTunion)]


class WNDCLASSW(C.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HANDLE),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


user32.RegisterClassW.argtypes = [C.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   C.c_int, C.c_int, C.c_int, C.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterRawInputDevices.argtypes = [C.POINTER(RAWINPUTDEVICE),
                                           wt.UINT, wt.UINT]
user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID,
                                   C.POINTER(wt.UINT), wt.UINT]
user32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID,
                                          C.POINTER(wt.UINT)]
user32.SendInput.argtypes = [wt.UINT, C.POINTER(INPUT), C.c_int]
user32.SendInput.restype = wt.UINT


def struct_unpack_h(buf, off):
    return int.from_bytes(buf[off:off + 2], "little")


def struct_unpack_i(buf, off):
    return int.from_bytes(buf[off:off + 4], "little")


def struct_unpack_q(buf, off):
    return int.from_bytes(buf[off:off + 8], "little")


# ---------------- 统计 ----------------
STATS = {
    "wm_input": 0,          # 收到 WM_INPUT 消息数
    "kbd": 0,               # dwType == 键盘
    "data_ok": 0,           # GetRawInputData 成功
    "data_fail": 0,         # GetRawInputData 失败（缓冲区被覆盖 -> 事件丢失）
    "hdev0": 0,             # hDevice == 0（注入回声）
    "hdev_real": 0,         # hDevice != 0
    "down": 0, "up": 0,
}
VKEY_COUNT = {}
NAMES = {}


def dev_name(hdev):
    if hdev in NAMES:
        return NAMES[hdev]
    size = wt.UINT(0)
    user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME, None,
                                  C.byref(size))
    nm = ""
    if size.value:
        buf = C.create_unicode_buffer(size.value + 1)
        if user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME, buf,
                                         C.byref(size)):
            nm = buf.value
    NAMES[hdev] = nm
    return nm


def inject_vk(vk, up=True):
    """用 SendInput 注入一个按键。返回 SendInput 的返回��（成功=1）。"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = 0
    inp.u.ki.dwFlags = 0
    n = user32.SendInput(1, C.byref(inp), C.sizeof(INPUT))
    if up:
        inp.u.ki.dwFlags = KEYEVENTF_KEYUP
        user32.SendInput(1, C.byref(inp), C.sizeof(INPUT))
    return n


def keybd_event(vk, scan, flags):
    """老 API 注入，返回 0 成功。"""
    user32.keybd_event(vk, scan, flags, 0)


def on_wm_input(lp):
    STATS["wm_input"] += 1
    size = wt.UINT(0)
    hraw = wt.HANDLE(lp)
    # 第一次：查大小
    r = user32.GetRawInputData(hraw, RID_INPUT, None, C.byref(size), 24)
    if r == 0xFFFFFFFF or not size.value:
        STATS["data_fail"] += 1
        return
    buf = C.create_string_buffer(size.value)
    if user32.GetRawInputData(hraw, RID_INPUT, buf, C.byref(size), 24) == 0xFFFFFFFF:
        STATS["data_fail"] += 1
        return
    STATS["data_ok"] += 1
    raw = buf.raw
    if struct_unpack_i(raw, 0) != RIM_TYPEKEYBOARD:
        return
    STATS["kbd"] += 1
    hdev = struct_unpack_q(raw, 8)
    vkey = struct_unpack_h(raw, 30)
    msg = struct_unpack_i(raw, 32)
    if hdev == 0:
        STATS["hdev0"] += 1
    else:
        STATS["hdev_real"] += 1
    if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
        STATS["down"] += 1
    elif msg in (WM_KEYUP, WM_SYSKEYUP):
        STATS["up"] += 1
    VKEY_COUNT[vkey] = VKEY_COUNT.get(vkey, 0) + 1


PROC = None


def wndproc(hwnd, msg, wp, lp):
    if msg == WM_INPUT:
        on_wm_input(lp)
        return 0
    if msg == WM_DESTROY:
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


def main():
    global PROC
    print("=" * 66)
    print("Raw Input 洪水探针")
    print("=" * 66)
    print()
    print("【实验 1】注入的按键会不会产生 Raw Input 回声？")
    print("-" * 66)

    PROC = WNDPROC(wndproc)
    clsname = "RawFloodProbe"
    wc = WNDCLASSW()
    wc.lpfnWndProc = PROC
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = clsname
    user32.RegisterClassW(C.byref(wc))
    hwnd = user32.CreateWindowExW(0, clsname, "RawFloodProbe", 0, 0, 0, 0, 0,
                                  None, None, wc.hInstance, None)
    if not hwnd:
        print("  创建窗口失败 err=%s" % C.get_last_error())
        return 1
    rid = RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, hwnd)
    if not user32.RegisterRawInputDevices(C.byref(rid), 1,
                                          C.sizeof(RAWINPUTDEVICE)):
        print("  Raw Input 注册失败 err=%s" % C.get_last_error())
        return 1
    print("  Raw Input 已注册（键盘页 1/6，INPUTSINK）")

    # 先静置 0.5 秒，把消息泵跑起来
    t0 = time.time()
    msg = wt.MSG()
    while time.time() - t0 < 0.5:
        while user32.PeekMessageW(C.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(C.byref(msg))
            user32.DispatchMessageW(C.byref(msg))
        time.sleep(0.005)

    before = dict(STATS)
    # 注入一个 F5（0x74），看看有没有 Raw Input 回声
    inject_vk(0x74)
    t0 = time.time()
    while time.time() - t0 < 0.6:
        while user32.PeekMessageW(C.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(C.byref(msg))
            user32.DispatchMessageW(C.byref(msg))
        time.sleep(0.002)
    d_wm = STATS["wm_input"] - before["wm_input"]
    d_ok = STATS["data_ok"] - before["data_ok"]
    d_0 = STATS["hdev0"] - before["hdev0"]
    print("  注入 1 次 F5 后：")
    print("    收到 WM_INPUT        = %d" % d_wm)
    print("    数据读取成功        = %d" % d_ok)
    print("    其中 hDevice == 0   = %d  %s"
          % (d_0, "← 注入回声，hDevice 是 0" if d_0 else ""))
    print()
    if d_wm == 0:
        print("  >>> 结论：本机注入不产生 Raw Input 回声")
    else:
        print("  >>> 结论：🔴 注入**会产生** Raw Input 回声，hDevice = 0")
        print("      这意味着：程序自己注入 ctrl+enter 会被自己的 Raw Input")
        print("      监听到，并被判成「其它设备按了发送键」-> 污染判据！")
    print()

    print("【实验 2】按键洪水下 Raw Input 的投递率")
    print("-" * 66)
    for hz in (10, 30, 60):
        before = dict(STATS)
        n = 0
        t0 = time.time()
        gap = 1.0 / hz
        while time.time() - t0 < 3.0:
            inject_vk(0x74, up=(n % 2 == 1))   # 交替 down/up
            n += 1
            target = t0 + n * gap
            dt = target - time.time()
            if dt > 0:
                time.sleep(dt)
        t1 = time.time()
        # 收尾：把消息泵抽干
        t0 = time.time()
        while time.time() - t0 < 0.5:
            while user32.PeekMessageW(C.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(C.byref(msg))
                user32.DispatchMessageW(C.byref(msg))
            time.sleep(0.002)
        d_wm = STATS["wm_input"] - before["wm_input"]
        d_ok = STATS["data_ok"] - before["data_ok"]
        d_fail = STATS["data_fail"] - before["data_fail"]
        elapsed = t1 - (t0 - 0.5)
        print("  注入速率 %3d 次/秒，实际注入 %4d 次，用时 %.1fs" %
              (hz, n, elapsed))
        print("    收到 WM_INPUT   = %4d  (投递率 %5.1f%%)" %
              (d_wm, 100.0 * d_wm / max(1, n)))
        print("    数据读取成功   = %4d" % d_ok)
        print("    数据读取失败   = %4d  ← 缓冲区被覆盖，事件真正丢了" % d_fail)
        print()
    print("=" * 66)
    print("怎么读这些数：")
    print("  · 投递率 < 100%  -> WM_INPUT 消息本身就被系统丢弃了")
    print("  · 读取失败 > 0   -> 消息到了但数据已被覆盖（队列积压）")
    print("  · 两个都 0 且投递率 100% -> 通道没问题，别的环节出错")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
