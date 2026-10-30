# -*- coding: utf-8 -*-
"""
遥控语音 (MiVoice) v1.7
=======================
把「小米蓝牙语音遥控器」（RC003 同款，VID 0x2717 / PID 0x32B8）的语音键，
变成 WorkBuddy 对话框的语音输入。

日常用法（三步）：
  1. 点进 WorkBuddy 的输入框（让光标在对话框里）
  2. 按住遥控器语音键，说话
  3. 松手 -> WorkBuddy 自动转写，文字出现在输入框里
  4. 按遥控器 OK 键 -> 变成 Ctrl+Enter -> 发送

原理：
  按住语音键 -> 遥控器经 BLE ATVV 协议推麦克风音频
    -> 本程序（纯 ctypes 直连 Windows 蓝牙 GATT）收下音频
    -> IMA ADPCM 解码为 16kHz 单声道 PCM
    -> waveOut 写入 VB-CABLE 虚拟声卡的 "CABLE Input"
    -> Windows 里 "CABLE Output" 是默认麦克风 -> WorkBuddy 语音输入收到声音
  同时：检测到语音会话开始/结束 -> SendInput 模拟「长按 Alt」
    -> 触发 / 结束 WorkBuddy 的语音输入（按钮 tooltip：点击或长按 Alt 语音输入）

为什么不碰 F5：
  语音键在 Windows 上同时表现为键盘 F5。全局改 F5 会把实体键盘的 F5（刷新）
  一起改掉，所以本程序完全不拦键盘，只用 ATVV 音频会话的起止当扳机。

🔴 v1.7 的架构革新：「吞掉按下、放开抬起」
  v1.3~v1.6 一直用「钩子先吞 -> 等 Raw Input 确认设备归属 -> 决定发送还是补发」。
  🔴 这条链**在 Windows 上根本不成立**（2026-10-30 实测钉死）：
      被低级键盘钩子吞掉的按键，**Raw Input 一条都不会到**。
      探针实测：放行 3 条 / 全吞 0 条；
      日志印证：19:03 那 265 条被吞的 F5 重复，主日志零条 Raw Input。
  ✅ v1.7 的新办法（同样实测钉死）：
      **只吞「按下」，把「抬起」放行** —— 抬起那一条 Raw Input 照样会到，
      而它带的 hDevice 就是**这一下按键的确定设备归属**。
      于是：
        按下 -> 吞掉（前台程序收不到，不会插入换行）
        抬起 -> 放行 -> Raw Input 到达 -> 拿到 hDevice
             -> 是遥控器 -> 发 ctrl+enter
             -> 是别的设备 -> 把刚才吞掉的按下**补发**回去
  另外还修了 v1.6 的一个真 bug：SendInput 注入的按键会产生 hdev=0 的
  Raw Input 回声，v1.6 把它当成「实体键盘按了发送键」，污染判据，
  导致**第一次之后每一次都失败**。v1.7 直接丢弃 hdev=0 的事件。

前置条件（一次性）：
  - 安装 VB-CABLE 虚拟声卡（管理员 + 重启一次）
  - Windows 声音设置里把「输入」默认设备设为 CABLE Output

零依赖：只用 Python 标准库 ctypes 调 Win32 API（不需要 pywin32 / pystray）。

命令行：
  python mivoice.py              正常启动（托盘常驻）
  python mivoice.py --probe      连接自检：打印能力协商，录一段会话存成 wav
  python mivoice.py --alt-test   模拟一次 Alt 触发，用来验证 WorkBuddy 有反应
  python mivoice.py --status     一次性打印：遥控器 / VB-CABLE / ATVV 是否就绪
  python mivoice.py --selftest N 启动 N 秒后自动退出
  python mivoice.py --debug      详细日志
"""

import configparser
import ctypes
import ctypes.wintypes as wt
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback

# ===========================================================================
# 0. 基本路径 / 日志
# ===========================================================================
APP_NAME = "遥控语音"
APP_VER = "1.7"
HERE = os.path.dirname(os.path.abspath(__file__))
INI_PATH = os.path.join(HERE, "mivoice.ini")
LOG_PATH = os.path.join(HERE, "mivoice.log")
KEYLOG_PATH = os.path.join(HERE, "mivoice_keys.log")
# 运行状态文件：写上「现在跑的是哪个版本、哪个 PID」。
# 用途：托盘图标被折叠进溢出面板时，用户找不到图标也不知道跑的是哪一版，
# 这个文件能让排错脚本直接问出答案（不用靠日志里的启动行猜）。
STATE_PATH = os.path.join(HERE, "mivoice.state")

_log_fp = None
_log_path = LOG_PATH     # 当前写入的日志文件（回归测试会重定向，别污染正式日志）
_debug = False


def set_log_path(path):
    """把日志改写到别的文件。

    🔴 回归测试专用：tests\\run_all.py 会调用它，把测试产生的日志
    写进 mivoice_test.log —— 否则木木看正式日志时会看到一堆
    「发送键已转换」之类的假记录，分不清哪条是真事。
    """
    global _log_fp, _log_path
    try:
        if _log_fp is not None:
            _log_fp.close()
    except Exception:
        pass
    _log_fp = None
    _log_path = path


def log(msg):
    """写日志。pythonw 启动时没有控制台，日志是唯一的排查线索。"""
    global _log_fp
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if _log_fp is None:
        try:
            _log_fp = open(_log_path, "a", encoding="utf-8")
        except Exception:
            return
    try:
        _log_fp.write(line + "\n")
        _log_fp.flush()
    except Exception:
        pass


def dbg(msg):
    if _debug:
        log("[dbg] " + str(msg))


# ===========================================================================
# 1. Win32 常量
# ===========================================================================
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_HOTKEY = 0x0312
WM_APP_STATE = 0x8003          # 自定义：ATVV 线程通知主线程刷托盘
WM_INPUT = 0x00FF              # Raw Input
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# ---- OK 键 -> 发送（Ctrl+Enter）相关 ----
WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 0x10          # 本程序自己注入的事件带这个标志，必须放行防递归
LLKHF_ALTDOWN = 0x20          # 事件发生时 Alt 正按着（自动重复的 F5 会带这个）
VK_RETURN = 0x0D
VK_LWIN, VK_RWIN = 0x5B, 0x5C
RIDEV_INPUTSINK = 0x00000100   # 窗口不在前台也收 WM_INPUT
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
# 🔴 v1.6 更正（此前判断错了，这里写清楚免得再被带偏）：
#    RIDI_DEVICENAME 的**官方值就是 0x20000007**，低位那个 7 是常量本身，
#    **不是**「名字索引」。别再按「扫 0..15 个索引」去理解它。
#    （0x20000000 是 RIDI_DEVICEINFO，返回的是 RAWINPUTDEVINFO 结构，不是名字。）
#    下面这个 BASE 只用于「多路尝试」：万一 0x20000007 这次读不出来，
#    再逐个试别的低位值 —— 实测有些 HID 集合只有某个低位能读出名字。
RIDI_DEVICENAME_BASE = 0x20000000
RIM_TYPEKEYBOARD = 1
GCS_COMPSTR = 0x0008

# 托盘
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x01, 0x02, 0x04, 0x10
TRAY_UID = 0x7766
WM_TRAY = 0x8002

# 菜单
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_GRAYED = 0x00000001
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_LEFTALIGN = 0x0000000
TPM_BOTTOMALIGN = 0x0020

# 热键
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID_QUIT = 0xB2

# 单实例保护（命名互斥体）
# 允许用环境变量 MIVOICE_MUTEX 覆盖 —— 只给自动化测试用，
# 平时不要设，否则会出现「开了两个实例同时抢键盘钩子」。
MUTEX_NAME = os.environ.get("MIVOICE_MUTEX", "Local\\MiVoice_SingleInstance_v1")
ERROR_ALREADY_EXISTS = 183

SM_CXSMICON = 49

# 输入模拟
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001   # 扩展键（方向键/Home/Menu 等）必须带上，否则键位不对
VK_MENU = 0x12        # Alt（通用）
VK_CONTROL = 0x11     # Ctrl（通用）
VK_SHIFT = 0x10       # Shift（通用）
VK_LMENU = 0xA4       # 左 Alt —— Electron/Chromium 认这个才当「真按住」
VK_LCONTROL = 0xA2    # 左 Ctrl
VK_LSHIFT = 0xA0      # 左 Shift
VK_F5 = 0x74

# 修饰键必须带硬件扫描码，Chromium 靠它判断左右键并维持按住状态。
# 只发 VK + wScan=0 时，Electron 会把它当成一次性的虚拟点按（表现为「只亮一下」）。
SCAN_BY_VK = {
    VK_MENU: 0x38, VK_LMENU: 0x38,
    VK_CONTROL: 0x1D, VK_LCONTROL: 0x1D,
    VK_SHIFT: 0x2A, VK_LSHIFT: 0x2A,
}

# 托盘图标字形（Segoe MDL2 Assets：E720 = Microphone）
FONT_ICON = "Segoe MDL2 Assets"
GLYPH_MIC = "\ue720"

# ATVV 协议常量（Google Android TV Voice，经 wangyl/mi-input 真机验证）
ATVV_SERVICE = "{ab5e0001-5a21-4f05-bc7d-af01f617b664}"
ATTR_WRITE = 0x0301     # ab5e0002 命令写入
ATTR_AUDIO = 0x0303     # ab5e0003 音频通知
ATTR_CONTROL = 0x0306   # ab5e0004 控制通知

OP_STREAM_STOP = 0x00
OP_STREAM_START = 0x04
OP_MIC_OPEN_REQ = 0x08
OP_CODEC_SYNC = 0x0A
OP_CAPABILITIES = 0x0B

SAMPLE_RATE = 16000
FRAME_BYTES = 120       # IMA ADPCM 帧长（每 120 字节一切）

# ===========================================================================
# 2. ctypes 声明
# ===========================================================================
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
btapis = ctypes.WinDLL("BluetoothApis.dll")

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
ERROR_ACCESS_DENIED = 5

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

MMSYSERR_NOERROR = 0
WAVE_MAPPER = 0xFFFFFFFF
CALLBACK_EVENT = 0x50000
WHDR_DONE = 0x00000001

WS_POPUP = 0x80000000


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid_from_string(s: str) -> GUID:
    m = re.match(
        r"\{([0-9A-Fa-f]{8})-([0-9A-Fa-f]{4})-([0-9A-Fa-f]{4})-([0-9A-Fa-f]{2})"
        r"([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})"
        r"([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})\}", s)
    g = GUID()
    g.Data1 = int(m.group(1), 16)
    g.Data2 = int(m.group(2), 16)
    g.Data3 = int(m.group(3), 16)
    g.Data4 = (ctypes.c_ubyte * 8)(*(int(m.group(4 + i), 16) for i in range(8)))
    return g


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wt.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATAW(ctypes.Structure):
    """64 位下 sizeof == 976。cbSize 填错会让图标静默不出现。"""
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD),
                ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64),
                ("dwInfoFlags", wt.UINT), ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wt.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wt.WORD),
        ("nChannels", wt.WORD),
        ("nSamplesPerSec", wt.DWORD),
        ("nAvgBytesPerSec", wt.DWORD),
        ("nBlockAlign", wt.WORD),
        ("wBitsPerSample", wt.WORD),
        ("cbSize", wt.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wt.DWORD),
        ("dwBytesRecorded", wt.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wt.DWORD),
        ("dwLoops", wt.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


# ---- ATVV GATT 结构（照搬 mi-input 已验证布局）----
class BTH_LE_UUID(ctypes.Structure):
    class _V(ctypes.Union):
        _fields_ = [("ShortUuid", ctypes.c_ushort), ("LongUuid", GUID)]

    _anonymous_ = ("v",)
    _fields_ = [("IsShortUuid", ctypes.c_ubyte), ("v", _V)]


class BTH_LE_GATT_SERVICE(ctypes.Structure):
    _fields_ = [("ServiceUuid", BTH_LE_UUID), ("AttributeHandle", ctypes.c_ushort)]


class BTH_LE_GATT_CHARACTERISTIC(ctypes.Structure):
    _fields_ = [
        ("ServiceHandle", ctypes.c_ushort),
        ("CharacteristicUuid", BTH_LE_UUID),
        ("AttributeHandle", ctypes.c_ushort),
        ("CharacteristicValueHandle", ctypes.c_ushort),
        ("IsBroadcastable", ctypes.c_ubyte),
        ("IsReadable", ctypes.c_ubyte),
        ("IsWritable", ctypes.c_ubyte),
        ("IsWritableWithoutResponse", ctypes.c_ubyte),
        ("IsSignedWritable", ctypes.c_ubyte),
        ("IsNotifiable", ctypes.c_ubyte),
        ("IsIndicatable", ctypes.c_ubyte),
        ("HasExtendedProperties", ctypes.c_ubyte),
    ]


class BTH_LE_GATT_CHARACTERISTIC_VALUE(ctypes.Structure):
    _fields_ = [("DataSize", ctypes.c_ulong), ("Data", ctypes.c_ubyte * 1)]


class BLUETOOTH_GATT_VALUE_CHANGED_EVENT(ctypes.Structure):
    _fields_ = [
        ("ChangedAttributeHandle", ctypes.c_ushort),
        ("pad", ctypes.c_ushort),
        ("CharacteristicValueDataSize", ctypes.c_size_t),
        ("CharacteristicValue", ctypes.POINTER(BTH_LE_GATT_CHARACTERISTIC_VALUE)),
    ]


class _EVENT_REGISTRATION(ctypes.Structure):
    _fields_ = [("NumCharacteristics", ctypes.c_ushort),
                ("Characteristics", BTH_LE_GATT_CHARACTERISTIC * 1)]


PFN_GATT_CALLBACK = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

# ---- SendInput ----
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    _anonymous_ = ("i",)
    _fields_ = [("type", wt.DWORD), ("i", _I)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    """x64 下 sizeof == 24（dwExtraInfo 是 ULONG_PTR）。"""
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


# ---- setupapi / bluetooth / user32 / winmm 声明 ----
setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                          wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                                 ctypes.POINTER(GUID), wt.DWORD,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE,
                                                     ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                     ctypes.c_void_p, wt.DWORD,
                                                     ctypes.POINTER(wt.DWORD),
                                                     ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.restype = wt.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]

kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetModuleHandleW.restype = wt.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.GetCurrentThreadId.argtypes = []
user32.PostThreadMessageW.restype = wt.BOOL
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [wt.LPVOID, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

btapis.BluetoothGATTGetServices.restype = ctypes.c_long
btapis.BluetoothGATTGetServices.argtypes = [
    wt.HANDLE, ctypes.c_ushort, ctypes.POINTER(BTH_LE_GATT_SERVICE),
    ctypes.POINTER(ctypes.c_ushort), wt.ULONG]
btapis.BluetoothGATTGetCharacteristics.restype = ctypes.c_long
btapis.BluetoothGATTGetCharacteristics.argtypes = [
    wt.HANDLE, ctypes.POINTER(BTH_LE_GATT_SERVICE), ctypes.c_ushort,
    ctypes.POINTER(BTH_LE_GATT_CHARACTERISTIC), ctypes.POINTER(ctypes.c_ushort), wt.ULONG]
btapis.BluetoothGATTRegisterEvent.restype = ctypes.c_long
btapis.BluetoothGATTRegisterEvent.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, PFN_GATT_CALLBACK,
    ctypes.c_void_p, ctypes.POINTER(wt.HANDLE), wt.ULONG]
btapis.BluetoothGATTUnregisterEvent.restype = ctypes.c_long
btapis.BluetoothGATTUnregisterEvent.argtypes = [wt.HANDLE]
btapis.BluetoothGATTSetCharacteristicValue.restype = ctypes.c_long
btapis.BluetoothGATTSetCharacteristicValue.argtypes = [
    wt.HANDLE, ctypes.POINTER(BTH_LE_GATT_CHARACTERISTIC),
    ctypes.POINTER(BTH_LE_GATT_CHARACTERISTIC_VALUE), ctypes.c_void_p, wt.ULONG]

user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.RegisterClassW.restype = wt.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetMessageW.restype = ctypes.c_int
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.TranslateMessage.restype = wt.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.RegisterHotKey.restype = wt.BOOL
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.SetTimer.restype = ctypes.c_void_p
user32.SetTimer.argtypes = [wt.HWND, ctypes.c_void_p, wt.UINT, wt.LPVOID]
user32.KillTimer.restype = wt.BOOL
user32.KillTimer.argtypes = [wt.HWND, ctypes.c_void_p]
user32.DestroyWindow.restype = wt.BOOL
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PostMessageW.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SetForegroundWindow.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetRawInputData.restype = wt.UINT
user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p,
                                   ctypes.POINTER(wt.UINT), wt.UINT]
