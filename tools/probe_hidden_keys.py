# -*- coding: utf-8 -*-
"""遥控器「按了没反应的三个键」深度探针 —— 音量+ / 音量- / 返回

用法：双击 probe-hidden-keys.bat

为什么需要这个新探针（probe_remote_hid.py 不够用）：
  老探针只注册了 2 条 Raw Input 通道（键盘页 0x01/0x06 + 消费键页 0x0C/0x01）。
  如果遥控器的音量/返回键走的是**厂商自定义报告**（不是标准消费键），
  这两条通道**一条都收不到**，老探针就会得出「用户态无解」的错误结论。

  本探针多了一条**决定性的**通道：用 SetupAPI 枚举遥控器的每个 HID 集合，
  直接 CreateFile + ReadFile 读**原始输入报告**。这条路不经过 Raw Input 的
  页过滤，只要固件真的上报了，字节就一定看得见。

三阶段，一次运行全回答：

  阶段 A（不用按键，立刻出结果）
     这个 HID 集合在**描述符里声明**支持哪些东西？
     · 声明了音量/返回 → 固件本来就该报，只是 Windows 没处理 → 我们能接管
     · 啥都没声明      → 固件根本没打算通过蓝牙报这些键 → 用户态无解

  阶段 B（按提示按键）
     3 条通道同时监听：Raw Input 键盘页 / 消费键页 / HID 原始报告
     按音量+、音量-、返回，看有没有任何字节冒出来。

  阶段 C（自动）
     汇总 + 给结论，直接告诉你「能不能做成鼠标滚轮」。
"""
import ctypes
import ctypes.wintypes as wt
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

import mivoice as M  # noqa: E402  复用 Win32 声明

user32 = M.user32
kernel32 = M.kernel32
setupapi = M.setupapi
hid = ctypes.WinDLL("hid", use_last_error=True)

# 🔴 PeekMessage 系列必须补 argtypes，否则指针被截断成 int 直接崩
user32.PeekMessageW.restype = wt.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT,
                                 wt.UINT, wt.UINT]
user32.TranslateMessage.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = M.LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [ctypes.c_int]

# ---------------- hid.dll ----------------
hid.HidD_GetHidGuid.restype = None
hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(M.GUID)]
hid.HidD_GetPreparsedData.restype = wt.BOOL
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = wt.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]

HIDP_STATUS_SUCCESS = 0x00110000
HidP_Input = 0


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT),
        ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberOutputDataIndices", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


class _VAL_RANGE(ctypes.Structure):
    _fields_ = [("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT),
                ("StringMin", wt.USHORT), ("StringMax", wt.USHORT),
                ("DesignatorMin", wt.USHORT), ("DesignatorMax", wt.USHORT),
                ("DataIndexMin", wt.USHORT), ("DataIndexMax", wt.USHORT)]


class _VAL_NOTRANGE(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("Reserved1", wt.USHORT),
                ("StringIndex", wt.USHORT), ("Reserved2", wt.USHORT),
                ("DesignatorIndex", wt.USHORT), ("Reserved3", wt.USHORT),
                ("DataIndex", wt.USHORT), ("Reserved4", wt.USHORT)]


class _VAL_UNION(ctypes.Union):
    _fields_ = [("Range", _VAL_RANGE), ("NotRange", _VAL_NOTRANGE)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wt.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wt.USHORT),
        ("LinkCollection", wt.USHORT),
        ("LinkUsage", wt.USHORT),
        ("LinkUsagePage", wt.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wt.USHORT),
        ("ReportCount", wt.USHORT),
        ("Reserved2", wt.USHORT * 5),
        ("UnitsExp", wt.ULONG),
        ("Units", wt.ULONG),
        ("LogicalMin", wt.LONG),
        ("LogicalMax", wt.LONG),
        ("PhysicalMin", wt.LONG),
        ("PhysicalMax", wt.LONG),
        ("u", _VAL_UNION),
    ]


hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetValueCaps.restype = ctypes.c_long
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int,
                                  ctypes.POINTER(HIDP_VALUE_CAPS),
                                  ctypes.POINTER(wt.USHORT), ctypes.c_void_p]

# ---------------- Raw Input 常量 ----------------
RIDEV_INPUTSINK = 0x00000100
WM_INPUT = 0x00FF
WM_DESTROY = 0x0002
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2

