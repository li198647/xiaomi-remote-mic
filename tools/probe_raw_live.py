"""探针：把 Raw Input 的真实行为测清楚（2026-10-30）。

要回答三个问题，每一个都用实测回答，不靠推断：

  Q1. 本程序用 SendInput 注入的按键，**会不会产生 Raw Input 回声**？
      会不会？hDevice 是多少？
      → 这决定了「MiVoice 自己注入的 ctrl+enter 会不会被自己的
        Raw Input 看到并当成『实体键盘按了发送键』」。

  Q2. 遥控器按住语音键(F5) 说话时，Raw Input 事件**丢不丢**？
      丢了多少？是「消息没来」还是「消息来了但数据被覆盖」？

  Q3. 遥控器按 OK 键(Enter) 时，Raw Input 事件的 hDevice
      **跟语音键是同一个 hDevice 吗**？设备名读得到吗？

用法：
  python tools\\probe_raw_live.py inject   # 回答 Q1（自动，5 秒）
  python tools\\probe_raw_live.py live     # 回答 Q2/Q3（手动按键，写日志文件）

live 模式下会一直把每个 Raw Input 事件写进 tools\\raw_live.log，
按完键后直接看那个文件。
"""

import ctypes as C
import ctypes.wintypes as wt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "raw_live.log")

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


# ---------------------------------------------------------------- 结构体
# 🔴 坑：INPUT 的 union 必须 >= 最大成员（MOUSEINPUT = 32 字节），
#    所以 union = 32，INPUT = 4(type) + 4(pad) + 32 = **40 字节**。
#    算错成 24 的话 SendInput 会直接失败（返回 0），而且**不报错**。
class RAWINPUTDEVICE(C.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", LPULONG_PTR)]


class INPUTunion(C.Structure):
    _fields_ = [("ki", KEYBDINPUT), ("pad", C.c_byte * 8)]   # 24 + 8 = 32


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

# 🔴 不设 restype 的话 GetModuleHandleW 返回 32 位 int，句柄被截断 ->
#    SetWindowsHookExW 直接报 err=126 (ERROR_MOD_NOT_FOUND)。
kernel32.GetModuleHandleW.restype = wt.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]

VK_NAME = {0x74: "F5", 0x0D: "Enter", 0xA4: "LAlt", 0xA2: "LCtrl",
           0x20: "Space", 0x26: "Up", 0x28: "Down", 0x25: "Left",
           0x27: "Right", 0x24: "Home", 0x5D: "Apps", 0xC0: "TV"}


def vkey_label(vk):
    return VK_NAME.get(vk, "0x%02X" % vk)


def msg_label(m):
    return {WM_KEYDOWN: "down", WM_KEYUP: "up",
            WM_SYSKEYDOWN: "sysdown", WM_SYSKEYUP: "sysup"}.get(m, str(m))


def unpack_h(b, o):
    return int.from_bytes(b[o:o + 2], "little")


