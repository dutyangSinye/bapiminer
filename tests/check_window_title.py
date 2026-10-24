# -*- coding: utf-8 -*-
"""Check whether the Edge app window title matches UI_TITLE exactly.

The client finds an already-open UI window with GetWindowTextW and compares
it to UI_TITLE ("扒皮矿工·矿机端").  If the comparison fails, every tray click
spawns another Edge app window instead of restoring the existing one.
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32

TITLE = "扒皮矿工·矿机端"


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


hits = []


@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(h, _lp):
    c = cls(h)
    if c.startswith("Chrome_WidgetWin") or c.startswith("Chrome_Window"):
        t = txt(h)
        vis = user32.IsWindowVisible(h)
        hits.append((h, c, t, vis))
    return True


user32.EnumWindows(cb, 0)

exact = 0
for h, c, t, vis in hits:
    mark = ""
    if t.strip() == TITLE:
        mark = "  <== EXACT MATCH"
        exact += 1
    print("hwnd=%s class=%s visible=%s title=%r%s" % (h, c, vis, t, mark))

print()
print("windows total=%d  exact-match=%d" % (len(hits), exact))
print("RESULT:", "OK - tray click will restore the existing window"
      if exact else "BUG - title mismatch, every tray click opens a NEW Edge app window")
