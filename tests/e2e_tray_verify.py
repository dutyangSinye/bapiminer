# -*- coding: utf-8 -*-
"""托盘多窗口 BUG 的真机端到端验证。

流程：
  1) 杀掉所有 BaPiMiner 进程（以及残留的矿机端 Edge app 窗口）
  2) 用 dist/BaPiMiner.exe 启动客户端，等托盘就绪
  3) 用 UIA 物理点击托盘图标 N 次（默认 5）
  4) 每次点击后统计「扒皮矿工·矿机端」窗口数
  5) 判定：窗口数必须恒为 1（最多允许短暂 +1 后被收敛）

托盘点击用 PowerShell（UIA + 物理鼠标），窗口计数用本脚本的 Win32 枚举。
之所以物理点击：托盘溢出面板的按钮不支持 UIA InvokePattern，只能真鼠标点。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HERE), "client", "dist", "BaPiMiner.exe")
CLICKS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
PS = os.path.join(HERE, "tray_click_once.ps1")
LOG = os.path.join(HERE, "e2e_tray_verify.txt")

ALT = ("扒皮矿工", "矿机端")


def win_text(h):
    n = ctypes.windll.user32.GetWindowTextLengthW(h)
    if not n:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    ctypes.windll.user32.GetWindowTextW(h, b, n + 1)
    return b.value


def app_windows():
    u = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        if u.IsWindowVisible(h):
            t = win_text(h)
            if t and all(k in t for k in ALT):
                pid = wt.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                out.append((h, pid.value, t))
        return True

    u.EnumWindows(cb, 0)
    return out


def kill_clients():
    subprocess.run(["taskkill", "/F", "/IM", "BaPiMiner.exe"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)


def close_app_windows():
    for h, _p, _t in app_windows():
        ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)
    time.sleep(0.8)


def click_tray():
    """调 PowerShell 做一次物理点击，返回是否找到并点了。"""
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", PS], capture_output=True, text=True, timeout=60)
        out = (r.stdout or "").strip()
        return out
    except Exception as e:
        return "ERR %r" % e


lines = []


def log(s):
    print(s, flush=True)
    lines.append(s)


log("=== 端到端验证：托盘点击是否产生多个矿机端窗口 ===")
log("exe: %s" % EXE)
log("存在: %s" % os.path.isfile(EXE))

kill_clients()
close_app_windows()
log("\n清理后窗口数: %d" % len(app_windows()))

log("\n启动客户端...")
p = subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
time.sleep(9)
log("客户端进程 pid=%s" % p.pid)

# 等托盘图标出现（UIA 能找到按钮）
log("等待托盘图标就绪...")
ready = False
for i in range(12):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", PS, "-ProbeOnly"], capture_output=True, text=True, timeout=60)
    out = (r.stdout or "").strip()
    if "READY" in out:
        ready = True
        log("  托盘图标就绪 (第 %d 次探测)" % (i + 1))
        break
    time.sleep(2)
if not ready:
    log("!! 托盘图标未就绪，无法继续")

counts = []
for i in range(1, CLICKS + 1):
    log("\n=== 点击 #%d ===" % i)
    r = click_tray()
    log("  点击脚本: %s" % r.replace("\n", " | ")[:200])
    time.sleep(2.5)
    wins = app_windows()
    counts.append(len(wins))
    log("  矿机端窗口数 = %d" % len(wins))
    for h, pid, t in wins:
        log("     hwnd=%d pid=%d title=%r" % (h, pid, t))

log("\n=== 结论 ===")
log("各次窗口数: %s" % counts)
ok = all(c <= 1 for c in counts)
if ok:
    log("PASS：窗口数恒 <= 1，托盘重复点击不再堆窗口")
else:
    log("FAIL：出现多个窗口，BUG 未修复")
log("最终窗口数: %d" % len(app_windows()))

with open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("\n日志已写入: %s" % LOG)