OFF_RAW_TYPE = 0
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

# 消费键页 usage 名字
CONSUMER_NAMES = {
    0x30: "电源 Power",
    0x31: "睡眠 Sleep",
    0x32: "唤醒 Wake",
    0x33: "静音 Mute",
    0x34: "音量加 VolumeUp",
    0x35: "音量减 VolumeDown",
    0x36: "播放/暂停 PlayPause",
    0x37: "停止 Stop",
    0x38: "上一曲 PrevTrack",
    0x39: "下一曲 NextTrack",
    0x40: "快进 FastForward",
    0x41: "快退 Rewind",
    0x6F: "亮度加 BrightnessUp",
    0x70: "亮度减 BrightnessDown",
    0xB0: "播放 Play",
    0xB1: "暂停 Pause",
    0xB2: "录制 Record",
    0xB3: "快进 FastForward",
    0xB4: "快退 Rewind",
    0xB5: "下一曲 NextTrack",
    0xB6: "上一曲 PrevTrack",
    0xB7: "停止 Stop",
    0xCD: "播放/暂停 PlayPause",
    0xE2: "静音 Mute",
    0xE9: "音量加 VolumeUp",
    0xEA: "音量减 VolumeDown",
    0x183: "浏览器主页",
    0x223: "AC Home 主页",
    0x224: "AC Back 返回",
    0x225: "AC Forward 前进",
    0x226: "AC Stop",
    0x227: "AC Refresh 刷新",
    0x22A: "AC 书签",
}

PAGE_NAMES = {
    0x01: "通用桌面(键盘/鼠标)",
    0x02: "仿真控制",
    0x06: "通用设备",
    0x07: "键盘LED",
    0x08: "LED",
    0x09: "按键 Buttons",
    0x0B: "电话 Telephone",
    0x0C: "消费键 Consumer",
    0x0D: "数字化仪",
    0x0E: "触觉 Haptics",
    0xFF00: "厂商自定义",
    0xFF01: "厂商自定义 1",
}

VK_NAMES = {
    0x0D: "Enter", 0x74: "F5", 0x24: "Home", 0x5D: "Apps",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0xC0: "0xC0", 0xFF: "0xFF",
    0xAF: "VolumeUp", 0xAE: "VolumeDown", 0xAD: "Mute",
    0xA6: "浏览器后退",
}

# 我们关心的三个键在消费键页的 usage
WANT_USAGE = {0x34: "音量加", 0x35: "音量减", 0xE9: "音量加", 0xEA: "音量减",
              0x224: "返回"}


def u16(b, off):
    return int.from_bytes(b[off:off + 2], "little", signed=False)


def u32(b, off):
    return int.from_bytes(b[off:off + 4], "little", signed=False)


def u64(b, off):
    return int.from_bytes(b[off:off + 8], "little", signed=False)


# ===========================================================================
# 阶段 A：枚举遥控器的 HID 集合 + 读声明能力
# ===========================================================================
def enum_hid_interfaces(match="2717"):
    """枚举 HID 设备接口，返回符合 match 的接口路径列表。"""
    guid = M.GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    dev_list = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        M.DIGCF_PRESENT | M.DIGCF_DEVICEINTERFACE)
    paths = []
    if not dev_list:
        return paths
    idx = 0
    while True:
        iface = M.SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(M.SP_DEVICE_INTERFACE_DATA)
        if not setupapi.SetupDiEnumDeviceInterfaces(
                dev_list, None, ctypes.byref(guid), idx, ctypes.byref(iface)):
            break
        needed = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            dev_list, ctypes.byref(iface), None, 0, ctypes.byref(needed), None)
        if needed.value:
            buf = ctypes.create_string_buffer(needed.value)
            cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buf, ctypes.byref(wt.DWORD(cb_size)), 4)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    dev_list, ctypes.byref(iface), buf, needed.value,
                    None, None):
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                if path and (not match or match.lower() in path.lower()):
                    paths.append(path)
        idx += 1
    setupapi.SetupDiDestroyDeviceInfoList(dev_list)
    return paths