user32.GetRawInputDeviceInfoW.restype = wt.UINT
user32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, ctypes.c_void_p,
                                          ctypes.POINTER(wt.UINT)]
user32.RegisterRawInputDevices.restype = wt.BOOL
user32.RegisterRawInputDevices.argtypes = [ctypes.c_void_p, wt.UINT, wt.UINT]
user32.SetWindowsHookExW.restype = wt.HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                     wt.HINSTANCE, wt.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
user32.IsIconic.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.GetCursorPos.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetProcessDPIAware.restype = wt.BOOL
user32.SetProcessDPIAware.argtypes = []
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.EnumWindows.restype = wt.BOOL
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.SendInput.restype = wt.UINT
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.AppendMenuW.restype = wt.BOOL
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.CreatePopupMenu.restype = wt.HMENU
user32.CreatePopupMenu.argtypes = []
user32.TrackPopupMenu.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.POINTER(RECT)]
user32.DestroyMenu.restype = wt.BOOL
user32.DestroyMenu.argtypes = [wt.HMENU]

shell32.Shell_NotifyIconW.restype = wt.BOOL
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]

gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.DeleteDC.restype = wt.BOOL
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT,
                               ctypes.c_void_p]
gdi32.CreateSolidBrush.restype = wt.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wt.DWORD]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.SetTextColor.restype = wt.DWORD
gdi32.SetTextColor.argtypes = [wt.HDC, wt.DWORD]
gdi32.CreateFontW.restype = wt.HFONT
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [ctypes.c_ubyte] * 8 + [wt.LPCWSTR]

user32.FillRect.restype = ctypes.c_int
user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(RECT), wt.HBRUSH]
user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(RECT), wt.UINT]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.CreateIconIndirect.restype = wt.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.DestroyIcon.restype = wt.BOOL
user32.DestroyIcon.argtypes = [wt.HICON]

winmm.waveOutGetNumDevs.restype = wt.UINT
winmm.waveOutGetNumDevs.argtypes = []
winmm.waveOutGetDevCapsW.restype = wt.UINT
winmm.waveOutGetDevCapsW.argtypes = [wt.UINT, ctypes.c_void_p, wt.UINT]
winmm.waveOutOpen.restype = wt.UINT
winmm.waveOutOpen.argtypes = [ctypes.POINTER(wt.HANDLE), wt.UINT,
                              ctypes.POINTER(WAVEFORMATEX), wt.HANDLE,
                              ctypes.c_size_t, wt.DWORD]
winmm.waveOutPrepareHeader.restype = wt.UINT
winmm.waveOutPrepareHeader.argtypes = [wt.HANDLE, ctypes.POINTER(WAVEHDR), wt.UINT]
winmm.waveOutUnprepareHeader.restype = wt.UINT
winmm.waveOutUnprepareHeader.argtypes = [wt.HANDLE, ctypes.POINTER(WAVEHDR), wt.UINT]
winmm.waveOutWrite.restype = wt.UINT
winmm.waveOutWrite.argtypes = [wt.HANDLE, ctypes.POINTER(WAVEHDR), wt.UINT]
winmm.waveOutReset.restype = wt.UINT
winmm.waveOutReset.argtypes = [wt.HANDLE]
winmm.waveOutClose.restype = wt.UINT
winmm.waveOutClose.argtypes = [wt.HANDLE]
winmm.waveInGetNumDevs.restype = wt.UINT
winmm.waveInGetNumDevs.argtypes = []


# ===========================================================================
# 3. 配置（mivoice.ini，首次运行自动生成）
# ===========================================================================
DEFAULTS = {
    "general": {
        "device_match": "2717",       # ATVV 设备路径匹配串（遥控器 VID 0x2717）
        "output_device": "CABLE Input",  # waveOut 目标（VB-CABLE 的 Input 端）
        "gain": "3.0",                # 音频增益（遥控器电平偏低，线性放大后限幅）
        "enabled": "1",               # 0 = 暂停（托盘菜单可切）
        "alt_key": "alt",             # 触发 WorkBuddy 语音输入的键：alt / ctrl / shift / f5
        "alt_mode": "hold",           # hold=按住说话（会话期间一直按住）/ tap=点按切换
        "alt_tap_ms": "60",           # tap 模式下每次按住的时长（毫秒）
        "alt_pre_ms": "150",          # hold 模式下先按多久再等音频（有些框架要长按才触发）
        "focus_workbuddy": "0",       # 1 = 会话开始时把 WorkBuddy 窗口提到前台
        "focus_title": "WorkBuddy",   # 窗口标题匹配串（focus_workbuddy=1 时生效）
        # ---- OK 键 -> 发送 ----
        "ok_to_send": "1",            # 1 = 遥控器 OK 键（Enter）触发发送快捷键
        "send_keys": "ctrl+enter",    # 要发送的组合键，对应 WorkBuddy 的发送设置
        "ok_send_title": "WorkBuddy", # 只在前台窗口标题含此串时转换（空=任意窗口，危险）
        # 哪个物理键触发发送。可以写 enter / space / f7 / up / volumeup /
        # 0x74 …（用 tools\scan_remote_keys.py 查出遥控器各键的 vk 后填这里）
        "send_key": "enter",
        "remote_active_ms": "10000",  # 原始事件没等到时的兜底窗口：最近多少毫秒内
                                      # 有遥控器活动，就认为这一下是遥控器按的
        "send_fallback_ms": "30000", # 看门狗兜底窗口（专用）。抬起确认迟迟不来时，
                                      # 「最近 30 秒内用过遥控器」就算遥控器按的。
                                      # 🔴 用它而不是 remote_active_ms：说话到按 OK 键
                                      #    间隔经常超过 10 秒，窗口太紧会漏判。
        "send_confirm_ms": "800",    # 🔴 v1.7：抬起之后等多久去问「抬起那条 Raw Input
                                      #    到了没」。实测是毫秒级到达的，800ms 只是保险丝。
                                      #    （v1.6 的 400 是从「按下」算起，人按住回车
                                      #      稍久一点就会误超时，所以 v1.7 改成从抬起算起。）
        "hide_remote_f5": "1",        # 1 = 藏掉遥控器语音键(F5)，否则 WorkBuddy 会误判成组合键
        "hotkey_quit": "Ctrl+Shift+M",  # 退出热键（实测 Ctrl+Alt+M/V/O/Q/X/Z/L 已被占用）
        "autostart": "0",             # 开机自启（托盘菜单可切，会写回本文件）
    },
}


class Config:
    def __init__(self):
        self.cp = configparser.ConfigParser()
        for sec, kv in DEFAULTS.items():
            self.cp[sec] = dict(kv)
        if os.path.exists(INI_PATH):
            try:
                self.cp.read(INI_PATH, encoding="utf-8")
            except Exception as e:
                log("配置文件读取失败，使用默认值：%r" % (e,))
        else:
            try:
                with open(INI_PATH, "w", encoding="utf-8") as f:
                    self.cp.write(f)
                log("已生成默认配置文件 %s" % INI_PATH)
            except Exception as e:
                log("配置文件写入失败：%r" % (e,))

    def s(self, sec, key):
        return self.cp.get(sec, key, fallback=DEFAULTS[sec][key])

    def i(self, sec, key):
        try:
            return int(float(self.s(sec, key)))
        except Exception:
            return int(DEFAULTS[sec][key])

    def f(self, sec, key):
        try:
            return float(self.s(sec, key))
        except Exception:
            return float(DEFAULTS[sec][key])

    def b(self, sec, key):
        return self.s(sec, key).strip().lower() in ("1", "true", "yes", "on")

    def set(self, sec, key, value):
        try:
            if not self.cp.has_section(sec):
                self.cp.add_section(sec)
            self.cp.set(sec, key, str(value))
            with open(INI_PATH, "w", encoding="utf-8") as f:
                self.cp.write(f)
            return True
        except Exception as e:
            log("配置写入失败：%r" % (e,))
            return False


# ===========================================================================
# 4. IMA ADPCM 解码（与遥控器固件一致：高 nibble 在前，120 字节一帧）
# ===========================================================================
class ImaAdpcmDecoder:
    STEP_TABLE = (
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
        50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
        253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
        1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
        3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
        11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086,
        29794, 32767,
    )
    INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)

    def __init__(self):
        self.reset()

    def reset(self):
        self.predictor = 0
        self.step_index = 0

    def _nibble(self, n: int) -> int:
        step = self.STEP_TABLE[self.step_index]
        diff = step >> 3
        if n & 1:
            diff += step >> 2
        if n & 2:
            diff += step >> 1
        if n & 4:
            diff += step
        if n & 8:
            diff = -diff
        self.predictor += diff
        if self.predictor > 32767:
            self.predictor = 32767
        elif self.predictor < -32768:
            self.predictor = -32768
        self.step_index += self.INDEX_TABLE[n & 7]
        if self.step_index < 0:
            self.step_index = 0
        elif self.step_index > 88:
            self.step_index = 88
        return self.predictor

    def decode(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            out += self._nibble((b >> 4) & 0x0F).to_bytes(2, "little", signed=True)
            out += self._nibble(b & 0x0F).to_bytes(2, "little", signed=True)
        return bytes(out)


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """线性增益 + 限幅。gain<=1 时原样返回。"""
    if gain <= 1.001:
        return pcm
    out = bytearray(len(pcm))
    mv = memoryview(pcm).cast("h")
    ov = memoryview(out).cast("h")
    for i, s in enumerate(mv):
        v = int(s * gain)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        ov[i] = v
    return bytes(out)


# ===========================================================================
# 5. setupapi 辅助（找 ATVV 设备接口、打开句柄）
# ===========================================================================
def find_atvv_interface(match: str = "2717"):
    """枚举 ATVV 服务接口，返回第一个匹配 match 子串的设备路径。"""
    guid = guid_from_string(ATVV_SERVICE)
    dev_list = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not dev_list:
        return None
    result = None
    idx = 0
    while True:
        iface = SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
        if not setupapi.SetupDiEnumDeviceInterfaces(
                dev_list, None, ctypes.byref(guid), idx, ctypes.byref(iface)):
            break
        needed = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(dev_list, ctypes.byref(iface),
                                                  None, 0, ctypes.byref(needed), None)
        if needed.value:
            buf = ctypes.create_string_buffer(needed.value)
            cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buf, ctypes.byref(wt.DWORD(cb_size)), 4)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    dev_list, ctypes.byref(iface), buf, needed.value, None, None):
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                if path and (not match or match.lower() in path.lower()):
                    result = path
                    break
        idx += 1
    setupapi.SetupDiDestroyDeviceInfoList(dev_list)
    return result


def open_device(path: str):
    """打开设备句柄；先试读写，被拒则降级只读。"""
    handle = kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                  OPEN_EXISTING, 0, None)
    if handle == INVALID_HANDLE_VALUE and ctypes.get_last_error() == ERROR_ACCESS_DENIED:
        handle = kernel32.CreateFileW(path, GENERIC_READ,
                                      FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                      OPEN_EXISTING, 0, None)
    return handle


# ===========================================================================
# 6. ATVV 客户端
# ===========================================================================
class AtvvSession:
    def __init__(self):
        self.adpcm = bytearray()
        self.pcm = bytearray()
        self.decoder = ImaAdpcmDecoder()
        self.frames = 0

    def add_audio(self, data: bytes, gain: float = 1.0) -> bytes:
        new_pcm = bytearray()
        self.adpcm.extend(data)
        while len(self.adpcm) >= FRAME_BYTES:
            frame = bytes(self.adpcm[:FRAME_BYTES])
            del self.adpcm[:FRAME_BYTES]
            new_pcm += self.decoder.decode(frame)
            self.frames += 1
        if gain > 1.001:
            new_pcm = apply_gain(bytes(new_pcm), gain)
        self.pcm += new_pcm
        return bytes(new_pcm)

    @property
    def duration(self):
        return len(self.pcm) / 2 / SAMPLE_RATE


