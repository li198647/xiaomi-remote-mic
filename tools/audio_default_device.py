#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Query or set Windows default capture endpoint using MMDevice COM APIs."""
import ctypes
import ctypes.wintypes as wt
import sys
import uuid

ole32 = ctypes.OleDLL("ole32")
oleaut32 = ctypes.OleDLL("oleaut32")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ole32.CoInitialize.argtypes = [ctypes.c_void_p]
ole32.CoInitialize.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.DWORD,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

CLSCTX_ALL = 23
E_CAPTURE = 1
ROLES = ((0, "Console"), (1, "Multimedia"), (2, "Communications"))
CABLE_ID = "{0.0.1.00000000}.{73c892f8-39fa-4792-bbe0-5edbd91d193f}".upper()
MIC_ID = "{0.0.1.00000000}.{1c516d9e-c1da-4b06-9cce-cefb552a7166}".upper()


def guid(text):
    raw = uuid.UUID(text).bytes_le
    return (ctypes.c_ubyte * 16).from_buffer_copy(raw)


def method(ptr, index, restype, *argtypes):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def check(hr, what):
    if hr < 0:
        raise OSError("%s failed: 0x%08X" % (what, hr & 0xFFFFFFFF))


def endpoint_id(enum, role):
    dev = ctypes.c_void_p()
    get_default = method(enum, 4, ctypes.c_long, wt.DWORD, wt.DWORD,
                         ctypes.POINTER(ctypes.c_void_p))
    check(get_default(enum, E_CAPTURE, role, ctypes.byref(dev)), "GetDefaultAudioEndpoint")
    try:
        get_id = method(dev, 5, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
        p = ctypes.c_void_p()
        check(get_id(dev, ctypes.byref(p)), "IMMDevice.GetId")
        value = ctypes.wstring_at(p.value)
        ole32.CoTaskMemFree(p)
        return value
    finally:
        method(dev, 2, wt.ULONG)(dev)


def main():
    check(ole32.CoInitialize(None), "CoInitialize")
    enum = ctypes.c_void_p()
    try:
        clsid = guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        iid = guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        check(ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL,
                                     ctypes.byref(iid), ctypes.byref(enum)),
              "create MMDeviceEnumerator")
        print("Current default capture endpoints:")
        for role, name in ROLES:
            try:
                print("  %-13s %s" % (name, endpoint_id(enum, role)))
            except OSError as e:
                print("  %-13s %s" % (name, e))
        if "--set-cable" not in sys.argv and "--set-mic" not in sys.argv:
            return 0
        target = CABLE_ID if "--set-cable" in sys.argv else MIC_ID
        policy = ctypes.c_void_p()
        pclsid = guid("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")
        piid = guid("{F8679F50-850A-41CF-9C72-430F290290C8}")
        check(ole32.CoCreateInstance(ctypes.byref(pclsid), None, CLSCTX_ALL,
                                     ctypes.byref(piid), ctypes.byref(policy)),
              "create IPolicyConfig")
        try:
            set_default = method(policy, 13, ctypes.c_long, wt.LPCWSTR, wt.DWORD)
            for role, name in ROLES:
                check(set_default(policy, target, role), "SetDefaultEndpoint " + name)
        finally:
            method(policy, 2, wt.ULONG)(policy)
        print("After setting %s:" % ("CABLE Output" if target == CABLE_ID else "Mic Array"))
        for role, name in ROLES:
            print("  %-13s %s" % (name, endpoint_id(enum, role)))
        return 0
    finally:
        if enum:
            method(enum, 2, wt.ULONG)(enum)
        ole32.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main())
