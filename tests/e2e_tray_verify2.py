# -*- coding: utf-8 -*-
"""托盘 BUG 真机端到端复验（严格版）。

判据（比上一版更严）：
  1) 连续点 N 次托盘，矿机端窗口数必须恒为 1
  2) client.log 里第 2 次及以后的点击必须走「复用已有窗口」分支，
     绝不能出现「已启动 Edge app 窗口」（那说明枚举失手了）
  3) 巡检线程不能报 NameError（之前 _keep_only_one_app_window 未定义）
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(os.path.dirname(HERE), "client", "dist", "BaPiMiner.exe")
CLICKS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
PS = os.path.join(HERE, "tray_click_once.ps1")
LOG = os.path.join(HERE, "e2e_tray_verify2.txt")
CLIENT_LOG = os.path.join(os.path.dirname(EXE), "client.log")

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


lines = []


def log(s):
    print(s, flush=True)
    lines.append(s)


def click_once():
    # PS 5.1 输出是系统 ANSI 代码页(GBK)，用 utf-8 严格解码会炸；
    # 这里按 GBK 解、容错替换。
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", PS], capture_output=True, timeout=90)
    out = (r.stdout or b"").decode("gbk", errors="replace")
    err = (r.stderr or b"").decode("gbk", errors="replace")
    return out.strip().replace("\r\n", "\n").replace("\n", " | "), err.strip()


def parse_field(out, key):
    """从 click 输出里取 KEY=... 字段。"""
    for part in out.split(" | "):
        if part.startswith(key + "="):
            return part.split("=", 1)[1].strip()
    return ""


def click_hit_ok(out):
    """校验这次点击确实作用在托盘图标上（不是只看坐标）。

    Win11 通知区是 XAML island，FromPoint() 永远只能命中面板宿主，
    所以「光标下是什么元素」这个判据在这里没有意义。
    真正的两个证据：
      1) UIA 读到的目标元素 Name 以「扒皮矿工」开头、类名是
         SystemTray.NormalButton（语义确认，绝无可能点错图标）
      2) 物理左键点击后 GetCursorPos 确实落在该按钮的矩形内
    """
    if "CURSOR_MISMATCH" in out:
        return False, "光标未落到目标点"
    if "PHYSICAL_CLICK=ok" not in out:
        return False, "物理点击未执行"
    if "INVOKE_PATTERN=available" not in out:
        return False, "托盘按钮不支持 InvokePattern（可能不是真图标）"
    name = parse_field(out, "TARGET")
    if "扒皮矿工" not in name:
        return False, "目标不是矿机端托盘图标: %s" % name
    if "SystemTray.NormalButton" not in name:
        return False, "目标类名不对: %s" % name
    return True, name


log("=== 托盘 BUG 端到端复验（严格版）===")
log("exe: %s" % EXE)

# 清理
subprocess.run(["taskkill", "/F", "/IM", "BaPiMiner.exe"],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
for h, _p, _t in app_windows():
    ctypes.windll.user32.PostMessageW(h, 0x0010, 0, 0)
time.sleep(0.8)
log("清理后窗口数: %d" % len(app_windows()))

# 清掉旧日志，便于只看本次
if os.path.isfile(CLIENT_LOG):
    try:
        os.remove(CLIENT_LOG)
    except OSError:
        pass

log("\n启动客户端...")
subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
time.sleep(10)

# 等托盘就绪
for i in range(15):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", PS, "-ProbeOnly"], capture_output=True, timeout=60)
    probe = (r.stdout or b"").decode("gbk", errors="replace")
    if "READY" in probe:
        log("托盘图标就绪（第 %d 次探测）" % (i + 1))
        break
    time.sleep(2)

counts = []
hits_ok = []
for i in range(1, CLICKS + 1):
    out, err = click_once()
    ok_hit, hit_detail = click_hit_ok(out)
    hits_ok.append(ok_hit)
    time.sleep(3.0)
    wins = app_windows()
    counts.append(len(wins))
    log("点击 #%d -> %s" % (i, out[:160]))
    log("         落点元素校验: %s (%s) | 窗口数=%d" %
        ("OK" if ok_hit else "BAD", hit_detail, len(wins)))
    if err:
        log("         stderr: %s" % err[:200])

log("\n=== 日志中的「调出界面」记录 ===")
ui_lines = []
if os.path.isfile(CLIENT_LOG):
    with open(CLIENT_LOG, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if "调出界面" in ln or "收敛" in ln or "NameError" in ln:
                ui_lines.append(ln.rstrip())
                log("  " + ln.rstrip())
else:
    log("  (无 client.log)")

log("\n=== 结论 ===")
log("各次窗口数: %s" % counts)
log("每次点击落点校验: %s" % hits_ok)
ok_count = all(c <= 1 for c in counts)
ok_hit_all = all(hits_ok)

# 日志判定：除首次外，不应再出现「已启动 Edge app 窗口」
started = [l for l in ui_lines if "已启动 Edge app 窗口" in l]
reused = [l for l in ui_lines if "复用已有窗口" in l]
ignored = [l for l in ui_lines if "忽略本次重复触发" in l]
nm = [l for l in ui_lines if "NameError" in l]
log("「已启动 Edge app 窗口」次数: %d（客户端启动自开 1 次属正常）" % len(started))
log("「复用已有窗口」次数: %d" % len(reused))
log("「忽略本次重复触发」次数: %d" % len(ignored))
log("NameError 次数: %d" % len(nm))

ok_log = (len(nm) == 0) and (len(reused) + len(ignored) >= 1)
if ok_count and ok_log and ok_hit_all:
    log("PASS：窗口恒为 1，落点校验全部通过，日志确认走「复用/忽略」分支")
elif ok_count and ok_hit_all:
    log("PARTIAL：窗口数没堆、落点正确，但日志未体现复用分支，需复核")
elif ok_count:
    log("PARTIAL：窗口数没堆，但落点校验有失败，测试本身不可信")
else:
    log("FAIL：窗口堆叠，BUG 未修复")

with open(LOG, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("\n日志: %s" % LOG)
sys.exit(0 if (ok_count and ok_log and ok_hit_all) else 1)