class AtvvRemote:
    """连接遥控器 ATVV 服务，管理语音会话。

    回调：on_begin() / on_audio(pcm16) / on_end(session)
    协议流程（mi-input 真机验证）：get_caps -> mic_open_request -> MIC_OPEN
      -> stream_start -> [音频] -> stream_stop -> MIC_CLOSE
    """

    def __init__(self, log_fn=log, on_begin=None, on_audio=None, on_end=None):
        self.log = log_fn
        self.on_begin = on_begin
        self.on_audio = on_audio
        self.on_end = on_end
        self._handle = wt.HANDLE()
        self._chars = {}
        self._event_handles = []
        self._keep_cb = []
        self._keep_reg = []
        self.control_q = queue.Queue()
        self.audio_q = queue.Queue()
        self.protocol_version = 0
        self.selected_codec = 0
        self.session_id = 0
        self._session = None
        self._stop = threading.Event()
        self._connected = False
        self.last_event_ts = 0.0
        self.reconnects = 0

    # ---- 连接 ----
    def connect(self, match: str = "2717") -> None:
        path = find_atvv_interface(match)
        if not path:
            raise OSError("未找到 ATVV 服务接口（遥控器未连接/已休眠？）")
        dbg("ATVV 接口: %s" % path)
        h = open_device(path)
        if h == INVALID_HANDLE_VALUE:
            raise OSError("打开设备失败 err=%s" % ctypes.get_last_error())
        self._handle = h

        n = ctypes.c_ushort(0)
        btapis.BluetoothGATTGetServices(h, 0, None, ctypes.byref(n), 0)
        services = (BTH_LE_GATT_SERVICE * n.value)()
        actual = ctypes.c_ushort(0)
        btapis.BluetoothGATTGetServices(h, n.value, services, ctypes.byref(actual), 0)
        svc = services[0]

        n2 = ctypes.c_ushort(0)
        btapis.BluetoothGATTGetCharacteristics(h, ctypes.byref(svc), 0, None,
                                               ctypes.byref(n2), 0)
        chars = (BTH_LE_GATT_CHARACTERISTIC * n2.value)()
        n3 = ctypes.c_ushort(0)
        btapis.BluetoothGATTGetCharacteristics(h, ctypes.byref(svc), n2.value, chars,
                                               ctypes.byref(n3), 0)
        for c in chars[:n3.value]:
            self._chars[c.AttributeHandle] = c

        for attr in (ATTR_AUDIO, ATTR_CONTROL):
            self._subscribe(attr)

        self._connected = True
        self.last_event_ts = time.time()
        self.log("ATVV 已连接（%d 个特征）" % len(self._chars))
        # 主动握手获取能力
        self.write(bytes([0x0A, 0x01, 0x00, 0x00, 0x03, 0x03]))

    def _subscribe(self, attr: int) -> None:
        reg = _EVENT_REGISTRATION()
        reg.NumCharacteristics = 1
        reg.Characteristics[0] = self._chars[attr]

        def on_event(_type, param, _ctx):
            try:
                ev = ctypes.cast(param,
                                 ctypes.POINTER(BLUETOOTH_GATT_VALUE_CHANGED_EVENT)).contents
                val = ev.CharacteristicValue.contents
                data = ctypes.string_at(ctypes.addressof(val) + 4, val.DataSize)
                if ev.ChangedAttributeHandle == ATTR_AUDIO:
                    self.audio_q.put(data)
                else:
                    self.control_q.put(data)
            except Exception:
                pass

        cb = PFN_GATT_CALLBACK(on_event)
        eh = wt.HANDLE()
        hr = btapis.BluetoothGATTRegisterEvent(
            self._handle, ctypes.c_void_p(0),
            ctypes.byref(reg), cb, None, ctypes.byref(eh), 0)
        if hr != 0:
            raise OSError("RegisterEvent(0x%04X) 失败: 0x%08X" % (attr, hr & 0xFFFFFFFF))
        self._event_handles.append(eh)
        self._keep_cb.append(cb)
        self._keep_reg.append(reg)

    def write(self, data: bytes) -> None:
        buf = ctypes.create_string_buffer(
            ctypes.sizeof(BTH_LE_GATT_CHARACTERISTIC_VALUE) + len(data))
        val = ctypes.cast(buf, ctypes.POINTER(BTH_LE_GATT_CHARACTERISTIC_VALUE))
        val.contents.DataSize = len(data)
        ctypes.memmove(val.contents.Data, data, len(data))
        hr = btapis.BluetoothGATTSetCharacteristicValue(
            self._handle, ctypes.byref(self._chars[ATTR_WRITE]), val, None, 0)
        if hr != 0:
            raise OSError("写入命令失败: 0x%08X" % (hr & 0xFFFFFFFF))

    def close(self):
        if self._session is not None:
            try:
                cmd = bytes([0x0D, self.session_id]) if self.protocol_version >= 0x0100 \
                    else bytes([0x0D])
                self.write(cmd)
                self._session = None
            except OSError:
                pass
        for eh in self._event_handles:
            try:
                btapis.BluetoothGATTUnregisterEvent(eh)
            except Exception:
                pass
        self._event_handles.clear()
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = wt.HANDLE()
        self._connected = False

    @property
    def connected(self):
        return self._connected

    # ---- 事件处理主循环（独立线程）----
    def process_loop(self, gain: float = 1.0, idle_reconnect_s: float = 25.0):
        while not self._stop.is_set() and self._connected:
            progressed = False
            try:
                while True:
                    msg = self.control_q.get_nowait()
                    self.last_event_ts = time.time()
                    self._on_control(msg, gain)
                    progressed = True
            except queue.Empty:
                pass
            try:
                while True:
                    data = self.audio_q.get_nowait()
                    self.last_event_ts = time.time()
                    if self._session is not None:
                        pcm = self._session.add_audio(data, gain)
                        if pcm and self.on_audio:
                            try:
                                self.on_audio(pcm)
                            except Exception as e:
                                self.log("音频回调异常: %r" % (e,))
                    progressed = True
            except queue.Empty:
                pass
            if not progressed:
                # 不在这里做「空闲重连」：遥控器休眠后链路本来就静默，按时重连
                # 只会每 25 秒刷一遍日志、还耗蓝牙。改成由 Raw Input 感知到
                # 遥控器按键时再触发重连（见 App._on_remote_activity）。
                time.sleep(0.01)

    def stop(self):
        self._stop.set()

    def _on_control(self, msg: bytes, gain: float):
        op = msg[0] if msg else -1
        if op == OP_CAPABILITIES and len(msg) >= 3:
            self.protocol_version = (msg[1] << 8) | msg[2]
            codecs = msg[3] if self.protocol_version >= 0x0100 else 0x02
            if self.protocol_version >= 0x0100 and codecs == 0 and len(msg) >= 9 \
                    and (msg[4] & 0x03):
                codecs = msg[4]
            self.selected_codec = 0x02 if (codecs & 0x02) else 0x01
            self.log("ATVV 能力: 版本 0x%04X codec=%d"
                     % (self.protocol_version, self.selected_codec))
        elif op == OP_MIC_OPEN_REQ:
            dbg("mic_open_request %s" % msg.hex())
            if self._session is None:
                self._session = AtvvSession()
            try:
                cmd = bytes([0x0C, 0x00]) if self.protocol_version >= 0x0100 \
                    else bytes([0x0C, 0x00, self.selected_codec])
                self.write(cmd)
            except OSError as e:
                self.log("MIC_OPEN 应答失败: %s" % e)
        elif op == OP_STREAM_START:
            if len(msg) >= 2:
                self.session_id = msg[1]
            if self._session is None:
                self._session = AtvvSession()
            self.log("● 语音会话开始（按住语音键说话）")
            if self.on_begin:
                try:
                    self.on_begin()
                except Exception as e:
                    self.log("begin 回调异常: %r" % (e,))
        elif op == OP_STREAM_STOP:
            if self._session is None:
                return  # 重复 stop / MIC_CLOSE 的 ack，防乒乓
            self.log("■ 语音会话结束（松手）")
            try:
                cmd = bytes([0x0D, self.session_id]) if self.protocol_version >= 0x0100 \
                    else bytes([0x0D])
                self.write(cmd)
            except OSError:
                pass
            # 排空残余音频包
            time.sleep(0.15)
            try:
                while True:
                    pcm = self._session.add_audio(self.audio_q.get_nowait(), gain)
                    if pcm and self.on_audio:
                        try:
                            self.on_audio(pcm)
                        except Exception as e:
                            self.log("音频回调异常: %r" % (e,))
            except queue.Empty:
                pass
            sess = self._session
            self._session = None
            self.log("  会话时长 %.2fs / %d 帧" % (sess.duration, sess.frames))
            if self.on_end:
                try:
                    self.on_end(sess)
                except Exception as e:
                    self.log("end 回调异常: %r" % (e,))
        else:
            dbg("控制消息 opcode=0x%02X %s" % (op, msg.hex()))


# ===========================================================================
# 7. waveOut 播放器（PCM -> CABLE Input）
# ===========================================================================
class WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wt.WORD), ("wPid", wt.WORD),
        ("vDriverVersion", wt.UINT), ("szPname", wt.WCHAR * 32),
        ("dwFormats", wt.DWORD), ("wChannels", wt.WORD),
        ("wReserved1", wt.WORD), ("dwSupport", wt.DWORD),
    ]


def list_output_devices():
    names = []
    n = winmm.waveOutGetNumDevs()
    caps = WAVEOUTCAPSW()
    for i in range(n):
        if winmm.waveOutGetDevCapsW(i, ctypes.byref(caps),
                                    ctypes.sizeof(caps)) == MMSYSERR_NOERROR:
            names.append(caps.szPname)
    return names


def find_output_device(part: str):
    part = (part or "").strip().lower()
    if not part:
        return WAVE_MAPPER
    for i, name in enumerate(list_output_devices()):
        if part in name.lower():
            return i
    return -1


class WaveOutPlayer:
    """向指定输出设备流式播放 16bit PCM。waveOut 自带实时节拍，写入即排队播放。"""

    def __init__(self, device_part: str = "", rate: int = SAMPLE_RATE, channels: int = 1):
        self.device_part = device_part
        self.device_id = find_output_device(device_part)
        self.rate = rate
        self.channels = channels
        self._handle = wt.HANDLE()
        self._event = None
        self._headers = []
        self._lock = threading.Lock()

    @property
    def ok(self):
        return self.device_id != -1

    def open(self):
        if self._handle:
            return
        if self.device_id == -1:
            raise OSError("找不到输出设备 %r（已安装 VB-CABLE？）" % self.device_part)
        block = self.channels * 2
        fmt = WAVEFORMATEX(1, self.channels, self.rate, self.rate * block, block, 16, 0)
        self._event = kernel32.CreateEventW(None, False, False, None)
        err = winmm.waveOutOpen(ctypes.byref(self._handle), self.device_id,
                                ctypes.byref(fmt), self._event, 0, CALLBACK_EVENT)
        if err != MMSYSERR_NOERROR:
            raise OSError("waveOutOpen 失败: %s" % err)

    def write(self, pcm: bytes):
        if not self._handle or not pcm:
            return
        with self._lock:
            self._reap()
            buf = ctypes.create_string_buffer(pcm, len(pcm))
            hdr = WAVEHDR()
            hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
            hdr.dwBufferLength = len(pcm)
            err = winmm.waveOutPrepareHeader(self._handle, ctypes.byref(hdr),
                                             ctypes.sizeof(hdr))
            if err != MMSYSERR_NOERROR:
                raise OSError("waveOutPrepareHeader 失败: %s" % err)
            err = winmm.waveOutWrite(self._handle, ctypes.byref(hdr),
                                     ctypes.sizeof(hdr))
            if err != MMSYSERR_NOERROR:
                raise OSError("waveOutWrite 失败: %s" % err)
            self._headers.append((hdr, buf))

    def _reap(self):
        remain = []
        for hdr, buf in self._headers:
            if hdr.dwFlags & WHDR_DONE:
                winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(hdr),
                                             ctypes.sizeof(hdr))
            else:
                remain.append((hdr, buf))
        self._headers = remain

    def wait_done(self, timeout: float = 3.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                self._reap()
                done = not self._headers
            if done:
                return True
            kernel32.WaitForSingleObject(self._event, 100)
        return False

    def close(self, drain: bool = True):
        if not self._handle:
            return
        try:
            if drain:
                self.wait_done()
            else:
                winmm.waveOutReset(self._handle)
        except Exception:
            pass
        with self._lock:
            for hdr, buf in self._headers:
                try:
                    winmm.waveOutUnprepareHeader(self._handle, ctypes.byref(hdr),
                                                 ctypes.sizeof(hdr))
                except Exception:
                    pass
            self._headers = []
        winmm.waveOutClose(self._handle)
        self._handle = wt.HANDLE()
        if self._event:
            kernel32.CloseHandle(self._event)
            self._event = None


# ===========================================================================
# 8. 按键模拟（SendInput）+ 窗口聚焦
# ===========================================================================
VK_MAP = {"alt": VK_LMENU, "ctrl": VK_CONTROL, "control": VK_CONTROL,
          "shift": VK_SHIFT, "f5": VK_F5, "enter": VK_RETURN, "return": VK_RETURN,
          "win": VK_LWIN, "space": 0x20, "tab": 0x09, "esc": 0x1B}
KEY_NAME = {0x11: "ctrl", 0x10: "shift", 0x12: "alt", 0x5B: "win",
            0xA4: "alt", 0xA2: "ctrl", 0xA0: "shift"}

# ---- 「哪个键触发发送」的配置（把遥控器上任意一个键绑成发送键）----
# 名称 -> VK。数字键/字母键/功能键/方向键都能写名字，写不出来就写 0x??。
VK_ALIASES = {
    "enter": 0x0D, "return": 0x0D, "kp_enter": 0x6E,
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "bksp": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "menu": 0x5D, "apps": 0x5D, "appskey": 0x5D,
    "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD,
    "play": 0xB3, "playpause": 0xB3, "stop": 0xB2,
    "next": 0xB0, "prev": 0xB1,
    "browser_home": 0xAC, "browser_back": 0xA6,
    "browser_forward": 0xA7, "browser_refresh": 0xA8,
    "browser_stop": 0xA9, "browser_search": 0xAA,
    "browser_favorites": 0xAB,
}
VK_LABELS = {
    0x0D: "Enter 回车", 0x20: "空格", 0x09: "Tab", 0x1B: "Esc",
    0x25: "左方向键", 0x26: "上方向键", 0x27: "右方向键", 0x28: "下方向键",
    0x5D: "菜单键", 0x6E: "小键盘 Enter",
    0xAD: "静音", 0xAE: "音量-", 0xAF: "音量+",
    0xB0: "下一曲", 0xB1: "上一曲", 0xB2: "停止", 0xB3: "播放/暂停",
    0xAC: "浏览器主页",
}


def parse_vk_spec(spec):
    """把「发送键」的配置值解析成 VK 码。解析不出来返回 None。

    支持：enter / space / tab / f1..f12 / a..z / 0..9 / up / down / left /
    right / home / end / volumeup / ... 以及 0x?? 十六进制。
    """
    s = str(spec or "").strip().lower()
    if not s:
        return None
    if s.startswith("0x"):
        try:
            v = int(s, 16)
            return v if 0 < v < 0x10000 else None
        except ValueError:
            return None
    if s in VK_ALIASES:
        return VK_ALIASES[s]
    if s.startswith("f") and s[1:].isdigit():
        n = int(s[1:])
        if 1 <= n <= 24:
            return 0x6F + n
    if len(s) == 1:
        if "0" <= s <= "9":
            return ord(s)
        if "a" <= s <= "z":
            return ord(s.upper())          # 键名统一按大写字母处理
    return None


def vk_label(vk):
    """VK 码 -> 好读的名字（用在提示文字里）。"""
    if vk in VK_LABELS:
        return VK_LABELS[vk]
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x6F)
    if 0x30 <= vk <= 0x39:
        return "数字 %d" % (vk - 0x30)
    if 0x41 <= vk <= 0x5A:
        return "字母 %s" % chr(vk)
    return "VK 0x%02X" % vk


def parse_combo(spec: str):
    """把 "ctrl+enter" 解析成 (修饰键列表, 主键)。解析不出来返回 ([], None)。"""
    parts = [p.strip().lower() for p in str(spec).replace(" ", "").split("+") if p.strip()]
    if not parts:
        return [], None
    mods, key = [], None
    for p in parts:
        vk = VK_MAP.get(p)
        if vk is None and len(p) == 1:
            vk = ord(p.upper())
        if vk is None:
            return [], None
        if p in ("ctrl", "control", "shift", "alt", "win"):
            mods.append(vk)
        else:
            key = vk
    return mods, key


def build_combo_seq(spec: str):
    """把 "ctrl+enter" 展开成按键事件序列 [(vk, 是否抬起), ...]。

    顺序：按下所有修饰键 -> 按下主键 -> 抬起主键 -> 逆序抬起修饰键。
    解析失败时抛 ValueError。
    """
    mods, key = parse_combo(spec)
    if key is None:
        raise ValueError("组合键解析失败: %r" % (spec,))
    return ([(m, False) for m in mods] + [(key, False), (key, True)]
            + [(m, True) for m in reversed(mods)])


class KeySender:
    """用 SendInput 模拟按键。比 keybd_event 新，不会被某些程序忽略。"""

    def __init__(self):
        self._held = set()

    def _send_key(self, vk: int, flags: int):
        """发送单个按键事件。修饰键自动附带硬件扫描码。

        这是本程序的关键细节：Electron/Chromium 判断「Alt 是否一直按着」时
        需要 wScan 非零，否则会当成一次性虚拟点按，导致 WorkBuddy 的语音
        输入「只绿一下就停」。
        """
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.ki = KEYBDINPUT(vk, SCAN_BY_VK.get(vk, 0), flags, 0, None)
        if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            raise OSError("SendInput 失败 err=%s" % ctypes.get_last_error())

    def down(self, name: str):
        vk = VK_MAP.get(name.strip().lower(), VK_LMENU)
        if vk in self._held:
            return
        self._send_key(vk, 0)
        self._held.add(vk)
        dbg("key down 0x%02X scan=0x%02X" % (vk, SCAN_BY_VK.get(vk, 0)))

    def up(self, name: str):
        vk = VK_MAP.get(name.strip().lower(), VK_LMENU)
        if vk not in self._held:
            return
        self._send_key(vk, KEYEVENTF_KEYUP)
        self._held.discard(vk)
        dbg("key up 0x%02X scan=0x%02X" % (vk, SCAN_BY_VK.get(vk, 0)))

    def tap(self, name: str, hold_ms: int = 60):
        self.down(name)
        time.sleep(max(0.01, hold_ms / 1000.0))
        self.up(name)

    def send_combo(self, spec: str) -> bool:
        """一次性发送组合键，例如 "ctrl+enter"。所有事件合并成一次 SendInput。"""
        seq = build_combo_seq(spec)
        arr = (INPUT * len(seq))()
        for i, (vk, is_up) in enumerate(seq):
            arr[i].type = INPUT_KEYBOARD
            arr[i].ki = KEYBDINPUT(vk, SCAN_BY_VK.get(vk, 0),
                                   KEYEVENTF_KEYUP if is_up else 0, 0, None)
        n = user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
        if n != len(seq):
            raise OSError("SendInput 组合键失败 err=%s" % ctypes.get_last_error())
        dbg("combo %s sent (%d 个事件)" % (spec, len(seq)))
        return True

    def replay_key(self, vk: int, scan: int = 0, extended: bool = False):
        """把「被我们吞掉的按键」原样补发回去。

        用在「先吞掉 -> 异步确认 -> 发现不是遥控器」这条路上：
        实体键盘的 Enter 被误吞后必须补回来，否则用户会「敲了回车没反应」。
        补发的是注入事件（带 LLKHF_INJECTED），本程序自己的钩子会放行，不会递归。
        """
        arr = (INPUT * 2)()
        for i, is_up in enumerate((False, True)):
            arr[i].type = INPUT_KEYBOARD
            flags = KEYEVENTF_KEYUP if is_up else 0
            if extended:
                flags |= KEYEVENTF_EXTENDEDKEY
            arr[i].ki = KEYBDINPUT(vk, scan, flags, 0, None)
        n = user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        if n != 2:
            raise OSError("SendInput 补发按键失败 err=%s" % ctypes.get_last_error())
        dbg("replay vk=0x%02X scan=0x%02X ext=%s" % (vk, scan, extended))
        return True

    def release_all(self):
        for name in list(VK_MAP):
            if VK_MAP[name] in self._held:
                self.up(name)


