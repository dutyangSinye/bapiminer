"""证明自更新脚本的窗口问题：DETACHED_PROCESS 会弹窗，CREATE_NO_WINDOW 不会。

背景：pythonw / --noconsole 的 GUI 进程自己没有控制台。用 DETACHED_PROCESS 拉起
cmd.exe 时，cmd 也没有控制台；bat 里的外部命令（tasklist / find / timeout）拿不到
可继承的控制台，Windows 就给每个都新建一个「可见」窗口。
CREATE_NO_WINDOW 则给 cmd 一个隐藏控制台，外部命令继承它，全程零窗口。

用法（必须用 pythonw 跑，与打包后的 GUI 进程等价）：
    pythonw.exe test_update_bat_silent.py
结果写到同目录 update_bat_result.txt。
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "update_bat_result.txt")

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
        if buf.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS") \
                and u.IsWindowVisible(hwnd):
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


BAT = os.path.join(HERE, "_probe_update.bat")
with open(BAT, "w", encoding="ascii", newline="\r\n") as f:
    # 与 _updater_bat 的等待段同构：tasklist | find + timeout，全是外部控制台命令
    f.write("@echo off\r\n"
            'tasklist /FI "PID eq 999999" 2>nul | find /I "BaPiMiner" >nul\r\n'
            "timeout /t 1 /nobreak >nul\r\n")

DETACHED = 0x00000008
CNW = 0x08000000
SI = subprocess.STARTUPINFO()
SI.dwFlags |= subprocess.STARTF_USESHOWWINDOW
SI.wShowWindow = 0


def launch(flags):
    try:
        p = subprocess.Popen(["cmd.exe", "/c", BAT], cwd=HERE,
                             startupinfo=SI, creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL)
        p.wait(timeout=20)
    except Exception:  # noqa: BLE001
        pass


def main():
    log = []
    log.append("pythonw 进程: %s" % sys.executable)
    log.append("是否无控制台: %s" % (ctypes.windll.kernel32.GetConsoleWindow() == 0))
    log.append("基线可见控制台窗口: %d" % len(consoles()))

    th = threading.Thread(target=watcher, daemon=True)
    th.start()

    for phase, flags in (("DETACHED_PROCESS", DETACHED), ("CREATE_NO_WINDOW", CNW)):
        time.sleep(1.0)
        PHASE[0] = phase
        n0 = len(HITS)
        for _ in range(3):
            launch(flags)
            time.sleep(0.8)
        hits = len(HITS) - n0
        log.append("%s 3 次 -> 新增可见控制台窗口 %d 个" % (phase, hits))

    STOP.set()
    time.sleep(0.3)
    log.append("--- 明细 ---")
    for ph, pid, title in HITS[:20]:
        log.append("  [%s] pid=%d title=%r" % (ph, pid, title))
    log.append("---")
    det = sum(1 for ph, _p, _t in HITS if ph == "DETACHED_PROCESS")
    cnw = sum(1 for ph, _p, _t in HITS if ph == "CREATE_NO_WINDOW")
    if det > 0 and cnw == 0:
        log.append("RESULT=PASS  DETACHED 弹窗 %d 次，CREATE_NO_WINDOW 零窗口" % det)
    elif det == 0 and cnw == 0:
        log.append("RESULT=INCONCLUSIVE  两种写法都没弹窗")
    else:
        log.append("RESULT=FAIL  det=%d cnw=%d" % (det, cnw))
    try:
        os.remove(BAT)
    except OSError:
        pass
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
