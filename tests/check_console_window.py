"""检测客户端启动挖矿后是否出现可见的控制台窗口。

用法：python check_console_window.py [秒数]
原理：EnumWindows 枚举所有顶层窗口，只看 ConsoleWindowClass 且可见的窗口。
      打包成 --noconsole 的 GUI 进程若未正确设置 CREATE_NO_WINDOW，
      它拉起的 nvidia-smi / 挖矿内核会各自冒出一个控制台窗口。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

u = ctypes.windll.user32

u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
u.EnumWindows.restype = wt.BOOL
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetClassNameW.restype = ctypes.c_int
u.IsWindowVisible.argtypes = [wt.HWND]
u.IsWindowVisible.restype = wt.BOOL
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextW.restype = ctypes.c_int
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u.GetWindowThreadProcessId.restype = wt.DWORD


def consoles():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, buf, 256)
        cls = buf.value
        # 经典控制台窗口；Windows Terminal 接管时可能是 CASCADIA_HOSTING_WINDOW_CLASS
        if cls in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
            if u.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(512)
                u.GetWindowTextW(hwnd, title, 512)
                pid = wt.DWORD(0)
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append({"hwnd": hwnd, "cls": cls,
                              "title": title.value, "pid": pid.value})
        return True

    u.EnumWindows(cb, 0)
    return found


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    t0 = time.time()
    seen = {}
    base = {w["hwnd"] for w in consoles()}
    print("基线可见控制台窗口: %d 个" % len(base))
    bad = 0
    while time.time() - t0 < dur:
        for w in consoles():
            if w["hwnd"] not in base and w["hwnd"] not in seen:
                seen[w["hwnd"]] = w
                bad += 1
                print("  ⚠ 新增窗口 %.1fs  cls=%s  pid=%d  title=%r"
                      % (time.time() - t0, w["cls"], w["pid"], w["title"]))
        time.sleep(0.3)
    print("---")
    if bad:
        print("RESULT=FAIL 检测到 %d 个新增控制台窗口" % bad)
    else:
        print("RESULT=PASS %.0f 秒内未出现任何新的控制台窗口" % dur)


if __name__ == "__main__":
    main()