def find_window_by_title(part: str):
    """按标题子串找第一个可见顶层窗口，返回 hwnd 或 None。"""
    part = (part or "").strip().lower()
    if not part:
        return None
    found = []
    # 注意：EnumWindowsProc 是 2 参数回调，不能借用 4 参数的 WNDPROC，
    # 否则 ctypes 回调会因参数个数不匹配抛 TypeError 被吞掉、枚举中断。
    ENUMWNDSPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    @ENUMWNDSPROC
    def _cb(hwnd, _lparam):
        try:
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                title = buf.value
                if title and part in title.lower():
                    found.append(hwnd)
        except Exception:
            pass
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def title_matches(hwnd, part: str) -> bool:
    part = (part or "").strip().lower()
    if not part:
        return True
    return part in window_title(hwnd).lower()


imm32 = ctypes.WinDLL("imm32")
imm32.ImmGetContext.restype = wt.HANDLE
imm32.ImmGetContext.argtypes = [wt.HWND]
imm32.ImmGetCompositionStringW.restype = ctypes.c_long
imm32.ImmGetCompositionStringW.argtypes = [wt.HANDLE, wt.DWORD,
                                           ctypes.c_void_p, wt.DWORD]
imm32.ImmReleaseContext.restype = wt.BOOL
imm32.ImmReleaseContext.argtypes = [wt.HWND, wt.HANDLE]


def ime_composing(hwnd) -> bool:
    """输入法是否正在组词（候选窗开着）。是的话 Enter 要留给输入法选词。"""
    try:
        himc = imm32.ImmGetContext(hwnd)
        if not himc:
            return False
        n = imm32.ImmGetCompositionStringW(himc, GCS_COMPSTR, None, 0)
        imm32.ImmReleaseContext(hwnd, himc)
        return n > 0
    except Exception:
        return False


# ===========================================================================
# 8.5 遥控器活动感知（Raw Input）+ OK 键改发送（低级键盘钩子）
# ===========================================================================
class RemoteInputWatcher:
    """用 Raw Input 监听键盘事件，认出「这一下是遥控器按的」。

    低级键盘钩子拿不到设备信息，Raw Input 的 WM_INPUT 带 hDevice，可以解析
    出设备路径（含 VID&012717）。它不能拦截按键，只用来回答一个问题：
    **这一下按键，到底是哪个设备按的？**

    🔴 v1.7 架构核心（全部经实测钉死，2026-10-30）：

      1. **被低级键盘钩子吞掉的按键，Raw Input 一条都不会到。**
         探针实测：放行 3 条 / 全吞 0 条。
         —— 所以 v1.3~v1.6 的「吞掉 -> 等 Raw Input 确认」永远走不通。

      2. **但只要「抬起」被放行，抬起那一条 Raw Input 照样会到**，
         而它带的 hDevice 就是这一下按键的**确定**设备归属。
         —— 所以 v1.7 改成「吞下、放开抬」，用抬起事件做判定。

      3. **SendInput 注入的按键会产生 hdev=0 的 Raw Input 回声。**
         那是「我们自己注入的」，绝不能当成「实体键盘按了发送键」
         （v1.6 就栽在这，污染判据 -> 第一次之后每次都失败）。
         —— v1.7 直接丢弃 hdev==0 的事件。
    """

    def __init__(self, hwnd, match: str = "2717", on_activity=None,
                 on_keydown=None, on_keyup=None):
        self.hwnd = hwnd
        self.match = (match or "").strip().lower()
        self.on_activity = on_activity
        self.on_keydown = on_keydown
        self.on_keyup = on_keyup
        # 最近一次**遥控器**按键的时刻
        self.last_activity = 0.0
        # 🔴 v1.7：最近一次**实体键盘**按键的时刻。
        #    用来判断「刚才那一下更可能是谁按的」—— 这是时间窗兜底的第二条判据。
        self.last_physical_ts = 0.0
        self._dev_cache = {}
        self._seen = set()
        # 🔴 v1.6：读不出设备名的 hDevice（只提醒一次，别刷屏）
        self._unreadable = set()
        # 🔴 v1.6：已经确认过「是遥控器」的 hDevice -> 名字读不出来时也能认
        self._remote_hdevs = set()
        # 🔴 v1.6：最近一次**实体键盘**按发送键的时刻（看门狗兜底判据用）
        self.last_send_other_ts = 0.0
        # 🔴 v1.7：注入回声 / 解析失败的计数（原来静默 return False，查无实据）
        self.echo_count = 0
        self.drop_count = 0
        self._echo_logged = False
        self._drop_logged = False
        self.registered = False

    def register(self) -> bool:
        rid = RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, self.hwnd)  # usage page 1 / 6 = 键盘
        ok = user32.RegisterRawInputDevices(ctypes.byref(rid), 1,
                                            ctypes.sizeof(RAWINPUTDEVICE))
        self.registered = bool(ok)
        if not ok:
            log("！！Raw Input 注册失败 err=%s（OK 键转换只能靠语音会话兜底）"
                % ctypes.get_last_error())
        else:
            log("Raw Input 已注册（键盘，INPUTSINK）")
        return self.registered

    def _read_names(self, hdev):
        """真的去 Win32 问一遍这个 hDevice 有哪些顶层集合名。

        拆出来是为了**能单测**：缓存逻辑（device_name）和系统调用
        （_read_names）分开，测试只要打桩 _read_names 就能锁死
        「读失败不许缓存」这条规则，不用真的插设备。
        """
        names = []
        for idx in list(range(16)) + [0x07]:
            cmd = RIDI_DEVICENAME_BASE | idx
            size = wt.UINT(0)
            user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), cmd, None,
                                          ctypes.byref(size))
            if not size.value:
                continue
            buf = ctypes.create_unicode_buffer(size.value + 1)
            if user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), cmd, buf,
                                             ctypes.byref(size)):
                nm = buf.value
                # 只收像设备路径的名字（\\?\HID#... / \\?\hid#...），
                # 免得把索引越界时返回的垃圾当成真名字
                if nm and nm.lower().startswith("\\\\?\\") and nm not in names:
                    names.append(nm)
        return names

    def device_name(self, hdev) -> str:
        """读这个 hDevice 的设备路径（名字），**带缓存**。

        🔴🔴🔴 【v1.6 关键修复·本次的真根因】**读不到名字时绝对不能缓存！**

        知识点：`GetRawInputDeviceInfoW` 的 `uiCommand` 低位在
        `RIDI_DEVICENAME`（=0x20000007）之外还能带**集合序号**，
        一个物理 HID 设备可以有**多个顶层集合**（键盘 / 消费键 / 厂商自定义…），
        每个集合是**独立的 hDevice**。所以这里**多路尝试**哪个能读出就用哪个。

        ⚠️ 实测（本机 2026-10-30）：**读名字会偶发失败返回空串**。
        典型场景 = 蓝牙设备刚重连、新 HID 集合刚枚举出来，路径还没准备好。

        🔴 v1.5 的致命 bug（已修）：老代码最后一句无条件
            `self._dev_cache[hdev] = name`
        把**空串也缓存了** —— 于是「第一次读失败」会让这个 hDevice
        **永远**返回空名，`is_remote()` 只能静默判 False ->
        遥控器的 OK 键被当成实体键盘回车补发出去，**永远发不出去**。

        ✅ v1.6：**只有读到非空名字才写缓存**；读不到就返回空串且**不缓存**，
        下一次这个设备的任何按键事件都会自动重试 —— 蓝牙稳定后就能读出来，
        判定自动恢复正常。

        证据（v1.5 日志 17:45:02）：
            Raw 其它设备按下发送键 vk=0x0D -> 不是遥控器，原样补发
        这一行**前面没有**「Raw Input 键盘设备: ...」—— 因为名字是空的，
        老代码静默 return False，连设备行都不打。
        """
        if hdev in self._dev_cache:
            return self._dev_cache[hdev]
        names = self._read_names(hdev)
        name = names[0] if names else ""
        if name:
            # ✅ 只有读到真名字才缓存
            self._dev_cache[hdev] = name
            if hdev in self._unreadable:
                # 之前读不出来，现在读出来了 —— 说明是「刚重连还没准备好」，
                # 记一笔，让木木知道已经自动恢复正常。
                self._unreadable.discard(hdev)
                log("Raw 设备名后来读出来了 hdev=%s -> %s（之前是读不出来的，"
                    "已自动恢复）" % (hdev, name[:100]))
        else:
            # 🔴 读不到 -> **不缓存**，下次事件自动重试
            dbg("设备名暂时读不出来 hdev=%s（不缓存，下次事件重试）" % hdev)
        if len(names) > 1:
            dbg("设备 %s 有 %d 个顶层集合名" % (name[:40], len(names)))
        return name

    def is_remote(self, hdev) -> bool:
        # ⚡ 快速通道：已经确认过是遥控器的 hDevice 直接认。
        #    蓝牙重连会产生新的 hDevice，名字又可能读不出来 ——
        #    但只要历史上认过一次同一个 hDevice，就不用再赌名字了。
        if hdev in self._remote_hdevs:
            return True
        name = self.device_name(hdev).lower()
        if not name:
            # 🔴 读不到名字不等于「不是遥控器」—— 但也不能瞎猜。
            #    这里明确记一笔，避免又出现「静默判错、查无实据」的情况。
            if hdev not in self._unreadable:
                self._unreadable.add(hdev)
                log("Raw 设备名读不出来 hdev=%s -> 无法判定设备，"
                    "按「非遥控器」处理（如遥控器 OK 键发不出去，先看这里）"
                    % hdev)
            return False
        hit = ("vid&01" + self.match) in name or ("vid_" + self.match) in name
        if hit:
            self._remote_hdevs.add(hdev)
        if hdev not in self._seen:
            self._seen.add(hdev)
            log("Raw Input 键盘设备: %s -> %s"
                % (name[:120], "遥控器" if hit else "普通键盘"))
        return hit

    def handle(self, lparam) -> bool:
        """在 WM_INPUT 里调用。返回 True 表示这次事件来自遥控器。

        🔴 v1.7 的三处关键改动：

        1. **hdev == 0 的事件直接丢弃** —— 那是 SendInput 注入的回声，
           是「我们自己发的」，不是任何人的手。
           🔴 v1.6 的真 bug 就栽在这：注入回声被当成「实体键盘按了发送键」，
           污染 `last_send_other_ts` -> 「最近活动来自遥控器」永远判不成立
           -> **第一次之后每一次按 OK 键都失败**（19:03:51 实测现场）。

        2. **抬起事件也要处理** —— v1.7 的整套判定就靠抬起那一条 Raw Input。
           按下被钩子吞了不会有 Raw，抬起放行了才有。

        3. **解析失败不再静默** —— 原来三处 `return False` 什么都不记，
           洪峰丢包时完全查不出原因。现在计数 + 打一条正式日志。
        """
        try:
            size = wt.UINT(0)
            hraw = wt.HANDLE(lparam)
            if user32.GetRawInputData(hraw, RID_INPUT, None,
                                      ctypes.byref(size), 24) != 0:
                self._note_drop("GetRawInputData 第一次调用失败")
                return False
            if not size.value:
                self._note_drop("GetRawInputData 返回 0 字节")
                return False
            buf = ctypes.create_string_buffer(size.value)
            if user32.GetRawInputData(hraw, RID_INPUT, buf,
                                      ctypes.byref(size), 24) == 0xFFFFFFFF:
                self._note_drop("GetRawInputData 读取失败")
                return False
            raw = buf.raw
            if struct_unpack_i(raw, 0) != RIM_TYPEKEYBOARD:
                return False
            hdev = struct_unpack_q(raw, 8)
            # 🔴🔴 v1.7：注入回声必须在这里挡掉（实测：SendInput 注入的按键
            #    会产生 hdev=0 的 Raw Input）。放行它 = 自己污染自己的判据。
            if hdev == 0:
                self.echo_count += 1
                if not self._echo_logged:
                    self._echo_logged = True
                    log("Raw Input：忽略 hdev=0 的注入回声（我们自己 SendInput "
                        "注入的按键，不是任何设备按的）")
                return False
            # 🔴 MakeCode / VKey 都是 USHORT，必须读 2 字节（详见 struct_unpack_h）
            makecode = struct_unpack_h(raw, 24)     # RAWKEYBOARD.MakeCode
            vkey = struct_unpack_h(raw, 30)         # RAWKEYBOARD.VKey
            msg = struct_unpack_i(raw, 32)          # RAWKEYBOARD.Message
            is_down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
            is_up = msg in (WM_KEYUP, WM_SYSKEYUP)
            if not (is_down or is_up):
                return False
            hit = self.is_remote(hdev)
            # 活动时间：遥控器和实体键盘**分别**记，谁更近谁说了算。
            # 🔴 v1.7：抬起也记 —— 遥控器的语音键抬起，说明它刚被用过。
            if hit:
                self.last_activity = time.time()
                if self.on_activity:
                    try:
                        self.on_activity()
                    except Exception:
                        pass
            else:
                self.last_physical_ts = time.time()
            # 把「这一下是哪个设备按的」告诉仲裁器 —— 只有这里能分清设备
            if is_down and self.on_keydown:
                try:
                    self.on_keydown(vkey, makecode, hit)
                except Exception:
                    pass
            elif is_up and self.on_keyup:
                try:
                    self.on_keyup(vkey, makecode, hit)
                except Exception:
                    pass
            return hit
        except Exception as e:
            dbg("WM_INPUT 解析异常: %r" % (e,))
            return False

    def _note_drop(self, why: str):
        """记一笔「这次 Raw Input 没解析出来」，别再静默吞掉。"""
        self.drop_count += 1
        if not self._drop_logged:
            self._drop_logged = True
            log("Raw Input：%s（已丢弃，之后每 50 次再记一次）" % why)
        elif self.drop_count % 50 == 0:
            log("Raw Input：累计丢弃 %d 条（最后一次：%s）"
                % (self.drop_count, why))


def struct_unpack_i(buf, off):
    return int.from_bytes(buf[off:off + 4], "little", signed=False)


def struct_unpack_h(buf, off):
    """读 2 字节无符号整数（USHORT）。

    🔴 为什么必须有这个（踩过的坑）：
    RAWKEYBOARD 里的 MakeCode 和 VKey 都是 **USHORT（2 字节）**，不是 DWORD。
    用 struct_unpack_i（4 字节）去读 VKey，会把紧跟其后的 Message 的高 2 字节
    一起吃进来，VKey=0x0D 会变成 0x0100000D —— 于是
    `vkey != send_key_vk` 永远成立，Raw Input 的设备判定**一次都进不去**，
    「遥控器 OK 键 -> Ctrl+Enter」就永远不生效。
    """
    return int.from_bytes(buf[off:off + 2], "little", signed=False)


def struct_unpack_q(buf, off):
    return int.from_bytes(buf[off:off + 8], "little", signed=False)


