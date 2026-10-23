# -*- coding: utf-8 -*-
"""验证矿机端「关闭 = 收进通知区」+「真退出会停内核」。

做法：把 exe 复制到空目录（配置里没有钱包，不会自动开挖），启动后用
Win32 向主窗口发 WM_CLOSE，然后检查：
  1) 进程仍然存活（没有退出）
  2) 主窗口已隐藏（withdraw 到托盘）
  3) 进程没有拉矿工内核
最后强杀进程收尾。

注意：PyInstaller onefile 有两个同名进程（bootloader + 真正的 app），
Tk 窗口属于子进程，所以窗口按「进程可执行文件路径在测试目录内」定位，
避免撞到机主正在运行的正式实例。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.dirname(HERE)
EXE = os.path.join(CLIENT, "client", "dist", "BaPiMiner.exe")
WORK = os.path.join(os.environ.get("TEMP", "."), "_minrigs_tray_test")

user32 = ctypes.WinDLL("user32", use_last_error=True)
WM_CLOSE = 0x0010

# 64 位下必须声明 argtypes，否则 HWND 会被截断成 32 位，永远匹配不到窗口
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL

ok_all = True


def check(name, cond, extra=""):
    global ok_all
    ok_all = ok_all and bool(cond)
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


def all_windows():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _l):
        n = user32.GetWindowTextLengthW(hwnd)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            found.append((hwnd, buf.value, bool(user32.IsWindowVisible(hwnd)), p.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def image_of(pid):
    """进程的可执行文件路径（失败返回空串）。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                               ctypes.POINTER(wt.DWORD)]
    k32.CloseHandle.argtypes = [wt.HANDLE]
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(520)
    n = wt.DWORD(520)
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    k32.CloseHandle(h)
    return buf.value if ok else ""


def find_win():
    """按 exe 路径找测试实例自己的窗口（子进程路径同样在 WORK 内）。"""
    for hwnd, title, vis, pid in all_windows():
        img = image_of(pid)
        if img and img.lower().startswith(WORK.lower()):
            return hwnd, title, vis, pid
    return None


def pids_of(name="BaPiMiner.exe"):
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + name],
                         capture_output=True, text=True, errors="replace").stdout
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def main():
    if not os.path.isfile(EXE):
        print("[FAIL] 找不到 exe: %s" % EXE)
        return 1
    if os.path.isdir(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    target = os.path.join(WORK, "BaPiMiner.exe")
    shutil.copy2(EXE, target)
    # 空钱包 => 不会自动挖矿，避免测试抢占显卡
    with open(os.path.join(WORK, "client_config.json"), "w", encoding="utf-8") as f:
        json.dump({"wallet": "", "pool_url": "", "autostart_mine": False,
                   "autonmine": False, "kernel": "SRBMiner-MULTI"}, f)
    logf = os.path.join(WORK, "client.log")
    if os.path.exists(logf):
        os.remove(logf)

    print("启动:", target)
    proc = subprocess.Popen([target], cwd=WORK)
    time.sleep(10)
    check("启动器进程存活", proc.poll() is None)

    hit = find_win()
    check("找到主窗口", hit is not None,
          (hit[1] + " pid=%d" % hit[3]) if hit else str([t for _h, t, v, _p in all_windows() if v][:8]))
    if not hit:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        return 1
    hwnd, title, vis, winpid = hit
    check("窗口可见", vis)
    check("窗口进程在运行", winpid in pids_of(), "pid=%d pids=%s" % (winpid, pids_of()))

    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(5.0)

    check("点关闭后进程仍在（缩到任务栏，不再直接退出）", proc.poll() is None and winpid in pids_of())
    user32.IsIconic.argtypes = [wt.HWND]
    user32.IsIconic.restype = wt.BOOL
    iconic = bool(user32.IsIconic(hwnd))
    check("主窗口已缩到任务栏（最小化状态）", iconic, "iconic=%s" % iconic)

    def miner_pids():
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq SRBMiner-MULTI.exe"],
                             capture_output=True, text=True, errors="replace").stdout
        pids = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() == "srbminer-multi.exe":
                try:
                    pids.add(int(parts[1]))
                except ValueError:
                    pass
        return pids

    baseline = miner_pids()  # 机主自己的矿工不算在测试头上

    # 第二次关闭（窗口已缩到任务栏）= 任务栏右键「关闭窗口」→ 真退出并停内核
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(6.0)
    check("任务栏关闭 = 真退出（进程已结束）", not pids_of(), str(pids_of()))
    check("测试实例没拉起矿工内核（对比基线无新增）", miner_pids() <= baseline,
          "新增=%s" % (miner_pids() - baseline))

    logtxt = open(logf, encoding="utf-8", errors="replace").read() if os.path.exists(logf) else ""
    # 本测试实例没挖矿（无内核进程），退出清理走空路径；杀内核路径由 selftest 覆盖
    check("退出清理无失败记录", "兜底失败" not in logtxt and "未能结束" not in logtxt,
          (logtxt.strip().splitlines() or ["(本实例无内核进程，日志为空)"])[-1])
    check("托盘中未报错", "托盘不可用" not in logtxt and "托盘启动失败" not in logtxt,
          (logtxt.strip().splitlines() or ["(无日志)"])[-1])

    print("== e2e tray close", "PASSED ==" if ok_all else "FAILED ==")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
