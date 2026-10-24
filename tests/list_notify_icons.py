# -*- coding: utf-8 -*-
"""Enumerate Windows notification-area (system tray) icons.

For each icon button found in the visible tray and in the overflow
("hidden icons") panel we print the owning window handle, owning PID and
the tooltip text.  That tells us whether a tray icon belongs to a live
process or is a stale/"ghost" entry left behind by a process that was
killed without unregistering the icon.
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_USER = 0x0400
TB_GETBUTTON = WM_USER + 80
TB_BUTTONCOUNT = WM_USER + 24
TB_GETBUTTONTEXTW = WM_USER + 75


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


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def win_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def children(hwnd):
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _lp):
        out.append(h)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return out


def find_child_by_class(parent, cls):
    for h in children(parent):
        if class_name(h) == cls:
            return h
    return 0


def find_top_by_class(cls):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _lp):
        if class_name(h) == cls:
            found.append(h)
            return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def owner_pid(hwnd_icon):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd_icon, ctypes.byref(pid))
    return pid.value


def dump_toolbar(toolbar, label):
    if not toolbar:
        print("[%s] not found" % label)
        return
    count = user32.SendMessageW(toolbar, TB_BUTTONCOUNT, 0, 0)
    print("[%s] toolbar hwnd=%s buttons=%d" % (label, toolbar, count))
    for i in range(count):
        buf = ctypes.create_string_buffer(ctypes.sizeof(TBBUTTON) + 512)
        got = user32.SendMessageW(toolbar, TB_GETBUTTON, i, buf)
        if not got:
            continue
        tb = ctypes.cast(buf, ctypes.POINTER(TBBUTTON)).contents
        hicon = tb.dwData[0] if tb.dwData else 0
        pid = owner_pid(hicon) if hicon else 0
        # tooltip
        tlen = user32.SendMessageW(toolbar, TB_GETBUTTONTEXTW, tb.idCommand, 0)
        tip = ""
        if tlen > 0:
            tb2 = ctypes.create_unicode_buffer(tlen + 2)
            r = user32.SendMessageW(toolbar, TB_GETBUTTONTEXTW, tb.idCommand,
                                    ctypes.cast(tb2, ctypes.c_void_p).value)
            tip = tb2.value if r >= 0 else ""
        # owning pid image name
        img = ""
        if pid:
            h = kernel32.OpenProcess(0x1000, False, pid)
            if h:
                b = ctypes.create_unicode_buffer(1024)
                n = ctypes.c_uint(1024)
                if kernel32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
                    img = b.value
                kernel32.CloseHandle(h)
        print("   #%d id=%d iconHwnd=%s pid=%s tip=%r img=%s"
              % (i, tb.idCommand, hicon, pid, tip, img))


tray = find_top_by_class("Shell_TrayWnd")
print("Shell_TrayWnd =", tray)
if tray:
    notify = find_child_by_class(tray, "TrayNotifyWnd")
    print("TrayNotifyWnd =", notify)
    if notify:
        pager = find_child_by_class(notify, "SysPager")
        print("SysPager =", pager)
        if pager:
            tb = find_child_by_class(pager, "ToolbarWindow32")
            dump_toolbar(tb, "visible tray")

overflow = find_top_by_class("NotifyIconOverflowWindow")
print("NotifyIconOverflowWindow =", overflow)
if overflow:
    tb = find_child_by_class(overflow, "ToolbarWindow32")
    dump_toolbar(tb, "overflow")