class SendArbiter:
    """发送键仲裁器：把遥控器的 OK 键变成发送快捷键，同时绝不误伤实体键盘。

    🔴🔴🔴 v1.7 架构翻新（本次的**真根因修复**，三条全部经实测钉死）：

      v1.3~v1.6 用的链路是：
          钩子先吞 -> 等 Raw Input 确认设备归属 -> 决定发送还是补发

      🔴 这条链**在 Windows 上根本不成立**：
          **被低级键盘钩子吞掉的按键，Raw Input 一条都不会到。**
          探针实测（tools\\probe_raw_live.py swallow）：
              放行(对照)    WM_INPUT 3 条
              全吞(下+抬)   WM_INPUT 0 条
          日志印证：19:03 那 265 条被吞的 F5 重复，主日志**零条** Raw Input。
          所以「等 Raw Input 确认自己」永远等不到；v1.4/v1.5 日志里的
          「来源 raw」其实是**别的**未吞按键的事件被 FIFO 误配出来的。

      ✅ v1.7 的新链路（同样实测钉死）：
          **只吞「按下」，把「抬起」放行** —— 抬起那一条 Raw Input 照样会到，
          而它带的 hDevice 就是**这一下按键的确定**设备归属。

            按下 -> 吞掉（前台程序收不到，不会插入换行）
            抬起 -> 放行 -> Raw Input 到达 -> 拿到 hDevice
                 -> 是遥控器 -> 发 send_keys（默认 ctrl+enter）
                 -> 是别的设备 -> 把刚才吞掉的按下**原样补发**回去

      ⚠️ 因此本类**故意打破**「吞了按下就必须吞抬起」的配对原则：
         发送键的抬起**必须放行**，否则拿不到设备归属。
         代价 = 前台程序会收到一个「没有按下过的抬起」—— 对输入框无害
         （不会插入任何字符），换来的是**确定**的判定。
         （F5 语音键那边仍然保持配对原则，是另一条逻辑，不冲突。）

    ⚠️ 补发（replay）是安全网的关键：宁可「晚几毫秒送达」，
       也不能让用户的回车凭空消失。
    """

    MAX_PENDING = 16
    # 🔴 v1.6：「keyup 没来」时的兜底阈值（秒）。
    #    **权威的「按住周期结束」标志是 keyup** —— 系统的自动重复**不产生 keyup**，
    #    而人的两次独立按键**一定产生 keyup**。所以 keyup 才是可靠判据，
    #    这个阈值只用来兜底「keyup 事件丢失」（例如程序刚启动时按键已经按着）。
    #    取 300ms：远大于系统最快重复速率（约 33ms/次），
    #    也远小于人「松开再按」的正常间隔（>100ms）。
    REPEAT_GAP = 0.300
    # 🔴 v1.7：抬起之后等多久去问「抬起那条 Raw Input 到了没」。
    #    实测抬起事件是毫秒级到达的，800ms 只是保险丝。
    CONFIRM_WAIT_MS = 800
    # 🔴 v1.7：按下之后多久还没等到抬起 -> 认定抬起丢了，走时间窗兜底。
    #    人按住回车一般 <1s；3s 足够宽松，又不至于让 pending 无限挂着。
    HOLD_TIMEOUT_MS = 3000

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._pending = []          # FIFO：每条 = 一次「被吞掉的发送键」
        self._up_vk = 0             # 需要把抬起也吞掉的那个 vk
        self._cycle_vk = 0          # 本次「按住周期」正在吞的 vk
        self._cycle_ts = 0.0        # 本周期最后一次 down 的时刻
        self.stats = {"sent": 0, "replayed": 0, "timeout": 0, "dropped": 0,
                      "repeat": 0, "up_raw": 0}

    # ---------------- 钩子侧（同步，必须快：不写日志、不阻塞） ----------------
    def on_hook_down(self, kb) -> bool:
        """钩子收到发送键 keydown。返回 True = 吞掉这一下。

        🔴 v1.6 关键修复：**按住不放不能连发！**

        旧版每来一次 keydown 就新增一条 pending。系统按住不放会自动重复
        产生几十次/秒的 keydown，于是 pending 堆成 6 条，
        结果就是**一次按住连发 6 次 ctrl+enter**（v1.5 日志 17:32:49 / 17:36:03
        连续 6 条「发送键已转换（来源 raw）」就是现场）。

        ✅ v1.6：**一次「按下-松开」只对应一条 pending**。
        自动重复的 keydown 照吞（不能让真实回车漏到前台程序），
        但不再新增 pending。

        ⚠️ 怎么区分「按住不放的自动重复」和「松开后再按一次」？
           **靠 keyup**：系统的自动重复**不产生 keyup**，人的两次独立按键
           **一定产生 keyup**。所以：
             · on_hook_up  -> 清掉 _cycle_vk（本次按住周期结束）
             · on_hook_down -> 若 _cycle_vk 仍是同一个 vk，就是自动重复
           时间阈值 REPEAT_GAP 只兜底「keyup 丢失」，不是主要判据。

        🔴 v1.7：**无条件吞掉按下**（前置条件满足时）。
           因为「抬起」那一条 Raw Input 会给出**确定**的设备归属，
           吞下去也不会丢信息；万一判成实体键盘，回车会被**原样补发**回来。
        """
        try:
            if not self.app.ok_precheck():
                return False
            now = time.time()
            with self._lock:
                vk = kb.vkCode
                if (self._cycle_vk == vk
                        and (now - self._cycle_ts) < self.REPEAT_GAP):
                    # 自动重复：吞掉，但**不新增 pending**
                    self.stats["repeat"] += 1
                    self._cycle_ts = now
                    self._up_vk = vk
                    return True
                self._cycle_vk = vk
                self._cycle_ts = now
                self._pending.append({
                    "vk": vk,
                    "scan": kb.scanCode,
                    "ext": bool(kb.flags & 0x01),   # LLKHF_EXTENDED
                    "ts": now,
                    "up_ts": None,     # 🔴 v1.7：抬起发生的时间（钩子看到时填）
                    "done": False,
                })
                if len(self._pending) > self.MAX_PENDING:
                    del self._pending[0]
                self._up_vk = vk
            return True
        except Exception:
            return False

    def on_hook_up(self, kb) -> bool:
        """钩子收到发送键 keyup。

        🔴🔴 v1.7：**放行抬起**（永远返回 False），这是新架构的关键。

        为什么必须放行：
          按下被吞掉 -> 没有 Raw Input -> 拿不到这一下的设备归属；
          抬起放行     -> 抬起那条 Raw Input 会到 -> hDevice 就是答案。

        ⚠️ 这**故意打破**「吞了按下就要吞抬起」的配对原则。
           代价：前台程序会收到一个「没有按下过的抬起」。
           对普通输入框无害（不会插入任何字符），换来的是**确定的**设备判定。

        同时在这里给 pending 打上「抬起已发生」的时间戳，
        看门狗据此判断「抬起确认迟迟不来」。
        """
        try:
            with self._lock:
                if self._up_vk == kb.vkCode:
                    self._up_vk = 0
                    self._cycle_vk = 0       # 本次按住周期结束
                    # 标记「抬起已经发生」，等它那条 Raw Input 来定性
                    for p in reversed(self._pending):
                        if not p["done"] and p["up_ts"] is None:
                            p["up_ts"] = time.time()
                            break
            return False                # ← 放行抬起（v1.7）
        except Exception:
            return False

    # ---------------- Raw Input 侧（异步，主线程） ----------------
    def on_raw_keydown(self, vkey: int, makecode: int, is_remote: bool) -> bool:
        """按下的原始事件到了。

        🔴 v1.7：按下被吞时**根本不会有**这条事件（实测：全吞 0 条），
        所以这里收到的一定是「我们没吞的那一下」（前置条件不满足），
        没有 pending 可结算 -> 原样放行即可。
        保留这个方法是为了不打断 App 的调用链。
        """
        return False

    def on_raw_keyup(self, vkey: int, makecode: int, is_remote: bool) -> bool:
        """🔴🔴 v1.7 主路径：**抬起**那条 Raw Input 到了 -> 设备归属**确定**。

        这一步是整套新架构的落点：
          抬起被放行，所以 Raw Input 一定会到；
          它带的 hDevice 就是**这一下按键**按下去的设备。
        """
        try:
            if vkey != self.app.send_key_vk:
                return False
            with self._lock:
                pend = None
                for p in self._pending:
                    if (not p["done"]) and p["up_ts"] is not None:
                        pend = p
                        break
                if pend is None:
                    return False
                pend["done"] = True
            self.stats["up_raw"] += 1
            if is_remote:
                self._do_send("up")
            else:
                self._do_replay(pend, "up")
            return True
        except Exception:
            return False

    # ---------------- 看门狗（主线程定时器） ----------------
    def tick(self):
        """抬起那条 Raw Input 迟迟不来时的兜底（按时间窗猜）。"""
        try:
            now = time.time()
            confirm = self.app.send_confirm_ms / 1000.0
            hold = self.HOLD_TIMEOUT_MS / 1000.0
            overdue = []
            with self._lock:
                for p in self._pending:
                    if p["done"]:
                        continue
                    if p["up_ts"] is not None:
                        # 抬起发生了，但那条 Raw Input 没来 -> 超时兜底
                        if (now - p["up_ts"]) > confirm:
                            p["done"] = True
                            overdue.append(p)
                    elif (now - p["ts"]) > hold:
                        # 抬起压根没来（丢了 / 用户一直按着）-> 超时兜底
                        p["done"] = True
                        overdue.append(p)
                if len(self._pending) > self.MAX_PENDING:
                    self._pending = [p for p in self._pending if not p["done"]][-4:]
            for p in overdue:
                self.stats["timeout"] += 1
                remote, why = self.app.send_key_verdict()
                if remote:
                    log("看门狗兜底：抬起确认没等到（%s）-> 按「遥控器」处理 -> 发 %s"
                        % (why, self.app.send_spec))
                    self._do_send("timeout")
                else:
                    log("看门狗兜底：抬起确认没等到（%s）-> 按「实体键盘」处理 -> 补发原键"
                        % why)
                    self._do_replay(p, "timeout")
        except Exception:
            pass

    # ---------------- 落地动作 ----------------
    def _do_send(self, via: str):
        try:
            self.app.keys.send_combo(self.app.send_spec)
            self.app.ok_send_count += 1
            self.stats["sent"] += 1
            log("发送键已转换：%s -> %s（来源 %s）"
                % (self.app.send_key_name, self.app.send_spec, via))
        except Exception as e:
            log("发送键转换失败: %r" % (e,))

    def _do_replay(self, pend: dict, via: str):
        try:
            self.app.keys.replay_key(pend["vk"], pend["scan"], pend["ext"])
            self.stats["replayed"] += 1
            log("补发原键 vk=0x%02X（来源 %s，判定为非遥控器）"
                % (pend["vk"], via))
        except Exception as e:
            # 补发都失败了 -> 这下回车是真的丢了，记一笔别装没发生
            self.stats["dropped"] += 1
            log("！！补发按键失败，用户这一下回车可能丢了: %r" % (e,))

class EnterSendHook:
    """低级键盘钩子，做两件事：

    1) **吞掉遥控器语音键(F5)的自动重复**（防止 WorkBuddy 误判成组合键）
       —— 这是「语音图标只绿一下就停」的根因，详见 _handle_f5()。
    2) 把「遥控器的发送键」变成「发送快捷键」（默认 Ctrl+Enter）。
       发送键本身可在 mivoice.ini 的 `send_key` 里改（enter / space / f7 /
       up / volumeup / 0x74 …），用 tools\\scan_remote_keys.py 可以查出
       遥控器每个键对应的 vk。

    判定链（任何一条不满足就原样放行）：
      1. 总开关 ok_to_send + 程序未暂停
      2. 前台窗口标题匹配（默认 WorkBuddy）
      3. 输入法没有在组词（否则 Enter 要留给输入法选词）
      4. 当前没有任何修饰键按下（用户自己按 Ctrl+Enter 时不干预）
      5. 事件不是本程序注入的（LLKHF_INJECTED，防递归）

    🔴 v1.7：**发送键的「抬起」是故意放行的**（不是漏放）。
      按下被吞 -> 没有 Raw Input -> 拿不到设备归属；
      抬起放行 -> 抬起那条 Raw Input 会到 -> hDevice 就是这一下的确定设备。
      详见 SendArbiter 的类文档。

    ⚠️ 钩子跑在独立线程里，且回调里绝不写日志/不调可能阻塞的 API：
      低级钩子一旦卡住，整个系统的键盘都会停止响应。所以输入法状态由
      ImeProbe 线程提前算好，钩子只读内存里的标志位。
    """

    def __init__(self, app):
        self.app = app
        self.hook = None
        self._proc = None
        self._thread = None
        self._tid = 0
        self._ready = threading.Event()
        self._swallow_up = False     # 这一下 keydown 被我吞了，配对的 keyup 也要吞
        self._last_convert = 0.0     # 防长按自动重复连发
        # F5 拦截的三态机（见 _handle_f5）
        #   0 = 没有 F5 按着
        #   1 = 本轮按下的第一次 down 放行了 -> 抬起来也必须放行（配对）
        #   2 = 本轮按下的第一次 down 吞掉了 -> 后续重复 + 抬起全部吞掉
        self._f5_state = 0
        # 修饰键自跟踪：钩子能看到每一个按键事件，比 GetAsyncKeyState 可靠
        #（异步键状态是「按线程」维护的，别的线程读出来可能是 0）。
        self._mods = set()

    def install(self) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="enter-hook")
        self._thread.start()
        self._ready.wait(2.0)
        return bool(self.hook)

    def _run(self):
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        self._proc = HOOKPROC(self._on_key)          # 必须留引用，否则被回收
        self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc,
                                             kernel32.GetModuleHandleW(None), 0)
        self._tid = kernel32.GetCurrentThreadId()
        if not self.hook:
            log("！！键盘钩子安装失败 err=%s（OK 键不会转换成发送）"
                % ctypes.get_last_error())
            self._ready.set()
            return
        log("键盘钩子已安装（OK 键 -> %s，独立线程 tid=%d）"
            % (self.app.send_spec, self._tid))
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        try:
            user32.UnhookWindowsHookEx(self.hook)
        except Exception:
            pass
        self.hook = None

    # 修饰键集合（含左右各版本）。用于判断「用户是不是自己按着组合键」。
    MODIFIER_VKS = frozenset((0x10, 0x11, 0x12,
                              0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5,
                              0x5B, 0x5C))

    def _track_modifier(self, wParam, kb):
        """跟踪当前按着的修饰键。

        钩子能看到系统里**每一个**按键事件（包括我们程序自己注入的 Alt），
        所以这里维护出来的状态就是「此刻屏幕上真正按着的修饰键」。
        """
        vk = kb.vkCode
        if vk not in self.MODIFIER_VKS:
            return
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            self._mods.add(vk)
        elif wParam in (WM_KEYUP, WM_SYSKEYUP):
            self._mods.discard(vk)

    def mods_down(self) -> bool:
        """此刻有没有修饰键按着。"""
        return bool(self._mods)

    def _on_key(self, nCode, wParam, lParam):
        """钩子回调：只做内存判断 + SendInput，不写日志、不调阻塞 API。"""
        try:
            if nCode == 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                self._track_modifier(wParam, kb)
                tag = ""
                swallowed = self._handle_f5(wParam, kb)
                if swallowed:
                    tag = "SWAL"
                # 发送键交给仲裁器：吞掉「按下」，放行「抬起」
                elif (self.app.arbiter is not None
                        and kb.vkCode == self.app.send_key_vk
                        and not (kb.flags & LLKHF_INJECTED)):
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        swallowed = self.app.arbiter.on_hook_down(kb)
                        if swallowed:
                            tag = "SND"
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                        # 🔴🔴 v1.7：抬起**必须放行**！
                        #    抬起放行 -> 抬起那条 Raw Input 才会到 ->
                        #    它的 hDevice 就是这一下按键的**确定**设备归属。
                        #    （v1.3~v1.6 一直吞抬起，所以永远拿不到 Raw Input。）
                        swallowed = self.app.arbiter.on_hook_up(kb)
                        tag = "UPE"       # 抬起放行 -> Raw Input 会到
                self._record_key(wParam, kb, swallowed, tag)
                if swallowed:
                    return 1
        except Exception:
            pass
        return user32.CallNextHookEx(self.hook, nCode, wParam, lParam)

    # ---- 遥控器语音键(F5)拦截 ----
    def _handle_f5(self, wParam, kb) -> bool:
        """在 WorkBuddy 录音期间把遥控器的 F5 整个吞掉。返回 True=吞掉。

        🔴 为什么必须吞（这是「语音图标只绿一下就停」的根因）：
        遥控器的「语音键」在 Windows 上就是一个 **F5**。按住不放时，系统会自动
        重复产生几十次/秒的 keydown。而 WorkBuddy 的语音热键逻辑是：

            if (!isVoiceModifierKey(e, 'Alt')) {         // 收到非修饰键
                e.phase === 'down' && n                  //   且 Alt 正按着
                  ? (i = true, r && stop())              //   -> 判定成「组合键」-> 停止录音
                  : ...
                return;
            }

        它**不检查 e.repeat**，所以系统的自动重复照样命中「组合键」判定：
        刚 hold-start 录音就立刻被自己的自动重复掐断（表现：图标只绿一下）。
        唯一稳妥的解法是让 WorkBuddy 根本收不到 F5 —— 也就是在这里吞掉。

        ⚠️ 配对原则：down 吞了 up 也必须吞；down 放行了 up 也要放行，
        否则别的程序会收到「有抬起没按下」，键盘状态就错乱了。
        """
        if kb.vkCode != VK_F5 or (kb.flags & LLKHF_INJECTED):
            return False
        alt_down = bool(kb.flags & LLKHF_ALTDOWN)
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if self._f5_state == 0:
                # 本轮按下的第一次 down：决定这一轮吞不吞
                block = self._f5_should_swallow(alt_down)
                self._f5_state = 2 if block else 1
                return block
            # 自动重复：只要 Alt 正按着就吞 —— 这正是 WorkBuddy 判成组合键的条件
            if self._f5_state == 2 or self._f5_should_swallow(alt_down):
                return True
            return False
        if wParam in (WM_KEYUP, WM_SYSKEYUP):
            # 抬起必须与按下配对：这一轮从头到尾吞了就吞抬起，放行过就放行抬起
            block = (self._f5_state == 2)
            self._f5_state = 0
            return block
        return False

    def _f5_should_swallow(self, alt_down: bool) -> bool:
        """这个 F5 事件要不要吞掉。

        判据有两条，任意一条成立就吞：

        1. **事件自带 LLKHF_ALTDOWN** —— Windows 明确告诉我们「这个事件发生
           时 Alt 正按着」。这跟 WorkBuddy 内部那个 `n`（Alt 按住标志）
           是同一个语义，是最准的判据，而且是**逐事件**的。
           ⚠️ 别改用 GetAsyncKeyState 二次确认：它的异步键状态是**按线程**
           维护的、随该线程取走键盘消息才刷新，而注入的按键被路由到前台
           窗口线程，所以别的线程很可能永远读到 0（实测纯脚本线程恒为
           0x0000，而 MiVoice 主线程却偶尔能读到 alt=3）。靠它做判据会
           时灵时不灵，属于典型的「看运气」写法。
        2. **voice_active** —— 我们正按住 Alt 模拟录音（ATVV 会话进行中）。

        这样物理键盘的 F5（刷新）在平时完全不受影响。
        """
        app = self.app
        if app is None:
            return False
        if not getattr(app, "hide_remote_f5", True):
            return False
        # 我们自己模拟的键如果正好是 F5（alt_key=f5），那 F5 是触发键不能吞
        if getattr(app, "alt_key", "alt").strip().lower() == "f5":
            return False
        if alt_down:
            return True
        return bool(getattr(app, "voice_active", False))

    def uninstall(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT
            self._tid = 0
        self._thread = None

    # ---- 键盘事件取证（只写内存，由主线程定时落盘）----
    def _record_key(self, wParam, kb, swallowed=False, tag=""):
        """低级钩子里不能写文件（会卡住整个系统键盘）。

        这里只把事件塞进内存列表，真正的落盘交给主线程的定时器。

        tag 用来区分「为什么被吞」：
          "SWAL" = 遥控器语音键 F5 被吞（防 WorkBuddy 误判组合键）
          "SND"  = 发送键被仲裁器吞（等 Raw Input 确认设备归属）
        """
        try:
            app = self.app
            if app is None:
                return
            app._key_events.append((
                time.time(),
                wParam,
                kb.vkCode,
                kb.scanCode,
                kb.flags,
                swallowed,
                tag,
            ))
            if len(app._key_events) > 4000:      # 防止无限增长
                del app._key_events[:2000]
        except Exception:
            pass


class ImeProbe(threading.Thread):
    """后台探测「输入法是否正在组词」。

    单独开线程的原因：ImmGetContext 是跨线程调用，目标程序卡住时它可能跟着卡。
    放在独立线程里，最坏情况只是这个线程僵住，键盘钩子和托盘不受影响。
    """

    def __init__(self, app):
        super().__init__(daemon=True, name="ime-probe")
        self.app = app
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                fg = user32.GetForegroundWindow()
                composing = bool(fg and title_matches(fg, self.app.ok_send_title)
                                 and ime_composing(fg))
                self.app.ime_flag = composing
            except Exception:
                pass
            self._stop.wait(0.2)

    def stop(self):
        self._stop.set()


# ===========================================================================
# 9. 托盘图标（与 PinTop 同一套 DIB+字形渲染法）
# ===========================================================================
def make_dib(w, h):
    hdc_screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0,
                                  ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, hbmp)
    user32.ReleaseDC(None, hdc_screen)
    return dc, hbmp, bits, old