def read_caps(handle):
    """读句柄对应集合的 HIDP_CAPS，返回 (caps, value_usage_list) 或 None。"""
    prep = ctypes.c_void_p()
    if not hid.HidD_GetPreparsedData(wt.HANDLE(handle), ctypes.byref(prep)):
        return None
    try:
        caps = HIDP_CAPS()
        st = hid.HidP_GetCaps(prep, ctypes.byref(caps))
        if st != HIDP_STATUS_SUCCESS:
            return None
        usages = []
        n = caps.NumberInputValueCaps
        if n:
            arr = (HIDP_VALUE_CAPS * n)()
            ln = wt.USHORT(n)
            st2 = hid.HidP_GetValueCaps(HidP_Input, arr, ctypes.byref(ln), prep)
            if st2 == HIDP_STATUS_SUCCESS:
                for i in range(ln.value):
                    c = arr[i]
                    if c.IsRange:
                        for u in range(c.u.Range.UsageMin, c.u.Range.UsageMax + 1):
                            usages.append((c.UsagePage, u, c.BitSize))
                    else:
                        usages.append((c.UsagePage, c.u.NotRange.Usage, c.BitSize))
        return caps, usages
    finally:
        hid.HidD_FreePreparsedData(prep)


def short_path(p):
    """把超长的设备路径压成可读的一小段。"""
    if len(p) <= 96:
        return p
    return p[:60] + "…" + p[-30:]


# ===========================================================================
# 阶段 B：两条 Raw Input 通道
# ===========================================================================
class RawListener:
    def __init__(self):
        self.hwnd = None
        self.events = []
        self._dev_cache = {}
        self.lock = threading.Lock()

    def register(self):
        """两条通道。

        🔴 为什么不是三条（实测踩过，别再试）：
          想用「通用 HID 页 0x00/0x00」收厂商自定义报告 ——
          `RegisterRawInputDevices` 直接返回 err=87(参数非法)，
          带不带 RIDEV_INPUTSINK 都一样。这个姿势 Windows 不接受。

        🔴 为什么 HID 直读也不行（实测）：
          遥控器在系统里**只有一个 HID 集合，路径后缀是「kbd」**
          （就是键盘集合，没有独立的消费键集合）。
          它被系统键盘驱动独占，CreateFile 返回 err=5(拒绝访问)。

        结论：这两条「拿原始报告」的路都堵死了，唯一还通的通道是
        **键盘页 Raw Input** —— 固件只要上报，kbdhid 就一定翻成键盘事件，
        我们就能看见（哪怕是 VK_VOLUME_UP 这种）。所以这里只注册这两条。
        """
        devs = []
        for page, usage, tag in (
                (0x01, 0x06, "键盘页 0x01/0x06"),
                (0x0C, 0x01, "消费键页 0x0C/0x01（备用，本机大概率无用）")):
            devs.append(M.RAWINPUTDEVICE(page, usage, RIDEV_INPUTSINK, self.hwnd))
            print("  已注册通道: %s" % tag)
        ok = user32.RegisterRawInputDevices(
            (M.RAWINPUTDEVICE * len(devs))(*devs), len(devs),
            ctypes.sizeof(M.RAWINPUTDEVICE))
        if not ok:
            print("  [!] Raw Input 注册失败 err=%s" % ctypes.get_last_error())
            return False
        return True

    def dev_name(self, hdev):
        if hdev in self._dev_cache:
            return self._dev_cache[hdev]
        name = ""
        size = wt.UINT(0)
        user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME, None,
                                      ctypes.byref(size))
        if size.value:
            buf = ctypes.create_unicode_buffer(size.value + 1)
            if user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME,
                                             buf, ctypes.byref(size)):
                name = buf.value or ""
        if name:                       # 🔴 只在读到非空名字时缓存
            self._dev_cache[hdev] = name
        return name

    def handle(self, lparam):
        size = wt.UINT(0)
        hraw = wt.HANDLE(lparam)
        if user32.GetRawInputData(hraw, RID_INPUT, None,
                                  ctypes.byref(size), 24) != 0:
            return
        if not size.value:
            return
        buf = ctypes.create_string_buffer(size.value)
        if user32.GetRawInputData(hraw, RID_INPUT, buf,
                                  ctypes.byref(size), 24) == 0xFFFFFFFF:
            return
        raw = buf.raw
        rtype = u32(raw, OFF_RAW_TYPE)
        hdev = u64(raw, OFF_RAW_HDEV)
        if hdev == 0:                  # 注入回声，丢掉
            return
        name = self.dev_name(hdev)
        if "2717" not in name.lower():
            return                     # 只看遥控器，实体键盘一律忽略
        if rtype == RIM_TYPEKEYBOARD:
            vk = u16(raw, OFF_KB_VKEY)
            mc = u16(raw, OFF_KB_MAKECODE)
            fl = u16(raw, OFF_KB_FLAGS)
            msg = u32(raw, OFF_KB_MESSAGE)
            with self.lock:
                self.events.append(("键盘页", vk,
                                    VK_NAMES.get(vk, "vk=0x%02X" % vk),
                                    "scan=0x%02X flags=0x%02X msg=0x%04X"
                                    % (mc, fl, msg)))
            print("  [键盘页] %-14s scan=0x%02X flags=0x%02X"
                  % (VK_NAMES.get(vk, "vk=0x%02X" % vk), mc, fl))
            sys.stdout.flush()
        elif rtype == RIM_TYPEHID:
            cb = u32(raw, OFF_HID_CBDATA)
            data = raw[OFF_HID_DATA:OFF_HID_DATA + min(cb, 64)]
            with self.lock:
                self.events.append(("HID报告", -1, "原始报告", data.hex(" ")))
            print("  [HID报告] %s" % data.hex(" "))
            sys.stdout.flush()


