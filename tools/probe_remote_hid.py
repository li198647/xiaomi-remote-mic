# -*- coding: utf-8 -*-
"""遥控器「消失的按键」探针：音量键 / 返回键到底有没有到达用户态？

背景（实测已知）：
  · 遥控器的语音键 = F5、OK 键 = Enter、TV 键 = 0xC0 走的是「键盘页」
    （Usage Page 0x01），Raw Input 能拿到，还带 hDevice 能认出是遥控器。
  · 但音量+/-、返回、电源键在 Windows 上**一点反应都没有** ——
    连扫描器的低级钩子都看不到它们。

🔴 这个探针专门回答一件事：**它们是真的消失了，还是只是走的通道不一样？**

  · 键盘页 (Usage Page 0x01 / Usage 0x06)  -> RAWINPUT 里 dwType = 1
  · 消费键页 (Usage Page 0x0C / Usage 0x01) -> 可能是 RAWHID (dwType = 2)
  · 通用 HID (Usage Page 0x00 / Usage 0x00)  -> 把所有 HID 报告都收上来

如果消费键页能收到，说明只是 kbdhid 把它们从「键盘流」里拿走了，
我们自己拦下来做音量调节完全可行。
如果连通用 HID 都收不到，那就是驱动/固件层就没上报，用户态无解。

用法：
  双击 probe-remote-hid.bat，然后按提示依次按遥控器上的键。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
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

import mivoice as M  # noqa: E402  复用 Win32 声明

user32 = M.user32
kernel32 = M.kernel32

# 🔴 这些 API 在 mivoice.py 里没声明过，必须自己补 argtypes，
#    否则 ctypes 默认转换会把指针参数截断成 int，PeekMessage 直接崩。
user32.PeekMessageW.restype = wt.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT,
                                 wt.UINT, wt.UINT]
user32.TranslateMessage.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = M.LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [ctypes.c_int]

RIDEV_INPUTSINK = 0x00000100      # 窗口不在前台也收 WM_INPUT

WM_INPUT = 0x00FF
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000000
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2

# RAWINPUT 里各字段的偏移（x64）
OFF_RAW_TYPE = 0
OFF_RAW_SIZE = 4
OFF_RAW_HDEV = 8
OFF_KB_MAKECODE = 24
OFF_KB_FLAGS = 26
OFF_KB_VKEY = 30
OFF_KB_MESSAGE = 32
OFF_HID_CBDATA = 28
OFF_HID_DATA = 32

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# 消费键页（Usage Page 0x0C）的常见 Usage ID
CONSUMER_NAMES = {
    0x30: "音量加 VolumeUp",
    0x31: "音量减 VolumeDown",
    0x32: "静音 Mute",
    0x33: "播放/暂停 PlayPause",
    0x34: "停止 Stop",
    0x36: "主页 Home",
    0x38: "返回 Back",
    0x3B: "搜索 Search",
    0x3C: "收藏 Favorites",
    0xE0: "音量加(另一种编码)",
    0xE2: "音量减(另一种编码)",
    0xE5: "播放/暂停(另一种编码)",
}

VK_NAMES = {
    0x0D: "Enter", 0x74: "F5", 0x24: "Home", 0x5D: "Apps",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0xC0: "未知(0xC0)", 0xFF: "未知(0xFF)",
    0xAF: "VolumeUp", 0xAE: "VolumeDown", 0xAD: "Mute",
}


def u16(b, off):
    return int.from_bytes(b[off:off + 2], "little", signed=False)


def u32(b, off):
    return int.from_bytes(b[off:off + 4], "little", signed=False)


def u64(b, off):
    return int.from_bytes(b[off:off + 8], "little", signed=False)


RIDI_DEVICENAME = 0x20000000


def device_names(hdev):
    """拿这个 hDevice 的**全部**顶层集合名字。

    🔴 关键知识点：`GetRawInputDeviceInfoW` 的 uiCommand 低 16 位是**名字索引**。
    一个物理 HID 设备可以有**多个顶层集合**（键盘 / 消费键 / 厂商自定义…），
    每个集合是一个独立的 hDevice、独立的索引。

    ⚠️ 实测（本机）：只有 `uiCommand = 0x20000007`（低位=7）能读出名字，
    纯 0x20000000（索引 0）读不到。所以这里 **0..15 全扫 + 再补一个 0x07**，
    哪个有就用哪个 —— 不猜。
    """
    names = []
    for idx in list(range(16)) + [0x07]:
        cmd = RIDI_DEVICENAME | idx
        size = wt.UINT(0)
        user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), cmd, None,
                                      ctypes.byref(size))
        if not size.value:
            continue
        buf = ctypes.create_unicode_buffer(size.value + 1)
        if user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), cmd, buf,
                                         ctypes.byref(size)):
            nm = buf.value
            # 只收像设备路径的名字（\\?\HID#... 或 \\?\hid#...），
            # 免得把索引越界时返回的垃圾当成真名字
            if nm and nm.lower().startswith("\\\\?\\") and nm not in names:
                names.append(nm)
    return names


def is_remote(names):
    for n in names:
        low = n.lower()
        if "vid&012717" in low or "vid_2717" in low:
            return True
    return False


def short_dev(names):
    if not names:
        return "<未知设备>"
    if is_remote(names):
        # 遥控器：只显示集合名里能区分的部分
        return "【遥控器】" + names[0].split("#")[0][-55:]
    return names[0].split("#")[0][-50:]


class Probe:
    def __init__(self):
        self.hwnd = None
        self.hook = None
        self.events = []
        self.seen_remote = False
        self._dev_cache = {}
        self._dropped = 0

    # ---------------- 低级钩子（对照用） ----------------
    def hook_proc(self, nCode, wParam, lParam):
        try:
            if nCode == 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(M.KBDLLHOOKSTRUCT)).contents
                kind = {WM_KEYDOWN: "down", WM_KEYUP: "up",
                        WM_SYSKEYDOWN: "sysdown",
                        WM_SYSKEYUP: "sysup"}.get(wParam, str(wParam))
                self.events.append(("HOOK", kind, kb.vkCode, kb.scanCode,
                                    kb.flags, ""))
        except Exception:
            pass
        return user32.CallNextHookEx(self.hook, nCode, wParam, lParam)

    def install_hook(self):
        # 复用 mivoice 的签名（LONG_PTR 返回），不一致会出乱码
        proc_type = ctypes.WINFUNCTYPE(M.LRESULT, ctypes.c_int, wt.WPARAM,
                                       wt.LPARAM)
        self._proc = proc_type(self.hook_proc)
        self.hook = user32.SetWindowsHookExW(13, self._proc, None, 0)  # WH_KEYBOARD_LL
        if not self.hook:
            print("  [!] 低级钩子安装失败 err=%s（对照功能失效，不影响主结论）"
                  % ctypes.get_last_error())
        else:
            print("  低级键盘钩子: 已安装")

    # ---------------- Raw Input ----------------
    def register_raw(self):
        """注册两条通道：键盘页 + 消费键页。

        🔴 故意**不**注册「通用 HID」(UsagePage 0x00 / Usage 0x00)：
        那一档会把系统里所有 HID 设备（摄像头、蓝牙、触控板…）的报告
        全部收上来，事件量能到每秒几万条，会把程序拖死。
        """
        devs = []
        for page, usage, tag in (
            (0x01, 0x06, "键盘页(0x01/0x06)"),
            (0x0C, 0x01, "消费键页(0x0C/0x01)"),
        ):
            devs.append(M.RAWINPUTDEVICE(page, usage, RIDEV_INPUTSINK, self.hwnd))
            print("  注册通道: %s" % tag)
        ok = user32.RegisterRawInputDevices(
            (M.RAWINPUTDEVICE * len(devs))(*devs), len(devs),
            ctypes.sizeof(M.RAWINPUTDEVICE))
        if not ok:
            print("  [!] Raw Input 注册失败 err=%s" % ctypes.get_last_error())
            return False
        print("  Raw Input: 注册成功（2 条通道）")
        return True

    def read_raw(self, lparam):
        size = wt.UINT(0)
        hraw = wt.HANDLE(lparam)
        if user32.GetRawInputData(hraw, RID_INPUT, None,
                                  ctypes.byref(size), 24) != 0:
            return None
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if user32.GetRawInputData(hraw, RID_INPUT, buf,
                                  ctypes.byref(size), 24) == 0xFFFFFFFF:
            return None
        return buf.raw

    def handle(self, lparam):
        raw = self.read_raw(lparam)
        if raw is None:
            return
        rtype = u32(raw, OFF_RAW_TYPE)
        hdev = u64(raw, OFF_RAW_HDEV)
        # 设备名要缓存：GetRawInputDeviceInfoW 每次调用都要分配 buffer，
        # 事件一多就拖死
        if hdev not in self._dev_cache:
            self._dev_cache[hdev] = device_names(hdev)
        names = self._dev_cache[hdev]
        remote = is_remote(names)
        if rtype == RIM_TYPEKEYBOARD:
            mc = u16(raw, OFF_KB_MAKECODE)
            vk = u16(raw, OFF_KB_VKEY)
            fl = u16(raw, OFF_KB_FLAGS)
            msg = u32(raw, OFF_KB_MESSAGE)
            kind = {WM_KEYDOWN: "down", WM_KEYUP: "up",
                    WM_SYSKEYDOWN: "sysdown",
                    WM_SYSKEYUP: "sysup"}.get(msg, str(msg))
            label = VK_NAMES.get(vk, "vk=0x%02X" % vk)
            self.events.append(("RAW-KB", kind, vk, mc, fl,
                                "%s | %s | scan=0x%02X flags=0x%02X | %s"
                                % (short_dev(names), label, mc, fl,
                                   "遥控器" if remote else "其它设备")))
        elif rtype == RIM_TYPEHID:
            cb = u32(raw, OFF_HID_CBDATA)
            data = raw[OFF_HID_DATA:OFF_HID_DATA + min(cb, 64)]
            # HID 报告：第 2 字节通常是 Usage Page 高字节，第 3 字节 Usage
            usage = 0
            page = 0
            if len(data) >= 3:
                page = data[1]
                usage = data[2]
            label = CONSUMER_NAMES.get(usage, "usage=0x%02X" % usage)
            self.events.append(("RAW-HID", "hid", usage, 0, 0,
                                "%s | page=0x%02X %s | 数据=%s"
                                % (short_dev(names), page, label,
                                   data[:16].hex(" "))))
        else:
            self.events.append(("RAW-?", str(rtype), 0, 0, 0, short_dev(names)))


# ---------------- 窗口过程 ----------------
def make_wndproc(probe):
    # 直接复用 mivoice 的 WNDPROC（签名必须完全一致，否则回调会被调用成乱码）
    def wndproc(hwnd, msg, wp, lp):
        if msg == WM_INPUT:
            try:
                probe.handle(lp)
            except Exception:
                pass
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    return M.WNDPROC(wndproc)


def main():
    selftest = 0
    for a in sys.argv[1:]:
        if a.startswith("--selftest"):
            parts = a.split("=")
            try:
                selftest = int(parts[1]) if len(parts) > 1 else 5
            except ValueError:
                selftest = 5

    print("=" * 66)
    print("遥控器「消失的按键」探针")
    print("=" * 66)
    print()
    print("目的：看音量+/-、返回、电源这些键到底有没有到达用户态。")
    print()
    sys.stdout.flush()

    probe = Probe()
    hinst = kernel32.GetModuleHandleW(None)
    cls_name = "MiVoiceHidProbe"

    wndproc = make_wndproc(probe)
    wndclass = M.WNDCLASSW()
    wndclass.style = 0x0008          # CS_HREDRAW
    wndclass.lpfnWndProc = wndproc
    wndclass.hInstance = hinst
    wndclass.lpszClassName = cls_name
    if not user32.RegisterClassW(ctypes.byref(wndclass)):
        # 已注册过也算正常
        pass
    hwnd = user32.CreateWindowExW(0, cls_name, "MiVoice HID Probe",
                                   0x00CF0000, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        print("[!] 建窗口失败 err=%s" % ctypes.get_last_error())
        return 1
    probe.hwnd = hwnd
    sys.stdout.flush()

    probe.install_hook()
    sys.stdout.flush()
    if not probe.register_raw():
        print("[!] Raw Input 没注册成功，后面的结论不可信，先退出。")
        input("按回车退出…")
        return 1
    sys.stdout.flush()

    print()
    print("-" * 66)
    if selftest:
        print("【自检模式】%d 秒后自动退出，不需要按任何键。" % selftest)
    else:
        print("请依次按下面这些键，每按一个停一下（每个键之间等 1 秒）：")
        print()
        print("   1) 语音键（按住 1 秒再松）")
        print("   2) OK / 确认键")
        print("   3) 音量 +")
        print("   4) 音量 -")
        print("   5) 返回键")
        print("   6) TV 键")
        print()
        print("按完最后一个键后，回到这个黑窗口，按回车键结束。")
    print("-" * 66)
    print()

    # 后台消息循环
    # 🔴 关键：WM_INPUT 必须交给窗口过程(wndproc)处理，**不能**在这里直接 handle。
    #    在 PeekMessage 阶段直接调 GetRawInputDeviceInfoW 读 hDevice 会失败
    #    （设备名读出来是空的 -> 会误判成「遥控器没到达」）。
    #    正确姿势和 mivoice.py 一致：取出消息 -> Dispatch -> 窗口过程里处理。
    msg = wt.MSG()
    deadline = time.time() + (selftest if selftest else 300)
    while time.time() < deadline:
        r = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)  # PM_REMOVE
        if r:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.02)

    # ---- 汇总 ----
    print()
    print("=" * 66)
    print("采集到的全部事件")
    print("=" * 66)
    remote_hooks = 0
    remote_raw_kb = 0
    remote_raw_hid = 0
    for src, kind, vk, sc, fl, extra in probe.events:
        line = "[%-7s] %-7s vk=0x%02X scan=0x%02X flags=0x%02X  %s" % (
            src, kind, vk, sc, fl, extra)
        print(line)
        if "遥控器" in extra:
            if src == "HOOK":
                remote_hooks += 1
            elif src == "RAW-KB":
                remote_raw_kb += 1
            else:
                remote_raw_hid += 1

    print()
    print("=" * 66)
    print("结论")
    print("=" * 66)
    print("  遥控器低级钩子事件 : %d 条" % remote_hooks)
    print("  遥控器 Raw 键盘事件: %d 条" % remote_raw_kb)
    print("  遥控器 Raw HID 事件 : %d 条" % remote_raw_hid)
    print()
    if remote_raw_kb > 0:
        print("  [OK] 遥控器的键盘页按键**能**通过 Raw Input 到达，")
        print("       带 hDevice，能分清设备和实体键盘。")
    else:
        print("  [!] 遥控器的键盘页按键**没有**到达 Raw Input。")
    if remote_raw_hid > 0:
        print("  [OK] 遥控器的 HID 报告**能**到达（含消费键）——")
        print("       说明音量/返回键不是消失了，只是走的通道不同，")
        print("       我们自己拦下来实现是可行的。")
    else:
        # 🔴 这一条以前写成「HID 报告 0 条 -> 用户态无解」，是**错的**，已更正：
        #    本探针只注册了「键盘页 + 消费键页」两条通道，而实测本机遥控器
        #    在系统里**只有 1 个 HID 集合、而且是键盘集合（路径后缀 kbd）**，
        #    系统把它交给 kbdhid，Raw Input 只给 KEYBOARD 事件、**永远不给 RAWHID**。
        #    所以 HID 报告计数恒为 0 是必然的，不能据此判定「无解」。
        print("  [i] HID 报告计数为 0 是**正常**的，不能据此判定「用户态无解」。")
        print("      原因：本探针注册的两条通道都不产生 RAWHID；而实测本机遥控器")
        print("      在系统里只有 1 个 HID 集合且是键盘集合，被 kbdhid 接管。")
        print("      要判断音量/返回键到底有没有上报，请用**新版探针**：")
        print("      probe-hidden-keys.bat（看按键时键盘页有没有事件）")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