def free_dib(dc, hbmp, old):
    try:
        gdi32.SelectObject(dc, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(dc)
    except Exception:
        pass


def make_tray_hicon(col, size=16, alpha_gain=1.0):
    """黑底白字渲染麦克风字形，再按 tint 上色，造 HICON。

    alpha_gain：把抗锯齿产生的半透明边缘往上提，让 16px 的字形更「实」。
    1.0 = 原始抗锯齿（边缘发虚、在深色任务栏上显得糊）；1.65 左右明显更清晰。
    """
    dc, hbmp, bits, old = make_dib(size, size)
    r = RECT(0, 0, size, size)
    br = gdi32.CreateSolidBrush(0x000000)
    user32.FillRect(dc, ctypes.byref(r), br)
    gdi32.DeleteObject(br)
    gdi32.SetBkMode(dc, 1)
    gdi32.SetTextColor(dc, 0x00FFFFFF)
    f = gdi32.CreateFontW(-int(size * 0.78), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0,
                          4, 0, FONT_ICON)
    gdi32.SelectObject(dc, f)
    user32.DrawTextW(dc, GLYPH_MIC, -1, ctypes.byref(r), 1 | 4 | 0x20 | 0x100)
    raw = bytearray(ctypes.string_at(bits, size * size * 4))
    for i in range(0, len(raw), 4):
        a = raw[i]  # 黑底白字：B 通道即覆盖率
        if alpha_gain != 1.0:
            a = 255 if a >= 250 else min(255, int(a * alpha_gain))
        raw[i] = (col[2] * a) // 255
        raw[i + 1] = (col[1] * a) // 255
        raw[i + 2] = (col[0] * a) // 255
        raw[i + 3] = a
    rows = (size + 15) // 16 * 2
    mask = gdi32.CreateBitmap(size, size, 1, 1, ctypes.c_char_p(b"\x00" * (rows * size)))
    dc2, hbmp2, bits2, old2 = make_dib(size, size)
    ctypes.memmove(bits2, bytes(raw), len(raw))
    ii = ICONINFO(True, 0, 0, mask, hbmp2)
    hicon = user32.CreateIconIndirect(ctypes.byref(ii))
    gdi32.SelectObject(dc2, old2)
    gdi32.DeleteObject(hbmp2)
    gdi32.DeleteDC(dc2)
    gdi32.DeleteObject(f)
    free_dib(dc, hbmp, old)
    return hicon


class Tray:
    def __init__(self, hwnd, hicon):
        self.hwnd = hwnd
        self.hicon = hicon
        self.added = False
        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = hwnd
        self.nid.uID = TRAY_UID
        self.nid.hIcon = hicon
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAY
        self.nid.szTip = "%s v%s" % (APP_NAME, APP_VER)

    def add(self):
        r = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))
        self.added = bool(r)
        log("托盘 NIM_ADD -> %s (err=%s)" % (r, ctypes.get_last_error()))
        return self.added

    def set_icon(self, hicon):
        self.hicon = hicon
        self.nid.hIcon = hicon
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        if self.added:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def set_tip(self, text):
        text = text[:127]
        if text == self.nid.szTip:
            return
        self.nid.szTip = text
        if self.added:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def balloon(self, title, text):
        """托盘气泡。

        ⚠️ Windows 的气泡**不解析 \\n，而且宽度有限**，文字一长就被截断
        （表现：说明只显示一半，后面没了）。所以气泡只放「一行短提示」，
        完整说明走 msgbox()。
        """
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
        self.nid.szInfoTitle = title[:63]
        self.nid.szInfo = text[:255]
        self.nid.dwInfoFlags = 0x00000001  # NIIF_INFO
        if self.added:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP

    def msgbox(self, title, text):
        """弹一个真正的对话框：支持换行、窗口自适应宽度，长说明用这个。"""
        MB_OK = 0x00000000
        MB_ICONINFORMATION = 0x00000040
        MB_TOPMOST = 0x00040000
        try:
            user32.MessageBoxW(self.hwnd, text, title,
                               MB_OK | MB_ICONINFORMATION | MB_TOPMOST)
        except Exception as e:
            log("MessageBox 失败: %r" % (e,))
            self.balloon(title, text.replace("\r\n", " ")[:120])

    def remove(self):
        if self.added:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
            self.added = False


# ===========================================================================
# 10. 开机自启（HKCU Run）
# ===========================================================================
def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, "MiVoice")
            return True
    except Exception:
        return False


def autostart_set(on: bool) -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if not os.path.exists(pyw):
                    pyw = sys.executable
                entry = os.path.abspath(__file__)
                winreg.SetValueEx(k, "MiVoice", 0, winreg.REG_SZ,
                                  '"%s" "%s"' % (pyw, entry))
            else:
                try:
                    winreg.DeleteValue(k, "MiVoice")
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        log("自启开关失败: %r" % (e,))
        return False


# ===========================================================================
# 11. 主程序
# ===========================================================================
IDM_TOGGLE = 2001
IDM_ALTTEST = 2002
IDM_AUTOSTART = 2003
IDM_LOGFILE = 2004
IDM_HELP = 2005
IDM_QUIT = 2006
IDM_SENDTEST = 2007
IDM_RECONNECT = 2008
IDM_SCANKEYS = 2009

COLOR_IDLE = (0x00, 0xE6, 0x76)     # 亮绿：待命（深色任务栏上最醒目，且不与蓝色系系统图标混淆）
COLOR_OFF = (0x90, 0x90, 0x90)      # 中灰：暂停
COLOR_REC = (0xFF, 0x3B, 0x30)      # 亮红：录音中
ALPHA_GAIN = 1.65                   # 抗锯齿边缘提实系数（1.0=原始发虚，1.65≈清晰饱满）
HOST_CLASS = "MiVoiceHostWnd"


