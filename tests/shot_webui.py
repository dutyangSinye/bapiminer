# -*- coding: utf-8 -*-
"""截取 Edge app 模式窗口（按标题模糊匹配，兼容 msedge 窗口类）。"""
import ctypes
import ctypes.wintypes as wt
import sys

from PIL import Image

TITLE = "扒皮矿工·矿机端"
user32 = ctypes.windll.user32
gdi = ctypes.windll.gdi32
user32.SetProcessDPIAware()

found = []

@ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(hwnd, _lp):
    n = user32.GetWindowTextLengthW(hwnd)
    if n:
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if TITLE in buf.value:
            found.append((hwnd, buf.value))
    return True

user32.EnumWindows(cb, 0)
print('candidates:', [(str(h), t[:30]) for h, t in found])
if not found:
    sys.exit(1)
h, title = found[0]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
rc = wt.RECT()
user32.GetWindowRect(h, ctypes.byref(rc))
w, hh = rc.right - rc.left, rc.bottom - rc.top
if w < 100 or hh < 100:
    print('window too small (minimized?):', w, hh)
    sys.exit(1)
hdc = user32.GetWindowDC(h)
mem = gdi.CreateCompatibleDC(hdc)
bmp = gdi.CreateCompatibleBitmap(hdc, w, hh)
gdi.SelectObject(mem, bmp)
user32.PrintWindow(h, mem, 2)

class BMI(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD),
                ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                ('biXPelsPerMeter', wt.LONG), ('biYPelsPerMeter', wt.LONG),
                ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]

bi = BMI(ctypes.sizeof(BMI), w, -hh, 1, 32, 0, 0, 0, 0, 0, 0)
buf = ctypes.create_string_buffer(w * hh * 4)
gdi.GetDIBits(mem, bmp, 0, hh, buf, ctypes.byref(bi), 0)
im = Image.frombuffer('RGBA', (w, hh), buf.raw, 'raw', 'BGRA', 0, 1).convert('RGB')
out = r'C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\tests\shots\webui_live.png'
im.save(out)
print('saved', out, w, hh, title)