def make_wndproc(listener):
    def wndproc(hwnd, msg, wp, lp):
        if msg == WM_INPUT:
            try:
                listener.handle(lp)
            except Exception:
                pass
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    return M.WNDPROC(wndproc)


# ===========================================================================
# 阶段 B 另一半：HID 原始报告直读
# ===========================================================================
class HidReader:
    """对每个能打开的 HID 集合起一个线程，同步 ReadFile 读原始报告。"""

    def __init__(self):
        self.threads = []
        self.counts = {}
        self.opened = []          # (path, handle, caps)
        self.failed = []          # (path, err)

    def try_open(self, path):
        """尽力打开：先共享读写，失败再只读。"""
        h = kernel32.CreateFileW(
            path, M.GENERIC_READ,
            M.FILE_SHARE_READ | M.FILE_SHARE_WRITE, None, M.OPEN_EXISTING, 0, None)
        if h == M.INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            self.failed.append((path, err))
            return None
        return h

    def start(self):
        paths = enum_hid_interfaces()
        if not paths:
            print("  [!] 没枚举到遥控器的 HID 接口")
            return
        for p in paths:
            h = self.try_open(p)
            if not h:
                continue
            info = read_caps(h)
            if info:
                caps, usages = info
                self.opened.append((p, h, caps, usages))
                print("  [可直读] 集合 UsagePage=0x%02X Usage=0x%02X "
                      "报告长度=%d 值usage数=%d"
                      % (caps.UsagePage, caps.Usage,
                         caps.InputReportByteLength, len(usages)))
            else:
                self.opened.append((p, h, None, []))
                print("  [可直读] 集合（读不到 caps）")
            sys.stdout.flush()
            t = threading.Thread(target=self._loop, args=(p, h), daemon=True)
            t.start()
            self.threads.append(t)

    def _loop(self, path, handle):
        size = 256
        buf = ctypes.create_string_buffer(size)
        got = wt.DWORD(0)
        while True:
            ok = kernel32.ReadFile(wt.HANDLE(handle), buf, size,
                                   ctypes.byref(got), None)
            if not ok or not got.value:
                time.sleep(0.01)
                continue
            data = buf.raw[:got.value]
            key = path
            self.counts[key] = self.counts.get(key, 0) + 1
            print("  [原始] %s  (%d 字节)" % (data.hex(" "), got.value))
            sys.stdout.flush()