class App:
    def __init__(self, args):
        self.args = args
        self.cfg = Config()
        global _debug
        _debug = args.debug
        self.enabled = self.cfg.b("general", "enabled")
        self.alt_key = self.cfg.s("general", "alt_key") or "alt"
        self.alt_mode = (self.cfg.s("general", "alt_mode") or "hold").strip().lower()
        self.alt_tap_ms = self.cfg.i("general", "alt_tap_ms")
        self.alt_pre_ms = self.cfg.i("general", "alt_pre_ms")
        self.gain = self.cfg.f("general", "gain")
        self.focus_wb = self.cfg.b("general", "focus_workbuddy")
        self.focus_title = self.cfg.s("general", "focus_title") or "WorkBuddy"
        self.quit_spec = self.cfg.s("general", "hotkey_quit") or "Ctrl+Shift+M"
        # ---- OK 键 -> 发送 ----
        self.ok_to_send = self.cfg.b("general", "ok_to_send")
        self.send_spec = self.cfg.s("general", "send_keys") or "ctrl+enter"
        self.ok_send_title = self.cfg.s("general", "ok_send_title") or ""
        # 哪个物理键触发发送（默认 Enter）。填错了就退回 Enter，别让程序起不来。
        self.send_key_spec = self.cfg.s("general", "send_key") or "enter"
        self.send_key_vk = parse_vk_spec(self.send_key_spec)
        if self.send_key_vk is None:
            log("！！send_key=%r 解析不出来，退回 Enter"
                % self.send_key_spec)
            self.send_key_vk = VK_RETURN
            self.send_key_spec = "enter"
        self.send_key_name = vk_label(self.send_key_vk)
        self.remote_active_ms = self.cfg.i("general", "remote_active_ms")
        # 看门狗兜底窗口：抬起那条 Raw Input 迟迟不来时，
        # 「最近 send_fallback_ms 毫秒内用过遥控器」就当成遥控器按的。
        self.send_fallback_ms = max(1000, self.cfg.i("general", "send_fallback_ms"))
        # 🔴 v1.7：抬起之后等多久去问「抬起那条 Raw Input 到了没」。
        # 超时了就按「最近活动来自遥控器」兜底，并补发原键。
        self.send_confirm_ms = max(50, self.cfg.i("general", "send_confirm_ms"))
        # 1 = 录音期间吞掉遥控器的 F5（见 EnterSendHook._handle_f5）
        # 根因：WorkBuddy 的 useVoiceAltHotkey 不检查 e.repeat，
        # 遥控器语音键的自动重复会被判成「组合键」而立刻 stop。
        self.hide_remote_f5 = self.cfg.b("general", "hide_remote_f5")
        self.host = None
        self.tray = None
        self.remote = None
        self.player = None
        self.keys = KeySender()
        self._key_events = []      # 键盘事件取证缓冲区（钩子写、主线程落盘）
        self.remote_input = None     # RemoteInputWatcher
        self.enter_hook = None       # EnterSendHook
        self.arbiter = None          # SendArbiter（发送键仲裁）
        self.ime_flag = False        # 输入法是否在组词（ImeProbe 线程维护）
        self.ime_probe = None
        self.ok_send_count = 0
        self._last_f5_raw_log = 0.0     # 语音键原始事件的日志节流
        self._last_other_raw_log = 0.0  # 其它设备按发送键的日志节流
        self._want_reconnect = False
        self._last_check_ts = 0.0    # 上次链路体检时间（节流用）
        self.state = "idle"          # idle | recording
        self.voice_active = False    # 我们正按住 Alt 模拟录音（钩子据此吞掉 F5）
        self.sessions_ok = 0
        self.last_err = ""
        self._hicon_idle = None
        self._hicon_rec = None
        self._hicon_off = None
        self._tip_cache = ""
        self._icon_cache = None
        self._worker = None
        self.start_ts = time.time()

    # ---------------- 生命周期 ----------------
    def run(self):
        user32.SetProcessDPIAware()
        log("=" * 60)
        log("%s v%s 启动 | python=%s | alt=%s(%s) | gain=%.1f"
            % (APP_NAME, APP_VER, sys.version.split()[0], self.alt_key,
               self.alt_mode, self.gain))

        self._make_icons()
        self._create_host()
        self.tray = Tray(self.host, self._hicon_idle if self.enabled else self._hicon_off)
        if not self.tray.add():
            log("！！托盘图标添加失败")
        self._register_hotkey()
        self._refresh_tip()
        user32.SetTimer(self.host, 1, 1000, None)
        self._write_state()

        # 音频输出设备（预热打开，降低首次延迟）
        self.player = WaveOutPlayer(self.cfg.s("general", "output_device"))
        try:
            self.player.open()
            log("音频输出就绪：%s（设备 %d）"
                % (self.cfg.s("general", "output_device"), self.player.device_id))
        except Exception as e:
            self.last_err = str(e)
            log("！！音频输出打开失败：%s —— 语音能收到但 WorkBuddy 听不到。"
                "请安装 VB-CABLE 并把默认录音设备设为 CABLE Output。" % e)
            if self.tray:
                self.tray.balloon("%s：虚拟声卡未就绪" % APP_NAME,
                                  "未找到输出设备「%s」。请安装 VB-CABLE。"
                                  % self.cfg.s("general", "output_device"))

        # ATVV 连接 + 工作线程
        self._start_worker()

        # OK 键 -> 发送：Raw Input 认出遥控器设备 + 低级钩子做转换
        if self.ok_to_send:
            mods, key = parse_combo(self.send_spec)
            if key is None:
                log("！！send_keys=%r 解析失败，OK 键发送不启用" % self.send_spec)
                self.ok_to_send = False
        if self.ok_to_send:
            self.arbiter = SendArbiter(self)
            self.remote_input = RemoteInputWatcher(
                self.host, self.cfg.s("general", "device_match"),
                on_activity=self._on_remote_activity,
                on_keydown=self._on_raw_key,
                on_keyup=self._on_raw_keyup)
            self.remote_input.register()
            self.ime_probe = ImeProbe(self)
            self.ime_probe.start()
            log("OK 键发送已启用：%s -> %s（仅前台窗口含 %r）"
                % (self.send_key_name, self.send_spec,
                   self.ok_send_title or "<任意>"))
            log("  判定方式 v1.7：吞掉「按下」、放行「抬起」，"
                "用抬起那条 Raw Input 的设备归属定性"
                "（抬起确认超过 %dms 未到则按最近 %dms 活动兜底）"
                % (self.send_confirm_ms, self.send_fallback_ms))

        # 键盘钩子：始终安装（与 ok_to_send 无关）
        #   · 录音期间吞掉遥控器的 F5 —— 不然 WorkBuddy 会把它当成
        #     「按住 Alt 时的组合键」，刚开录就停止（只绿一下就没了）
        #   · ok_to_send 打开时，顺便把 Enter 转成发送快捷键
        self.enter_hook = EnterSendHook(self)
        self.enter_hook.install()

        # 欢迎提示。⚠️ 托盘气泡不换行、宽度有限，长说明会被截断，
        # 所以这里只放一行短提示，完整说明在托盘菜单「使用说明」里。
        if self.tray:
            self.tray.balloon(
                "%s v%s 已就绪" % (APP_NAME, APP_VER),
                "按住遥控器语音键说话，松手出字。")

        if self.args.selftest:
            user32.SetTimer(self.host, 2, max(1, self.args.selftest) * 1000, None)
        # 看门狗：100ms 一次，检查有没有「吞了但没等到 Raw Input 确认」的按键
        if self.arbiter:
            user32.SetTimer(self.host, 3, 100, None)

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        log("消息循环结束，清理中")
        self.cleanup()

    def _write_state(self):
        """写下运行状态（版本 + PID + 启动时间），给排错脚本看。"""
        try:
            import json
            data = {
                "ver": APP_VER,
                "pid": os.getpid(),
                "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                "send_key": self.send_key_spec,
                "send_keys": self.send_spec,
                "ok_to_send": bool(self.ok_to_send),
            }
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except Exception as e:
            dbg("写状态文件失败: %r" % (e,))

    def _clear_state(self):
        try:
            if os.path.exists(STATE_PATH):
                os.remove(STATE_PATH)
        except Exception:
            pass

    def _make_icons(self):
        try:
            sz = max(16, min(32, user32.GetSystemMetrics(SM_CXSMICON) or 16))
        except Exception:
            sz = 16
        self._hicon_idle = make_tray_hicon(COLOR_IDLE, sz, ALPHA_GAIN)
        self._hicon_rec = make_tray_hicon(COLOR_REC, sz, ALPHA_GAIN)
        self._hicon_off = make_tray_hicon(COLOR_OFF, sz, ALPHA_GAIN)
        dbg("托盘图标 %dpx（alpha 增益 %.2f）" % (sz, ALPHA_GAIN))

    def _create_host(self):
        self._host_proc = WNDPROC(self._host_wndproc)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._host_proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = HOST_CLASS
        user32.RegisterClassW(ctypes.byref(wc))
        self.host = user32.CreateWindowExW(0, HOST_CLASS, APP_NAME, WS_POPUP,
                                           0, 0, 0, 0, None, None,
                                           kernel32.GetModuleHandleW(None), None)

    def _register_hotkey(self):
        mods, key = self._parse_hotkey(self.quit_spec)
        if key is None:
            log("退出热键解析失败: %r" % self.quit_spec)
            return
        ok = user32.RegisterHotKey(self.host, HOTKEY_ID_QUIT,
                                   mods | MOD_NOREPEAT, key)
        self.quit_hotkey_ok = bool(ok)
        if ok:
            log("退出热键 %s 注册成功" % self.quit_spec)
        else:
            log("！！退出热键 %s 注册失败（err=%s，可能被占用），"
                "托盘图标若被折叠就只能从任务管理器退出了"
                % (self.quit_spec, ctypes.get_last_error()))

    @staticmethod
    def _parse_hotkey(spec):
        mods, key = 0, None
        for part in spec.replace("+", " ").split():
            p = part.strip().lower()
            if p in ("ctrl", "control"):
                mods |= MOD_CONTROL
            elif p == "alt":
                mods |= MOD_ALT
            elif p == "shift":
                mods |= MOD_SHIFT
            elif p == "win":
                mods |= MOD_WIN
            elif len(p) == 1:
                key = ord(p.upper())
            elif p.startswith("f") and p[1:].isdigit():
                key = 0x70 + int(p[1:]) - 1
        return mods, key

    # ---------------- ATVV 工作线程 ----------------
    def _start_worker(self):
        self.remote = AtvvRemote(on_begin=self._on_begin,
                                 on_audio=self._on_audio,
                                 on_end=self._on_end)
        self._try_connect("首次连接")

        def worker():
            while not self.remote._stop.is_set():
                if self.remote.connected:
                    try:
                        self.remote.process_loop(gain=self.gain)
                    except Exception as e:
                        self.last_err = str(e)
                        log("process_loop 异常：%r（等遥控器下次按键再重连）" % (e,))
                        try:
                            self.remote.close()
                        except Exception:
                            pass
                else:
                    # 遥控器休眠时链路静默是正常的，别定时重连刷日志；
                    # 等 Raw Input 看到遥控器按键（说明它醒了）再连。
                    if self._want_reconnect:
                        self._want_reconnect = False
                        self._try_connect("遥控器按键唤醒")
                    time.sleep(0.3)

        self._worker = threading.Thread(target=worker, daemon=True,
                                        name="atvv-worker")
        self._worker.start()

    def _try_connect(self, why: str) -> bool:
        try:
            self.remote.connect(self.cfg.s("general", "device_match"))
            self.remote.reconnects += 1
            return True
        except Exception as e:
            self.last_err = str(e)
            dbg("%s：ATVV 连接失败 %s" % (why, e))
            return False

    def request_reconnect(self):
        """手动/自动触发一次重连（托盘菜单或遥控器按键唤醒）。"""
        self._want_reconnect = True

    # ---------------- 会话回调（ATVV 线程）----------------
    def _on_remote_activity(self):
        """Raw Input 看到遥控器按键时调用（消息线程）。

        这里同时兼做「链路体检」：遥控器会休眠断链，而断链后事件只是静默，
        程序自己发现不了。既然按键说明它醒了，就顺手确认/恢复连接。

        🔴 绝对不能在这里 close()：遥控器按键产生的 HID 事件和 ATVV 通知
        几乎同时到达，一旦 close 掉句柄，这一次语音键就白按了。所以这里只做
        「无损体检」——设备接口还在枚举里就说明链路活着，什么都不动。
        """
        if not self.remote:
            return
        if not self.remote.connected:
            self._want_reconnect = True
            return
        idle = time.time() - self.remote.last_event_ts
        if idle < 10.0:
            return
        if time.time() - self._last_check_ts < 10.0:
            return                       # 体检本身也有开销，最多 10 秒一次
        self._last_check_ts = time.time()
        try:
            alive = find_atvv_interface(self.cfg.s("general", "device_match")) is not None
        except Exception:
            alive = True                 # 查不出来就当它还活着，宁可不动
        if alive:
            dbg("链路静默 %ds，接口仍在线 —— 不动" % int(idle))
        else:
            dbg("链路静默 %ds，接口已消失 —— 标记重连" % int(idle))
            self._want_reconnect = True

    def _on_raw_key(self, vkey: int, makecode: int, is_remote: bool):
        """Raw Input 的**按下**事件。

        🔴 v1.7：按下被吞时根本不会有这条事件（实测：全吞 0 条），
        所以这里收到的一定是「我们没吞的那一下」—— 没有 pending 可结算，
        这个回调现在只负责「记账」：
          · 实体键盘按了发送键 -> 记下时刻（给时间窗兜底当反向证据）
          · 遥控器按了键       -> 刷新遥控器活动时间
        """
        try:
            now = time.time()
            # 记下「最近一次实体键盘按发送键」的时刻 —— 时间窗兜底时
            # 要用它区分「这次回车是键盘打的」还是「遥控器打的」。
            if vkey == self.send_key_vk and not is_remote:
                if self.remote_input:
                    self.remote_input.last_send_other_ts = now
            if is_remote:
                if vkey == self.send_key_vk:
                    log("Raw 遥控器按下发送键 vk=0x%02X（这次没被吞，"
                        "说明前置条件不满足）" % vkey)
                elif vkey == VK_F5:
                    if now - self._last_f5_raw_log > 1.0:
                        self._last_f5_raw_log = now
                        log("Raw 遥控器语音键 vk=0x%02X 到达（该键会被钩子吞掉）"
                            % vkey)
            elif vkey == self.send_key_vk:
                # 别的设备按了发送键 —— 这是「原样补发回车」路径的证据。
                # 节流 1 秒，否则木木用实体键盘打字时会刷屏。
                if now - self._last_other_raw_log > 1.0:
                    self._last_other_raw_log = now
                    log("Raw 其它设备按下发送键 vk=0x%02X -> 不是遥控器"
                        % vkey)
        except Exception:
            pass

    def _on_raw_keyup(self, vkey: int, makecode: int, is_remote: bool):
        """🔴🔴 v1.7 主路径：**抬起**那条 Raw Input 到了。

        这是整套新架构的落点 ——
          · 按下被钩子吞掉 -> 没有 Raw Input
          · 抬起被放行     -> Raw Input 一定会到
          · 它带的 hDevice = **这一下按键**按下去的设备
        所以这里的 `is_remote` 是**确定**的，不需要再靠时间窗猜。
        """
        try:
            if self.arbiter:
                self.arbiter.on_raw_keyup(vkey, makecode, is_remote)
        except Exception:
            pass

    def _touch_remote(self):
        """兜底：语音会话开始/结束时也刷新遥控器活动时间（Raw Input 失败时靠它）。"""
        if self.remote_input:
            self.remote_input.last_activity = time.time()

    def ok_precheck(self) -> bool:
        """钩子里的**前置条件**判定：这一下要不要进入「发送键仲裁」。

        ⚠️ 这里**故意不查遥控器活动**！
        遥控器的 OK 键在 Windows 上就是普通 Enter，钩子同步先跑的时候，
        这一下自己的 Raw Input 还没到 —— 任何「最近活动过」的时间窗都会
        **漏掉第一次按 OK**（这正是 v1.3 一直不生效的原因）。
        设备归属交给异步的仲裁器用 Raw Input 去判。

        判定链（任何一条不满足就原样放行，不吞）：
          1. 总开关 ok_to_send + 程序未暂停
          2. 前台窗口标题匹配（默认 WorkBuddy）
          3. 输入法没有在组词（否则 Enter 要留给输入法选词）
          4. 当前没有任何修饰键按下（用户自己按 Ctrl+Enter 时不干预）
        """
        if not self.ok_to_send or not self.enabled:
            return False
        fg = user32.GetForegroundWindow()
        if not fg or not title_matches(fg, self.ok_send_title):
            return False
        if self.ime_flag:
            return False            # 输入法在组词，Enter 要留给它选词
        if self.enter_hook is not None and self.enter_hook.mods_down():
            return False            # 用户自己按着组合键，别掺和
        return True

    def send_key_verdict(self):
        """这次被吞掉的发送键，是不是遥控器按的？返回 (是否遥控器, 依据文字)。

        🔴 v1.7：这是**兜底**判据（主判据是「抬起那条 Raw Input 的 hDevice」，
        那是确定的）。只有在抬起确认迟迟不来时才用到这里。

        判据（两条都要满足才算遥控器）：
          1. 遥控器在 send_fallback_ms（默认 30 秒）窗口内有过活动
             —— 必须宽，因为「说话 -> 松手 -> 按 OK 键」间隔经常超 10 秒；
          2. **且**最近一次「实体键盘活动」不比遥控器活动更近
             —— 木木用实体键盘打字时，别把它误当成遥控器。

        ⚠️ 只看「实体键盘有没有活动」，不看「有没有按发送键」：
           木木打字用的是字母键，也算「实体键盘活动」，
           这样「打字之后又用遥控器」的场景不会被误判。
           反过来，「刚打完字就按回车」会被判成键盘 -> 原样补发，行为不变。
        """
        ri = self.remote_input
        if not ri:
            return False, "没有 Raw Input 监听"
        now = time.time()
        last_remote = getattr(ri, "last_activity", 0.0)
        if not last_remote:
            return False, "还没见过遥控器按键"
        age = now - last_remote
        if age * 1000.0 > self.send_fallback_ms:
            return False, ("遥控器 %.0f 秒前才活动过，超出 %dms 窗口"
                           % (age, self.send_fallback_ms))
        last_phys = getattr(ri, "last_physical_ts", 0.0)
        if last_phys > last_remote:
            return False, "最近一次键盘活动来自实体键盘"
        return True, "遥控器 %.1f 秒内有活动" % age

    def remote_recently(self) -> bool:
        """兼容旧接口：等价于 send_key_verdict()[0]。"""
        return self.send_key_verdict()[0]

    def _on_begin(self):
        if not self.enabled:
            dbg("已暂停，忽略会话开始")
            return
        self._touch_remote()
        log("会话开始：触发 %s（%s 模式）" % (self.alt_key, self.alt_mode))
        if self.focus_wb:
            h = find_window_by_title(self.focus_title)
            if h:
                if user32.IsIconic(h):
                    user32.ShowWindow(h, 9)  # SW_RESTORE
                user32.SetForegroundWindow(h)
                log("已聚焦窗口：%s" % self.focus_title)
            else:
                log("没找到标题含 %r 的窗口（不聚焦，用当前焦点）" % self.focus_title)
        # 🔴 顺序很重要：voice_active 必须先于 Alt 按下就置位。
        #    遥控器的 F5 按下后会立刻开始自动重复（每秒 30 次），
        #    只要在按下 Alt 的那一瞬间没开始拦，WorkBuddy 就会收到
        #    「按住 Alt 期间的其它键」-> 判定成组合键 -> 立刻停止录音。
        self.voice_active = True
        try:
            if self.alt_mode == "tap":
                self.keys.tap(self.alt_key, self.alt_tap_ms)
            else:
                self.keys.down(self.alt_key)
                if self.alt_pre_ms > 0:
                    time.sleep(self.alt_pre_ms / 1000.0)
        except Exception as e:
            self.last_err = str(e)
            self.voice_active = False
            log("触发按键失败: %r" % (e,))
            return
        self.state = "recording"
        self._post(1)  # 让主线程刷托盘图标

    def _on_audio(self, pcm: bytes):
        if not self.enabled:
            return
        try:
            self.player.write(pcm)
        except Exception as e:
            self.last_err = str(e)
            log("音频写入失败: %r" % (e,))

    def _on_end(self, sess):
        if not self.enabled:
            return
        self._touch_remote()
        # 等 waveOut 把残余音频播完，再松开按键，避免 WorkBuddy 切掉尾音
        try:
            self.player.wait_done(timeout=2.0)
        except Exception as e:
            dbg("wait_done 异常: %r" % (e,))
        try:
            if self.alt_mode == "tap":
                self.keys.tap(self.alt_key, self.alt_tap_ms)
            else:
                self.keys.up(self.alt_key)
        except Exception as e:
            self.last_err = str(e)
            log("松开按键失败: %r" % (e,))
        # Alt 已经松开，WorkBuddy 那边不会再收到非修饰键，收尾后再放开 F5 拦截
        self.voice_active = False
        self.state = "idle"
        self.sessions_ok += 1
        self._post(1)
        log("会话收尾完成（累计 %d 次）" % self.sessions_ok)

    def _post(self, code):
        if self.host:
            user32.PostMessageW(self.host, WM_APP_STATE, code, 0)

    def _flush_key_events(self):
        """把钩子采集到的键盘事件写到 mivoice_keys.log（主线程做，不阻塞钩子）。"""
        try:
            evs = self._key_events
            if not evs:
                return
            self._key_events = []
            kinds = {WM_KEYDOWN: "down", WM_KEYUP: "up",
                     WM_SYSKEYDOWN: "sysdown", WM_SYSKEYUP: "sysup"}
            alt = 0
            try:
                if user32.GetAsyncKeyState(VK_MENU) & 0x8000:
                    alt |= 1
                if user32.GetAsyncKeyState(VK_LMENU) & 0x8000:
                    alt |= 2
            except Exception:
                pass
            with open(KEYLOG_PATH, "a", encoding="utf-8") as f:
                for ev in evs:
                    ts, wparam, vk, scan, flags, swallowed = ev[:6]
                    tag = ev[6] if len(ev) > 6 else ""
                    kind = kinds.get(wparam, str(wparam))
                    src = "INJ" if (flags & LLKHF_INJECTED) else "HW "
                    if tag == "SND":
                        src = "SND"          # 发送键「按下」被仲裁器吞
                    elif tag == "UPE":
                        src = "UPE"          # 发送键「抬起」放行 -> Raw Input 会到
                    elif tag == "SWAL" or swallowed:
                        src = "SWAL"          # 被 MiVoice 拦下，没送到 WorkBuddy
                    f.write("%s  %-7s vk=0x%02X scan=0x%02X flags=0x%X %s "
                            "alt=%d state=%s\n"
                            % (time.strftime("%H:%M:%S") + ".%03d"
                               % int((ts % 1) * 1000),
                               kind, vk, scan, flags, src, alt, self.state))
        except Exception:
            pass


    # ---------------- 托盘界面 ----------------
    def _refresh_tip(self):
        if not self.tray:
            return
        if not self.enabled:
            tip = "%s | 已暂停" % APP_NAME
            icon = self._hicon_off
        elif self.state == "recording":
            tip = "%s | 录音中…" % APP_NAME
            icon = self._hicon_rec
        elif self.remote and self.remote.connected:
            extra = ("，发送 %d 次" % self.ok_send_count) if self.ok_to_send else ""
            tip = "%s | 待命（语音 %d 次%s）" % (APP_NAME, self.sessions_ok, extra)
            icon = self._hicon_idle
        else:
            tip = "%s | 遥控器未连接" % APP_NAME
            icon = self._hicon_off
        if tip != self._tip_cache:
            self._tip_cache = tip
            self.tray.set_tip(tip)
        if icon and icon != self._icon_cache:
            self._icon_cache = icon
            self.tray.set_icon(icon)

    def show_menu(self):
        hmenu = user32.CreatePopupMenu()
        if not self.enabled:
            state_txt = "状态：已暂停"
        elif self.state == "recording":
            state_txt = "状态：录音中…"
        else:
            state_txt = "状态：待命"
        user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, 0, state_txt)
        user32.AppendMenuW(hmenu, MF_STRING | MF_GRAYED, 0,
                           "遥控器：%s｜成功 %d 次"
                           % ("已连接" if (self.remote and self.remote.connected)
                              else "未连接", self.sessions_ok))
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, IDM_TOGGLE,
                           "暂停映射" if self.enabled else "恢复映射")
        user32.AppendMenuW(hmenu, MF_STRING, IDM_ALTTEST,
                           "测试 %s 触发（看 WorkBuddy 有反应没）" % self.alt_key)
        user32.AppendMenuW(hmenu, MF_STRING, IDM_SENDTEST,
                           "测试发送（%s）" % self.send_spec)
        user32.AppendMenuW(hmenu, MF_STRING, IDM_RECONNECT, "重新连接遥控器")
        user32.AppendMenuW(hmenu, MF_STRING, IDM_SCANKEYS,
                           "扫描遥控器按键（查每个键是什么）")
        user32.AppendMenuW(hmenu, MF_STRING, IDM_AUTOSTART,
                           "关闭开机自启" if autostart_enabled() else "开启开机自启")
        user32.AppendMenuW(hmenu, MF_STRING, IDM_LOGFILE, "查看运行日志")
        user32.AppendMenuW(hmenu, MF_STRING, IDM_HELP, "使用说明")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, IDM_QUIT,
                           "退出 %s（%s）" % (APP_NAME, self.quit_spec))

        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        user32.SetForegroundWindow(self.host)
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON
                                    | TPM_NONOTIFY | TPM_LEFTALIGN | TPM_BOTTOMALIGN,
                                    p.x, p.y, 0, self.host, None)
        user32.PostMessageW(self.host, WM_NULL, 0, 0)
        user32.DestroyMenu(hmenu)

        if cmd == IDM_TOGGLE:
            self.enabled = not self.enabled
            self.cfg.set("general", "enabled", "1" if self.enabled else "0")
            log("映射已%s" % ("启用" if self.enabled else "暂停"))
            self._refresh_tip()
        elif cmd == IDM_ALTTEST:
            self._alt_test()
        elif cmd == IDM_SENDTEST:
            self._send_test()
        elif cmd == IDM_RECONNECT:
            if self.remote and self.remote.connected:
                try:
                    self.remote.close()
                except Exception:
                    pass
            self.request_reconnect()
            log("手动重连遥控器")
            if self.tray:
                self.tray.balloon(APP_NAME, "正在重新连接遥控器…（按一下语音键唤醒它）")
        elif cmd == IDM_SCANKEYS:
            self._scan_keys()
        elif cmd == IDM_AUTOSTART:
            on = not autostart_enabled()
            if autostart_set(on):
                self.cfg.set("general", "autostart", "1" if on else "0")
                log("开机自启 -> %s" % ("开" if on else "关"))
                if self.tray:
                    self.tray.balloon(APP_NAME, "开机自启已%s" % ("开启" if on else "关闭"))
        elif cmd == IDM_LOGFILE:
            try:
                os.startfile(LOG_PATH)
            except Exception as e:
                log("打开日志失败: %r" % (e,))
        elif cmd == IDM_HELP:
            self._show_help()
        elif cmd == IDM_QUIT:
            log("用户从托盘菜单退出")
            user32.DestroyWindow(self.host)

    def _show_help(self):
        """完整用法说明。

        原来用托盘气泡显示，气泡不换行又会被截断（一半字看不见），
        改成 MessageBox —— 支持 \\r\\n 换行，窗口也会自适应大小。
        """
        text = (
            "【%s v%s】\r\n"
            "\r\n"
            "1. Windows 声音设置里，把默认录音设备设为 CABLE Output\r\n"
            "2. 点进 WorkBuddy 输入框，按住遥控器语音键说话\r\n"
            "3. 松手出字；按遥控器的「%s」键发送（= %s）\r\n"
            "\r\n"
            "注意：\r\n"
            "· 说话和发送时，焦点都要在 WorkBuddy 输入框\r\n"
            "· 「%s」键只有在焦点窗口标题含「%s」时才会变成发送\r\n"
            "· 实体键盘的 Enter 不受影响（只是会晚几毫秒送达）\r\n"
            "· 退出热键：%s\r\n"
            "\r\n"
            "排错：托盘菜单「查看日志」"
            % (APP_NAME, APP_VER, self.send_key_name, self.send_spec,
               self.send_key_name, self.ok_send_title or "<任意窗口>",
               self.quit_spec))
        if self.tray:
            self.tray.msgbox("%s v%s 用法" % (APP_NAME, APP_VER), text)

    def _scan_keys(self):
        """开一个控制台窗口跑「遥控器按键扫描器」。

        用途：想知道遥控器上每个键在 Windows 里是什么 vk，
        好把任意一个键绑成发送键（mivoice.ini 的 send_key）。
        扫描器在 ..\\tools\\scan_remote_keys.py。
        """
        script = os.path.join(os.path.dirname(HERE), "tools",
                              "scan_remote_keys.py")
        if not os.path.exists(script):
            if self.tray:
                self.tray.balloon(APP_NAME, "找不到扫描器：%s" % script)
            return
        # 本程序是 pythonw 起的（没有控制台），要换成 python.exe 才有输出窗口
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            cand = os.path.join(os.path.dirname(exe), "python.exe")
            exe = cand if os.path.exists(cand) else "python"
        try:
            subprocess.Popen([exe, script, "--seconds", "120"],
                             creationflags=getattr(subprocess,
                                                  "CREATE_NEW_CONSOLE", 0))
            log("已启动遥控器按键扫描器：%s" % script)
            if self.tray:
                self.tray.balloon(APP_NAME,
                                  "已打开扫描器窗口：把遥控器每个键按一遍")
        except Exception as e:
            log("启动扫描器失败: %r" % (e,))
            if self.tray:
                self.tray.balloon(APP_NAME, "启动扫描器失败：%r" % (e,))

    def _send_test(self):
        """手动发一次发送快捷键，验证 WorkBuddy 的发送设置对得上。"""
        log("手动测试发送：%s" % self.send_spec)
        try:
            self.keys.send_combo(self.send_spec)
        except Exception as e:
            log("测试发送失败: %r" % (e,))
            return
        if self.tray:
            self.tray.balloon("%s：已发送 %s" % (APP_NAME, self.send_spec),
                              "如果输入框里的文字被发出去了，说明这个组合键就是"
                              "WorkBuddy 的发送键；否则改 mivoice.ini 的 send_keys。")

    def _alt_test(self):
        """手动模拟一次触发，验证 WorkBuddy 对按键有反应。"""
        log("手动测试 %s 触发（%s 模式）" % (self.alt_key, self.alt_mode))
        try:
            if self.alt_mode == "tap":
                self.keys.tap(self.alt_key, self.alt_tap_ms)
            else:
                self.keys.down(self.alt_key)
                time.sleep(0.6)
                self.keys.up(self.alt_key)
        except Exception as e:
            log("测试触发失败: %r" % (e,))
            return
        if self.tray:
            self.tray.balloon("%s：已发送 %s" % (APP_NAME, self.alt_key),
                              "如果 WorkBuddy 的语音输入有反应（麦克风按钮变红/出现波形），"
                              "说明触发链路 OK；否则在 mivoice.ini 里换 alt_key / alt_mode。")

    # ---------------- 窗口过程 ----------------
    def _host_wndproc(self, hwnd, msg, wp, lp):
        if msg == WM_INPUT:
            if self.remote_input:
                self.remote_input.handle(lp)
            return 0
        if msg == WM_TRAY:
            if lp == 0x0205:      # WM_RBUTTONUP
                self.show_menu()
            elif lp == 0x0202:    # WM_LBUTTONUP -> 左键也弹菜单（没有单击动作可做）
                self.show_menu()
            return 0
        if msg == WM_COMMAND:
            return 0
        if msg == WM_HOTKEY:
            if wp == HOTKEY_ID_QUIT:
                log("退出热键 %s 触发" % self.quit_spec)
                user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_TIMER:
            if wp == 2 and self.args.selftest:
                log("selftest %d 秒到，自动退出" % self.args.selftest)
                user32.DestroyWindow(hwnd)
                return 0
            if wp == 3:
                if self.arbiter:
                    self.arbiter.tick()      # 看门狗：超时补发
                return 0
            self._refresh_tip()
            self._flush_key_events()
            return 0
        if msg == WM_APP_STATE:
            self._refresh_tip()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    # ---------------- 清理 ----------------
    def cleanup(self):
        log("清理中…")
        self._clear_state()
        # 复位：异常退出时 Alt 可能还按着，voice_active 必须清掉，
        # 否则退出流程里 F5 会被无谓地吞掉（虽然进程马上就退了，但日志会误导）。
        self.voice_active = False
        if self.ime_probe:
            try:
                self.ime_probe.stop()
            except Exception:
                pass
        if self.enter_hook:
            try:
                self.enter_hook.uninstall()
            except Exception:
                pass
        try:
            self.keys.release_all()
        except Exception:
            pass
        if self.remote:
            try:
                self.remote.stop()
                self.remote.close()
            except Exception:
                pass
        if self.player:
            try:
                self.player.close(drain=False)
            except Exception:
                pass
        if self.tray:
            self.tray.remove()
        log("已退出。本次成功会话 %d 次，运行 %.0f 秒"
            % (self.sessions_ok, time.time() - self.start_ts))


