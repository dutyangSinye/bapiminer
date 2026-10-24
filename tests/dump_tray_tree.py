# -*- coding: utf-8 -*-
"""Dump the class/title tree under Shell_TrayWnd so we can locate the
Win11 notification-area toolbar, then map each icon button to its owner PID.
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_USER = 0x0400
TB_GETBUTTON = WM_USER + 80
TB_BUTTONCOUNT = WM_USER + 24


class TBBUTTON(ctypes.Structure):
    _fields_ = [
        ("iBitmap", ctypes.c_int),
        ("idCommand", ctypes.c_int),
        ("fsState", ctypes.c_ubyte),
        ("fsStyle", ctypes.c_ubyte),
        ("bReserved", ctypes.c_ubyte * 6),
        ("dwData", ctypes.POINTER(ctypes.c_ulonglong)),
        ("iString", ctypes.c_void_p),
    ]


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def txt(h):
    n = user32.GetWindowTextLengthW(h)
    if not n:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def kids(h):
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(c, _lp):
        out.append(c)
        return True

    user32.EnumChildWindows(h, cb, 0)
    return out


def img_of(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        b = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_uint(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
            return b.value
        return ""
    finally:
        kernel32.CloseHandle(h)


tops = []

@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(h, _lp):
    tops.append(h)
    return True

user32.EnumWindows(cb, 0)

for h in tops:
    if cls(h) != "Shell_TrayWnd":
        continue
    print("Shell_TrayWnd =", h)
    for c in kids(h):
        print("  child class=%-28s hwnd=%-10s title=%r" % (cls(c), c, txt(c)))
        for c2 in kids(c):
            print("    sub class=%-24s hwnd=%-10s title=%r" % (cls(c2), c2, txt(c2)))
            for c3 in kids(c2):
                print("      sub2 class=%-22s hwnd=%-10s title=%r" % (cls(c3), c3, txt(c3)))
                if cls(c3) == "ToolbarWindow32":
                    n = user32.SendMessageW(c3, TB_BUTTONCOUNT, 0, 0)
                    print("        -> toolbar buttons=%d" % n)
                    for i in range(max(0, n)):
                        buf = ctypes.create_string_buffer(ctypes.sizeof(TBBUTTON) + 512)
                        if not user32.SendMessageW(c3, TB_GETBUTTON, i, buf):
                            continue
                        b = ctypes.cast(buf, ctypes.POINTER(TBBUTTON)).contents
                        hi = b.dwData[0] if b.dwData else 0
                        pid = wt.DWORD()
                        if hi:
                            user32.GetWindowThreadProcessId(hi, ctypes.byref(pid))
                        p = pid.value
                        print("           #%d id=%d owner=%s pid=%s exe=%s"
                              % (i, b.idCommand, hi, p, img_of(p) if p else ""))
