"""证明静默启动有效：在无控制台的 pythonw 下对比旧写法与新写法。

pythonw.exe 没有控制台，与 PyInstaller --noconsole 打出来的 GUI 进程等价：
此时任何控制台子程序若不带 CREATE_NO_WINDOW，系统都会给它配一个可见窗口。

用法（必须用 pythonw 跑）：
    pythonw.exe test_silent_launch.py
结果写到同目录 silent_result.txt。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "silent_result.txt")

u = ctypes.windll.user32
u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

HITS = []        # (阶段, pid, title)
PHASE = ["boot"]
STOP = threading.Event()


def consoles():
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, buf, 256)
        if buf.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS") and u.IsWindowVisible(hwnd):
            t = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(hwnd, t, 512)
            pid = wt.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append((hwnd, pid.value, t.value))
        return True

    u.EnumWindows(cb, 0)
    return out


def watcher():
    known = {h for h, _p, _t in consoles()}
    while not STOP.is_set():
        for h, pid, title in consoles():
            if h not in known:
                known.add(h)
                HITS.append((PHASE[0], pid, title))
        time.sleep(0.04)


CNW = 0x08000000
CMD = ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]


def old_style():
    """原来的写法：无 creationflags、无 startupinfo。"""
    try:
        subprocess.run(CMD, capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001
        pass


def new_style():
    """run_hidden 的写法。"""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        subprocess.run(CMD, capture_output=True, timeout=10,
                       startupinfo=si, creationflags=CNW)
    except Exception:  # noqa: BLE001
        pass


def main():
    log = []

    def say(s):
        log.append(s)

    say("pythonw 进程: %s" % sys.executable)
    say("是否无控制台: %s" % (ctypes.windll.kernel32.GetConsoleWindow() == 0))
    say("基线可见控制台窗口: %d" % len(consoles()))

    th = threading.Thread(target=watcher, daemon=True)
    th.start()

    PHASE[0] = "旧写法"
    n0 = len(HITS)
    for _ in range(5):
        old_style()
        time.sleep(1.2)
    old_hits = len(HITS) - n0
    say("旧写法 5 次调用 -> 新增控制台窗口 %d 个" % old_hits)

    time.sleep(1.0)
    PHASE[0] = "新写法"
    n1 = len(HITS)
    for _ in range(5):
        new_style()
        time.sleep(1.2)
    new_hits = len(HITS) - n1
    say("新写法 5 次调用 -> 新增控制台窗口 %d 个" % new_hits)

    STOP.set()
    time.sleep(0.3)
    say("--- 明细 ---")
    for ph, pid, title in HITS[:20]:
        say("  [%s] pid=%d title=%r" % (ph, pid, title))
    say("---")
    if old_hits > 0 and new_hits == 0:
        say("RESULT=PASS 旧写法会弹窗（%d 次），静默写法零窗口" % old_hits)
    elif old_hits == 0 and new_hits == 0:
        say("RESULT=INCONCLUSIVE 两种写法都没弹窗（本机可能禁用了控制台窗口）")
    else:
        say("RESULT=FAIL 新写法仍然弹出了控制台窗口")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