def unpack_i(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def unpack_q(b, o):
    return int.from_bytes(b[o:o + 8], "little")


NAMES = {}
DEV_SEEN = {}


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
    """SendInput 注入。返回 SendInput 的返回值（1=成功，0=失败）。"""
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


STATS = {"wm_input": 0, "data_ok": 0, "data_fail": 0, "hdev0": 0,
         "down": 0, "up": 0}
VK_COUNT = {}          # vk -> 收到的 Raw Input 条数
VK_COUNT_UP = {}       # vk -> 其中「抬起(up)」的条数
LOG_FP = None
PROC = None


def logline(s):
    print(s)
    if LOG_FP:
        LOG_FP.write(s + "\n")
        LOG_FP.flush()


def on_wm_input(lp):
    STATS["wm_input"] += 1
    size = wt.UINT(0)
    hraw = wt.HANDLE(lp)
    r = user32.GetRawInputData(hraw, RID_INPUT, None, C.byref(size), 24)
    if r == 0xFFFFFFFF:
        STATS["data_fail"] += 1
        logline("[%s] GetRawInputData(查大小) 失败 err=%s  <- 数据被覆盖，事件丢了"
                % (time.strftime("%H:%M:%S"), C.get_last_error()))
        return
    if not size.value:
        STATS["data_fail"] += 1
        return
    buf = C.create_string_buffer(size.value)
    if user32.GetRawInputData(hraw, RID_INPUT, buf, C.byref(size), 24) == 0xFFFFFFFF:
        STATS["data_fail"] += 1
        logline("[%s] GetRawInputData(读数据) 失败 err=%s  <- 数据被覆盖，事件丢了"
                % (time.strftime("%H:%M:%S"), C.get_last_error()))
        return
    STATS["data_ok"] += 1
    raw = buf.raw
    if unpack_i(raw, 0) != RIM_TYPEKEYBOARD:
        return
    hdev = unpack_q(raw, 8)
    makecode = unpack_h(raw, 24)
    vkey = unpack_h(raw, 30)
    msg = unpack_i(raw, 32)
    if hdev == 0:
        STATS["hdev0"] += 1
    if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
        STATS["down"] += 1
    elif msg in (WM_KEYUP, WM_SYSKEYUP):
        STATS["up"] += 1
    VK_COUNT[vkey] = VK_COUNT.get(vkey, 0) + 1
    if msg in (WM_KEYUP, WM_SYSKEYUP):
        VK_COUNT_UP[vkey] = VK_COUNT_UP.get(vkey, 0) + 1
    if hdev not in DEV_SEEN:
        DEV_SEEN[hdev] = True
        nm = dev_name(hdev)
        logline("[%s] ★ 新设备 hdev=%s 名字=%r"
                % (time.strftime("%H:%M:%S"), hdev, nm))
    logline("[%s] %-8s vk=0x%02X(%s) scan=0x%02X hdev=%s"
            % (time.strftime("%H:%M:%S"), msg_label(msg), vkey, vkey_label(vkey),
               makecode, hdev))


def wndproc(hwnd, msg, wp, lp):
    if msg == WM_INPUT:
        on_wm_input(lp)
        return 0
    if msg == WM_DESTROY:
        return 0
    return user32.DefWindowProcW(hwnd, msg, wp, lp)


def make_window(clsname):
    global PROC
    PROC = WNDPROC(wndproc)
    wc = WNDCLASSW()
    wc.lpfnWndProc = PROC
    wc.hInstance = kernel32.GetModuleHandleW(None)
    wc.lpszClassName = clsname
    user32.RegisterClassW(C.byref(wc))
    hwnd = user32.CreateWindowExW(0, clsname, clsname, 0, 0, 0, 0, 0,
                                  None, None, wc.hInstance, None)
    if not hwnd:
        raise OSError("创建窗口失败 err=%s" % C.get_last_error())
    rid = RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, hwnd)
    if not user32.RegisterRawInputDevices(C.byref(rid), 1,
                                          C.sizeof(RAWINPUTDEVICE)):
        raise OSError("Raw Input 注册失败 err=%s" % C.get_last_error())
    return hwnd


