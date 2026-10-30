# -*- coding: utf-8 -*-
"""Raw Input 设备枚举探针 v2：把「遥控器到底有几个 hDevice、名字读不读得到」查清楚。

🔴 背景（v1.6 要修的 bug）：
  遥控器 OK 键在 Windows 上就是普通 Enter，只有 Raw Input 的 hDevice 能分设备。
  但主程序读 hDevice 名字时**偶尔读出空串**，且**把空串也缓存住了** ->
  之后这个 hDevice 永远被判成「不是遥控器」-> OK 键发不出去。

  v1.5 日志实证（17:45:02 同一秒两条矛盾日志）：
      发送键已转换：Enter 回车 -> ctrl+enter（来源 timeout）
      Raw 其它设备按下发送键 vk=0x0D -> 不是遥控器，原样补发
  注意第二条**前面没有**「Raw Input 键盘设备: ...」这一行 ->
  说明 device_name() 读出的是**空串**（v1.0.0 的 is_remote 遇到空名直接静默 return False）。

🔴 这个探针要回答：
  1. GetRawInputDeviceList 到底能不能用？（前一个版本报 err=87）
  2. 遥控器有几个顶层集合（hDevice）？每个的名字读不读得到？
  3. 用 SetupAPI 交叉验证：遥控器到底暴露了几个 HID 集合？

用法：
  python probe_raw_devices.py            一次性枚举
  python probe_raw_devices.py --watch    每 2 秒刷新（看插拔）
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
setupapi = M.setupapi

# ---- 补声明（本文件用到的，mivoice.py 里没声明全）----
user32.GetRawInputDeviceList.restype = wt.UINT
user32.GetRawInputDeviceList.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.UINT)]

# 🔴 Win32 常量（这里写死并标注含义，别再被「低位是索引」的误解带偏）：
#    RIDI_PREPARSEDDATA = 0x20000001
#    RIDI_OTHERUSAGE   = 0x20000010
#    RIDI_DEVICENAME   = 0x20000007   <-- 取设备名的唯一正确命令
#    RIDI_DEVICEINFO   = 0x20000000   <-- 取 RAWINPUTDEVINFO 结构，不是名字
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x20000000

GUID_DEVINTERFACE_HID = "{4d1e55b2-f16f-11cf-88cb-001111000030}"


class RAWINPUTDEVICE_LIST(ctypes.Structure):
    _fields_ = [("hDevice", wt.HANDLE), ("dwType", wt.DWORD)]


def try_list_variants():
    """GetRawInputDeviceList 有几种调用姿势，全试一遍。

    🔴 【关键坑】puiNumDevices 是 **in-out** 参数：
      入参 = 缓冲区能装多少个 RAWINPUTDEVICE_LIST，出参 = 实际装了几个。
      传 0 进去 -> API 认为缓冲区是空的 -> 直接 ERROR_INVALID_PARAMETER(87)。
      （这正是之前「本机 GetRawInputDeviceList 恒 87」的真正原因，不是本机不支持！）
    """
    print("-" * 70)
    print("GetRawInputDeviceList 调用姿势对比")
    print("-" * 70)

    # 姿势 A：先 NULL 拿数量（网上常见写法）
    n = wt.UINT(0)
    r = user32.GetRawInputDeviceList(None, ctypes.byref(n))
    print("  A) (NULL, &n)          -> ret=%s err=%s n=%s"
          % (r, ctypes.get_last_error(), n.value))

    # 姿势 B：给 buffer 但容量写 0（🔴 错误姿势，会得到 87）
    n2 = wt.UINT(0)
    buf = (RAWINPUTDEVICE_LIST * 256)()
    r2 = user32.GetRawInputDeviceList(buf, ctypes.byref(n2))
    print("  B) (buf256, &n=0)      -> ret=%s err=%s n=%s   <- 容量写 0，错误姿势"
          % (r2, ctypes.get_last_error(), n2.value))

    # 姿势 C：给 buffer 且容量 = 元素个数（🔴 正确姿势）
    n3 = wt.UINT(256)
    buf3 = (RAWINPUTDEVICE_LIST * 256)()
    r3 = user32.GetRawInputDeviceList(buf3, ctypes.byref(n3))
    print("  C) (buf256, &n=256)    -> ret=%s err=%s n=%s   <- 正确姿势"
          % (r3, ctypes.get_last_error(), n3.value))

    if r3 != 0xFFFFFFFF and n3.value > 0:
        print("  => ✅ 姿势 C 可用，设备数 = %d" % n3.value)
        return [(buf3[i].hDevice, buf3[i].dwType) for i in range(n3.value)]
    print("  => 全部失败")
    return None


def dev_name(hdev):
    """按官方 RIDI_DEVICENAME 读名字。返回 (name, err)。"""
    size = wt.UINT(0)
    user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME, None,
                                  ctypes.byref(size))
    if not size.value:
        return "", ctypes.get_last_error()
    b = ctypes.create_unicode_buffer(size.value + 1)
    if not user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICENAME, b,
                                         ctypes.byref(size)):
        return "", ctypes.get_last_error()
    return b.value, 0


def dev_info(hdev):
    """RIDI_DEVICEINFO：拿 usage page/usage。"""
    size = wt.UINT(0)
    user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICEINFO, None,
                                  ctypes.byref(size))
    if not size.value:
        return None
    b = ctypes.create_string_buffer(size.value)
    if not user32.GetRawInputDeviceInfoW(wt.HANDLE(hdev), RIDI_DEVICEINFO, b,
                                         ctypes.byref(size)):
        return None
    return {"page": int.from_bytes(b.raw[8:10], "little"),
            "usage": int.from_bytes(b.raw[10:12], "little")}


def setupapi_hid_collections():
    """用 SetupAPI 枚举 HID 设备实例（交叉验证遥控器有几个集合）。"""
    print("-" * 70)
    print("SetupAPI 枚举 HID 设备实例")
    print("-" * 70)
    guid = M.guid_from_string(GUID_DEVINTERFACE_HID)
    devs = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                          M.DIGCF_PRESENT | M.DIGCF_DEVICEINTERFACE)
    if not devs:
        print("  SetupDiGetClassDevsW 失败")
        return []
    out = []
    idx = 0
    while True:
        iface = M.SP_DEVICE_INTERFACE_DATA()
        iface.cbSize = ctypes.sizeof(M.SP_DEVICE_INTERFACE_DATA)
        if not setupapi.SetupDiEnumDeviceInterfaces(devs, None,
                                                    ctypes.byref(guid), idx,
                                                    ctypes.byref(iface)):
            break
        needed = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(devs, ctypes.byref(iface),
                                                  None, 0, ctypes.byref(needed),
                                                  None)
        if needed.value:
            buf = ctypes.create_string_buffer(needed.value)
            cb_size = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buf, ctypes.byref(wt.DWORD(cb_size)), 4)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    devs, ctypes.byref(iface), buf, needed.value, None, None):
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                if path:
                    out.append(path)
        idx += 1
    setupapi.SetupDiDestroyDeviceInfoList(devs)

    remote = [p for p in out if "vid_2717" in p.lower() or "vid&012717" in p.lower()]
    other = [p for p in out if p not in remote]
    print("  HID 接口总数 = %d" % len(out))
    print("  其中属于遥控器 (VID_2717) 的 = %d 个：" % len(remote))
    for p in remote:
        print("      %s" % p[:150])
    print("  其它 HID 设备 = %d 个" % len(other))
    for p in other[:8]:
        print("      %s" % p[:150])
    return out


def main():
    watch = "--watch" in sys.argv
    print("=" * 70)
    print("Raw Input 设备枚举探针 v2")
    print("=" * 70)
    print()

    devs = try_list_variants()
    print()
    setupapi_hid_collections()
    print()

    if devs:
        print("-" * 70)
        print("Raw Input 设备明细")
        print("-" * 70)
        for hdev, dwType in devs:
            name, err = dev_name(hdev)
            info = dev_info(hdev)
            mark = ""
            low = (name or "").lower()
            if "vid&012717" in low or "vid_2717" in low:
                mark = "   <== 遥控器"
            print("  hDevice=0x%X dwType=%s%s" % (hdev, dwType, mark))
            if info:
                print("      DEVICEINFO page=0x%02X usage=0x%02X"
                      % (info["page"], info["usage"]))
            if name:
                print("      名字: %s" % name[:140])
            else:
                print("      名字: <读不到> err=%s" % err)
        print()

    if not watch:
        return 0
    print("按 Ctrl+C 退出。")
    while True:
        time.sleep(2.0)
        try_list_variants()
        print()


if __name__ == "__main__":
    sys.exit(main())
