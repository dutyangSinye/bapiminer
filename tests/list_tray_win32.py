# -*- coding: utf-8 -*-
"""Recursively enumerate every window under the taskbar and read the
notification-area toolbars, mapping each icon button to its owning PID.

Win11 hides the overflow panel behind an XAML flyout, so instead of
scraping that we walk the whole Shell_TrayWnd tree and read any
ToolbarWindow32 we find (visible tray + overflow window if present).
Each icon button's dwData points at the hidden window that owns the
icon, so GetWindowThreadProcessId gives us the process behind it.
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


def class_name(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def win_text(h):
    n = user32.GetWindowTextLengthW(h)
    if not n:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, b, n + 1)
    return b.value


def descendants(hwnd, out):
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _lp):
        out.append(h)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
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


def dump_toolbar(tb, label):
    count = user32.SendMessageW(tb, TB_BUTTONCOUNT, 0, 0)
    print("[%s] toolbar=%s buttons=%d" % (label, tb, count))
    if count <= 0:
        return
    for i in range(count):
        buf = ctypes.create_string_buffer(ctypes.sizeof(TBBUTTON) + 512)
        if not user32.SendMessageW(tb, TB_GETBUTTON, i, buf):
            continue
        b = ctypes.cast(buf, ctypes.POINTER(TBBUTTON)).contents
        hicon = b.dwData[0] if b.dwData else 0
        pid = wt.DWORD()
        if hicon:
            user32.GetWindowThreadProcessId(hicon, ctypes.byref(pid))
        p = pid.value
        print("   #%d id=%d ownerHwnd=%s pid=%s exe=%s"
              % (i, b.idCommand, hicon, p, img_of(p) if p else ""))


tops = []

@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(h, _lp):
    tops.append(h)
    return True

user32.EnumWindows(cb, 0)

for h in tops:
    cn = class_name(h)
    if cn in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow"):
        print("=== %s hwnd=%s title=%r ===" % (cn, h, win_text(h)))
        for c in descendants(h, []):
            if class_name(c) == "ToolbarWindow32":
                dump_toolbar(c, cn)
