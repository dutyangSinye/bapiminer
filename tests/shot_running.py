"""截取已在运行的 扒皮矿工 客户端窗口（不启动新进程）。"""
import ctypes
import ctypes.wintypes as wt
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
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]

TITLE = "扒皮矿工·矿机端"


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
    ok = user32.PrintWindow(hwnd, mem, 2)
    bi = BMI(ctypes.sizeof(BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB").save(path)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return ok, (w, h)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shots/running.png"
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        print("WINDOW_NOT_FOUND（可能收进托盘了）")
        return 2
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetWindowPos(hwnd, wt.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2)
    time.sleep(1.0)
    ok, size = grab(hwnd, out)
    user32.SetWindowPos(hwnd, wt.HWND(0), 0, 0, 0, 0, 0x1 | 0x2)
    print("PRINTWINDOW %s size=%s -> %s" % ("OK" if ok else "FAIL", size, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