# ===========================================================================
def main():
    selftest = 0
    for a in sys.argv[1:]:
        if a.startswith("--selftest"):
            parts = a.split("=")
            try:
                selftest = int(parts[1]) if len(parts) > 1 else 8
            except ValueError:
                selftest = 8

    print("=" * 70)
    print("遥控器「按了没反应的键」深度探针")
    print("目标：音量加 / 音量减 / 返回")
    print("=" * 70)
    print()
    print("【阶段 A】不用按键，先看固件声明了什么")
    print("-" * 70)
    sys.stdout.flush()

    paths = enum_hid_interfaces()
    print("  遥控器 HID 接口数：%d" % len(paths))
    for i, p in enumerate(paths):
        print("   #%d %s" % (i + 1, short_path(p)))
    if not paths:
        print()
        print("  [!] 一个都没枚举到 —— 遥控器可能处于蓝牙休眠。")
        print("      按一下遥控器上的任意键唤醒它，再重跑本探针。")
        input("按回车退出…")
        return 1
    sys.stdout.flush()

    # 逐个读声明能力
    declared = []
    read_ok = 0
    read_fail = 0
    for p in paths:
        h = kernel32.CreateFileW(
            p, M.GENERIC_READ,
            M.FILE_SHARE_READ | M.FILE_SHARE_WRITE, None, M.OPEN_EXISTING, 0, None)
        if h == M.INVALID_HANDLE_VALUE:
            read_fail += 1
            err = ctypes.get_last_error()
            tail = p.split("}")[-1] if "}" in p else ""
            print()
            print("   [!] 打不开（err=%s，5=被系统键盘驱动独占，正常）：集合后缀 %s"
                  % (err, tail or "无"))
            continue
        info = read_caps(h)
        if info:
            read_ok += 1
            caps, usages = info
            page_name = PAGE_NAMES.get(caps.UsagePage,
                                       "未知页 0x%02X" % caps.UsagePage)
            print()
            print("   集合 UsagePage=0x%02X(%s) Usage=0x%02X"
                  % (caps.UsagePage, page_name, caps.Usage))
            print("     输入报告长度 %d 字节 / 输出 %d / 特性 %d"
                  % (caps.InputReportByteLength, caps.OutputReportByteLength,
                     caps.FeatureReportByteLength))
            if usages:
                print("     声明的值用法（%d 个）：" % len(usages))
                for pg, u, bits in usages:
                    nm = CONSUMER_NAMES.get(u) if pg == 0x0C else None
                    if not nm:
                        nm = "usage=0x%02X" % u
                    star = "  <<< 关注" if (pg == 0x0C and u in WANT_USAGE) else ""
                    print("       page=0x%02X usage=0x%03X (%s) %d bit%s"
                          % (pg, u, nm, bits, star))
                    if pg == 0x0C and u in WANT_USAGE:
                        declared.append(WANT_USAGE[u])
            else:
                print("     没有任何值用法（只有按钮）")
        kernel32.CloseHandle(wt.HANDLE(h))
        sys.stdout.flush()

    print()
    print("-" * 70)
    print("  能读到描述符的集合：%d 个；打不开：%d 个" % (read_ok, read_fail))
    if declared:
        print("  [阶段 A 结论] 固件**声明了**这些键：%s"
              % "、".join(sorted(set(declared))))
        print("     -> 说明遥控器本来就能报，只是 Windows 没处理 -> 我们能接管")
    elif read_ok == 0:
        print("  [阶段 A 结论] 读不到描述符 —— 无法直接判断固件声明了什么。")
        print("     ⚠️ 注意：这不等于「没声明」。集合被系统键盘驱动独占(err=5)。")
        print("        所以判定只能靠阶段 B：按键时「键盘页」到底有没有事件。")
        print("        （固件只要上报，kbdhid 一定会翻成键盘事件，我们能看见）")
    else:
        print("  [阶段 A 结论] 读到描述符了，但里面**确实没有**音量/返回键。")
        print("     -> 遥控器通过蓝牙压根没打算上报这三个键（大概率只在红外模式用）")
    print()
    sys.stdout.flush()

    # ---------------- 阶段 B ----------------
    print("=" * 70)
    print("【阶段 B】实际按键，看有没有字节到达")
    print("-" * 70)

    listener = RawListener()
    hinst = kernel32.GetModuleHandleW(None)
    cls_name = "MiVoiceHiddenKeyProbe"
    wndproc = make_wndproc(listener)
    wc = M.WNDCLASSW()
    wc.style = 0x0008
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    wc.lpszClassName = cls_name
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(0, cls_name, "MiVoice Hidden Key Probe",
                                   0x00CF0000, 0, 0, 0, 0, None, None, hinst, None)
    if not hwnd:
        print("[!] 建窗口失败 err=%s" % ctypes.get_last_error())
        return 1
    listener.hwnd = hwnd
    listener.register()

    reader = HidReader()
    reader.start()
    print()
    if reader.opened:
        print("  提示：上面出现 [可直读] 说明原始报告通道打通了，")
        print("        按键时能直接看到原始字节，那是最强证据。")
    else:
        print("  [i] HID 直读打不开（被系统键盘驱动独占，err=5）——这是预期结果，")
        print("      不影响结论。判定只看上面的「键盘页」事件：")
        print("      固件只要上报，kbdhid 一定会翻成键盘事件给我们看见。")
    print()
    print("-" * 70)
    if selftest:
        print("【自检模式】%d 秒后自动退出。" % selftest)
    else:
        print("现在请照着按（每按一下等 1 秒，别连按）：")
        print("   1) 音量 +      按 3 下")
        print("   2) 音量 -      按 3 下")
        print("   3) 返回键      按 3 下")
        print("   4) 再按一次 OK 键（对照组，这个一定能收到）")
        print()
        print("按完回到这个黑窗口，按回车结束。")
    print("-" * 70)
    print()
    sys.stdout.flush()

    msg = wt.MSG()
    deadline = time.time() + (selftest if selftest else 180)
    stop = {"v": False}

    def wait_enter():
        try:
            input()
        except Exception:
            pass
        stop["v"] = True

    if not selftest:
        threading.Thread(target=wait_enter, daemon=True).start()

    while time.time() < deadline and not stop["v"]:
        r = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
        if r:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.02)

    # ---------------- 阶段 C 汇总 ----------------
    print()
    print("=" * 70)
    print("【阶段 C】结论")
    print("=" * 70)
    with listener.lock:
        ev = list(listener.events)

    print("  Raw Input 收到遥控器事件：%d 条" % len(ev))
    for src, vk, label, extra in ev:
        print("    [%s] %s  %s" % (src, label, extra))
    print()

    vks = sorted({vk for _, vk, _, _ in ev if vk >= 0})
    got_vol = [v for v in vks if v in (0xAF, 0xAE, 0xAD)]
    got_back = [v for v in vks if v in (0xA6, 0x08, 0xA7)]
    print("  收到的按键 vk（去重）：%s"
          % ("、".join("0x%02X" % v for v in vks) if vks else "无"))
    print()
    print("  ── 判定 ──")
    if got_vol:
        print("  [OK] 音量键**有上报**（vk=%s）"
              % "、".join("0x%02X" % v for v in got_vol))
        print("       -> 可以接管，直接映射成鼠标滚轮")
    else:
        print("  [X]  音量键**没有**产生任何键盘事件")
    if got_back:
        print("  [OK] 返回键**有上报**（vk=%s）"
              % "、".join("0x%02X" % v for v in got_back))
    else:
        print("  [X]  返回键**没有**产生任何键盘事件")
    print()

    if not vks:
        print("  >>> 一条遥控器事件都没收到。先排除这两种情况：")
        print("      1) 遥控器休眠了（按一下别的键唤醒，再重跑本探针）")
        print("      2) 全程没按到遥控器上")
    elif not got_vol and not got_back:
        print("  >>> 收到了别的键（例如 OK=0x0D），但音量/返回一个都没有。")
        print("      结合阶段 A 的事实（系统里只有 1 个 kbd 集合、没有消费键集合），")
        print("      结论：固件在蓝牙模式下**不上报**这三个键，用户态拿不到。")
        print("      下一步只有两条路：")
        print("        a) 从 BLE GATT 层直接订阅 HID 报告特征（绕开 Windows 键盘栈）")
        print("        b) 换思路：把遥控器上**能用**的键改成滚轮")
        print("           （TV 键 0xC0 / 菜单键 0x5D 都没有默认功能，正合适）")
    print()
    print("  参考键位：语音=F5(0x74) OK=Enter(0x0D) 上=0x26 下=0x28")
    print("            左=0x25 右=0x27 主页=Home(0x24) 菜单=Apps(0x5D) TV=0xC0")
    print("=" * 70)
    print()
    input("按回车退出…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
