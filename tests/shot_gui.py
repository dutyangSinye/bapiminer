"""截取 扒皮矿工 GUI 整窗（PrintWindow），用于 UI 走查。

用法: <venv>/python tests/shot_gui.py <out.png> [page]
    page: mine|game|fleet|sys  （通过 MINRIGS_PAGE 环境变量传给客户端）
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

from PIL import Image

user32 = ctypes.windll.user32
gdi = ctypes.windll.gdi32
user32.SetProcessDPIAware()
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.GetWindowDC.restype = wt.HDC
user32.GetWindowDC.argtypes = [wt.HWND]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]

# GDI 句柄是 64 位指针，不声明 argtypes 会按 c_int 传参直接溢出
gdi.CreateCompatibleDC.restype = wt.HDC
gdi.CreateCompatibleDC.argtypes = [wt.HDC]
gdi.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi.SelectObject.restype = wt.HGDIOBJ
gdi.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi.GetDIBits.restype = ctypes.c_int
gdi.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                          ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi.DeleteDC.argtypes = [wt.HDC]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]

TITLE = "扒皮矿工·矿机端"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "client")


class BMI(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(hwnd, path):
    rc = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    bi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    im = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
    im.save(path)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return ok, (w, h)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shots/gui.png"
    page = sys.argv[2] if len(sys.argv) > 2 else "mine"
    env = dict(os.environ, MINRIGS_PAGE=page, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.join(APP_DIR, "miner_client.py")],
                            cwd=APP_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    hwnd = None
    for _ in range(40):
        time.sleep(0.5)
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            break
    if not hwnd:
        proc.kill()
        print("WINDOW_NOT_FOUND")
        return 2
    time.sleep(6)  # 等首帧渲染 + 收益口径/钱包余额从中控同步
    user32.SetWindowPos(hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2)  # TOPMOST
    time.sleep(0.8)
    ok, size = grab(hwnd, out)
    print("PRINTWINDOW %s size=%s ok=%s -> %s" % ("OK" if ok else "FAIL", size, ok, out))
    user32.SetWindowPos(hwnd, wt.HWND(0), 0, 0, 0, 0, 0x1 | 0x2)   # 取消置顶
    proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