# ===========================================================================
# 12. 单实例 + 命令行入口
# ===========================================================================
def _single_instance():
    """命名互斥体：重复双击启动时第二个实例直接退出。"""
    k = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not k:
        return True  # 拿不到句柄就放行，别因小失大
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(k)
        return False
    return True


def parse_args(argv):
    class A:
        pass
    a = A()
    a.debug = "--debug" in argv
    a.selftest = 0
    a.mode = "run"
    for i, x in enumerate(argv):
        if x == "--selftest" and i + 1 < len(argv):
            try:
                a.selftest = int(argv[i + 1])
            except ValueError:
                pass
        elif x == "--probe":
            a.mode = "probe"
        elif x == "--alt-test":
            a.mode = "alt-test"
        elif x == "--status":
            a.mode = "status"
    return a


def cmd_status():
    """一次性自检：遥控器 / VB-CABLE / ATVV 是否就绪。"""
    print("== 遥控语音 环境自检 ==")
    path = find_atvv_interface("2717")
    print("ATVV 设备接口: %s" % (path or "未找到（遥控器休眠了？按一下语音键唤醒）"))
    outs = list_output_devices()
    cable_in = [n for n in outs if "cable input" in n.lower()]
    print("waveOut 设备数: %d" % len(outs))
    for n in outs:
        print("   - %s" % n)
    if cable_in:
        print("=> CABLE Input 存在，可以输出")
    else:
        print("=> 没有 CABLE Input：请先安装 VB-CABLE 并重启")
    ins_n = winmm.waveInGetNumDevs()
    print("waveIn 设备数: %d（CABLE Output 应该出现在这里；"
          "Windows 声音设置里把它设为默认输入）" % ins_n)


def cmd_alt_test(cfg: Config):
    keys = KeySender()
    key = cfg.s("general", "alt_key") or "alt"
    mode = cfg.s("general", "alt_mode") or "hold"
    print("模拟触发：%s（%s 模式）" % (key, mode))
    if mode == "tap":
        keys.tap(key, cfg.i("general", "alt_tap_ms"))
    else:
        keys.down(key)
        time.sleep(0.6)
        keys.up(key)
    print("已发送。盯住 WorkBuddy 的麦克风按钮，看它有没有进入录音状态。")


def cmd_probe(cfg: Config):
    """连接遥控器，等一次语音会话，存成 wav 验证音频链路（不需要 VB-CABLE）。"""
    import wave
    gain = cfg.f("general", "gain")
    print("连接 ATVV…（若失败请先按一下遥控器语音键唤醒设备）")
    remote = AtvvRemote()
    remote.connect(cfg.s("general", "device_match"))
    got = {}

    def on_begin():
        print("会话开始，说话吧…")
        got["begin"] = time.time()

    def on_audio(pcm):
        got.setdefault("pcm", bytearray()).extend(pcm)

    def on_end(sess):
        got["end"] = time.time()
        got["sess"] = sess

    remote.on_begin = on_begin
    remote.on_audio = on_audio
    remote.on_end = on_end
    t = threading.Thread(target=remote.process_loop, kwargs={"gain": gain}, daemon=True)
    t.start()

    # 等能力协商结果（ connect 里已主动发 get_caps ）
    deadline = time.time() + 8
    while remote.protocol_version == 0 and time.time() < deadline:
        try:
            while True:
                remote._on_control(remote.control_q.get_nowait(), gain)
        except queue.Empty:
            time.sleep(0.2)
    print("协议版本 0x%04X codec=%d" % (remote.protocol_version, remote.selected_codec))
    print("现在按住遥控器语音键说几句话，然后松手（最多等 120 秒）…")
    deadline = time.time() + 120
    while "end" not in got and time.time() < deadline:
        time.sleep(0.2)
    remote.stop()
    remote.close()
    pcm = bytes(got.get("pcm", b""))
    if not pcm:
        print("没收到音频。可能原因：没按语音键 / 设备休眠 / 协商失败。")
        return
    out = os.path.join(HERE, "probe.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    print("已保存 %s（%.2fs）—— 播放听听里面有没有你的声音。"
          % (out, len(pcm) / 2 / SAMPLE_RATE))


def main():
    args = parse_args(sys.argv[1:])
    if args.mode == "status":
        cmd_status()
        return
    if args.mode == "alt-test":
        cmd_alt_test(Config())
        return
    if args.mode == "probe":
        cmd_probe(Config())
        return

    if not _single_instance():
        log("已有实例在运行，本次启动退出")
        return
    App(args).run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        tb = traceback.format_exc()
        log("主线程崩溃：\n%s" % tb)
        try:
            import ctypes as _c
            _c.windll.user32.MessageBoxW(
                None, tb[-800:], "%s 崩溃" % APP_NAME, 0x10)
        except Exception:
            pass
        raise