def pump(hwnd, seconds):
    msg = wt.MSG()
    t0 = time.time()
    while time.time() - t0 < seconds:
        while user32.PeekMessageW(C.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(C.byref(msg))
            user32.DispatchMessageW(C.byref(msg))
        time.sleep(0.003)


def mode_inject():
    print("=" * 68)
    print("Q1：SendInput 注入的按键，会不会产生 Raw Input 回声？")
    print("=" * 68)
    print()
    print("sizeof(INPUT) = %d  (x64 正确值是 40)" % C.sizeof(INPUT))
    hwnd = make_window("RawLiveInject")
    print("Raw Input 已注册（键盘页 1/6，INPUTSINK）")
    pump(hwnd, 0.5)
    print()
    for label, vk, up in (("Alt(0xA4) 单次按下", 0xA4, True),
                           ("F5(0x74) 单次按下", 0x74, True),
                           ("Enter(0x0D) 单次按下", 0x0D, True)):
        before = dict(STATS)
        n = inject_vk(vk, up=up)
        pump(hwnd, 0.5)
        dwm = STATS["wm_input"] - before["wm_input"]
        d0 = STATS["hdev0"] - before["hdev0"]
        print("  %-22s SendInput 返回=%s  收到 WM_INPUT=%d  其中 hdev=0 的=%d"
              % (label, n, dwm, d0))
    print()
    total = STATS["wm_input"]
    if total:
        print("  >>> 🔴 注入**会产生** Raw Input 回声！")
        print("      hDevice = %s" % ("0（说明是注入回声）"
                                        if STATS["hdev0"] else "非 0"))
        print("      影响：程序自己注入的 ctrl+enter 会被自己的 Raw Input")
        print("            监听到 -> 被判成「实体键盘按了发送键」->")
        print("            污染看门狗兜底判据 -> 下一次按 OK 键就废了。")
    else:
        print("  >>> 注入**不产生** Raw Input 回声（本机实测）")
        print("      影响：MiVoice 日志里那条 hdev=0 不是注入造成的，")
        print("            得另找来源。")
    return 0


def mode_live():
    global LOG_FP
    print("=" * 68)
    print("Q2/Q3：实时记录 Raw Input 事件")
    print("=" * 68)
    print()
    print("日志文件：%s" % LOG_PATH)
    print()
    print("请依次做（每步之间停 1 秒）：")
    print("  1) 按住遥控器【语音键】说话 5 秒，然后松手")
    print("  2) 按一下遥控器【OK 键】")
    print("  3) 用实体键盘敲几个键（含回车）")
    print("  4) 在这里按 Ctrl+C 结束")
    print()
    try:
        LOG_FP = open(LOG_PATH, "w", encoding="utf-8")
    except Exception as e:
        print("打不开日志文件: %r" % e)
        return 1
    hwnd = make_window("RawLiveWatch")
    print("Raw Input 已注册，开始记录…")
    print()
    try:
        while True:
            pump(hwnd, 0.05)
            if STATS["wm_input"] and STATS["wm_input"] % 40 == 0:
                print("  ...已记录 %d 个事件" % STATS["wm_input"])
    except KeyboardInterrupt:
        pass
    print()
    print("统计：")
    for k, v in STATS.items():
        print("  %-12s = %d" % (k, v))
    print()
    print("完整日志：%s" % LOG_PATH)
    return 0


def mode_flood():
    """量化「Raw Input 在高频按键下的丢包率」。

    🔴 注入的按键同样会产生 WM_INPUT（hdev=0），所以可以用它当压力源，
       不需要人配合按遥控器。

    要回答：主线程处理 WM_INPUT 的速度跟不上时，事件是怎么丢的？
      · WM_INPUT 消息本身没来      -> 投递率 < 100%
      · 消息来了但数据被覆盖      -> 「数据读取失败」计数 > 0
    """
    print("=" * 68)
    print("Q2：Raw Input 在高频按键下的丢包率")
    print("=" * 68)
    print()
    hwnd = make_window("RawLiveFlood")
    pump(hwnd, 0.4)
    print("  %-14s %8s %10s %10s %10s %10s"
          % ("注入速率", "注入数", "WM_INPUT", "读取成功", "读取失败", "投递率"))
    for hz in (10, 30, 60, 120):
        before = dict(STATS)
        n = 0
        t0 = time.time()
        gap = 1.0 / hz
        while time.time() - t0 < 3.0:
            inject_vk(0x74, up=(n % 2 == 1))     # 交替 down/up
            n += 1
            dt = (t0 + n * gap) - time.time()
            if dt > 0:
                time.sleep(dt)
        # 收尾：把消息泵抽干
        pump(hwnd, 0.8)
        d_wm = STATS["wm_input"] - before["wm_input"]
        d_ok = STATS["data_ok"] - before["data_ok"]
        d_fail = STATS["data_fail"] - before["data_fail"]
        print("  %-14s %8d %10d %10d %10d %9.1f%%"
              % ("%d 次/秒" % hz, n, d_wm, d_ok, d_fail,
                 100.0 * d_wm / max(1, n)))
    print()
    print("  怎么读：")
    print("   · 投递率 < 100%% -> WM_INPUT 消息被系统直接丢弃")
    print("   · 读取失败 > 0  -> 消息到了，但缓冲区已被新事件覆盖（真丢）")
    print()
    print("  重要：Windows 文档明确说 Raw Input 缓冲区只有一份，")
    print("  「没在下一条 WM_INPUT 到来前取走的数据会被丢弃」。")
    print("  所以主线程只要处理得慢，事件就会成片丢失。")
    return 0


def mode_swallow():
    """🔴 决定性实验：**被低级键盘钩子吞掉的按键，还有没有 Raw Input 事件？**

    为什么必须测这个（2026-10-30）：

      MiVoice 的整个架构是「钩子先吞 -> 等 Raw Input 确认设备归属」。
      但 19:03 的实测日志显示：
        · 19:03:40.372  真实 F5 down（**没被吞**，HW）-> Raw Input 到了
        · 19:03:41.876~ 之后 292 次自动重复（**全被吞**，SWAL）
                           -> 主日志一条「Raw 遥控器语音键」都没有
        · 19:03:51.048  真实 OK 键 down（**被吞**，SND）
                           -> 等 400ms 没等到 Raw Input -> 只能靠 timeout 兜底

      如果「被吞 = 没有 Raw Input」，那这套架构**从根上就不成立**，
      必须换判据。反之则说明是队列/覆盖问题，另想办法。

    做法：装一个低级键盘钩子，吞掉 vk=0x74（F5），然后注入 F5，
          看 Raw Input 收到几条。对照组：不吞。
    """
    print("=" * 68)
    print("Q2：被低级键盘钩子【吞掉】的按键，还有没有 Raw Input 事件？")
    print("=" * 68)
    print()
    global PROC
    hwnd = make_window("RawLiveSwallow")
    pump(hwnd, 0.4)

    HOOKPROC = C.WINFUNCTYPE(LRESULT, C.c_int, wt.WPARAM, wt.LPARAM)
    # mode: "pass" = 全放行；"all" = 下+抬全吞；"down" = 只吞「按下」，放开「抬起」
    state = {"mode": "pass"}

    class KBDLLHOOKSTRUCT(C.Structure):
        _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD),
                    ("flags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", LPULONG_PTR)]

    def hook_proc(nCode, wParam, lParam):
        if nCode == 0 and state["mode"] != "pass":
            kb = C.cast(lParam, C.POINTER(KBDLLHOOKSTRUCT)).contents
            if kb.vkCode == 0x74:
                is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if state["mode"] == "all":
                    return 1              # 下+抬都吞
                if state["mode"] == "down" and is_down:
                    return 1              # 只吞「按下」，抬起放行
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [wt.HANDLE, C.c_int, wt.WPARAM, wt.LPARAM]
    user32.SetWindowsHookExW.restype = wt.HHOOK
    user32.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]

    proc = HOOKPROC(hook_proc)          # 必须保留引用
    hook = user32.SetWindowsHookExW(13, proc, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        print("  钩子安装失败 err=%s" % C.get_last_error())
        return 1
    print("  低级键盘钩子已安装（可切换吞/放行）")
    print()
    print("  %-14s %10s %10s %12s %14s"
          % ("模式", "WM_INPUT", "其中F5", "F5抬起数", "F5 的 hdev"))
    for label, mode in (("放行(对照)", "pass"), ("全吞(下+抬)", "all"),
                        ("只吞按下", "down")):
        state["mode"] = mode
        before_wm = STATS["wm_input"]
        before_f5 = VK_COUNT.get(0x74, 0)
        before_up = VK_COUNT_UP.get(0x74, 0)
        # 注入 F5 down（不放行 up），等 0.6 秒
        inject_vk(0x74, up=False)
        pump(hwnd, 0.6)
        inject_vk(0x74, up=True)
        pump(hwnd, 0.4)
        d_wm = STATS["wm_input"] - before_wm
        d_f5 = VK_COUNT.get(0x74, 0) - before_f5
        d_up = VK_COUNT_UP.get(0x74, 0) - before_up
        print("  %-14s %10d %10d %12d %14s"
              % (label, d_wm, d_f5, d_up, "见日志"))
    print()
    print("  怎么读（这三条是本探针要回答的唯一问题）：")
    print("   · 「全吞(下+抬)」F5 = 0  -> 被钩子吞掉的按键**没有** Raw Input")
    print("                             （已实测成立 -> v1.3+ 仲裁器架构前提被推翻）")
    print("   · 「只吞按下」F5 抬起数 >= 1 -> 【抬起事件仍能到达 Raw Input】")
    print("       => 就可以「吞掉按下、放开抬起」，")
    print("          用**抬起那一条**的 hDevice 拿到**这一下按键的确定设备归属**")
    print("          （这是 v1.7 的新架构基础）")
    print("   · 「只吞按下」F5 抬起数 = 0  -> 抬起也被连带吞了，这条路走不通")
    print()
    print("  详细事件（上面这段时间的原始记录）：")
    print()
    user32.UnhookWindowsHookEx(hook)
    return 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "inject"
    if mode == "inject":
        return mode_inject()
    if mode == "flood":
        return mode_flood()
    if mode == "swallow":
        return mode_swallow()
    if mode == "live":
        return mode_live()
    print("用法: python tools\\probe_raw_live.py [inject|flood|swallow|live]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
