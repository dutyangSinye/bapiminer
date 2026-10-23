"""端到端探测：启动客户端，按时间线记录进程/内核/控制台窗口/日志。

用法：python probe_client.py [秒数] [exe路径]
判定：整个过程出现任何可见控制台窗口即 FAIL；内核成功拉起且客户端存活即 PASS。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_console_window import consoles  # noqa: E402

EXE = sys.argv[2] if len(sys.argv) > 2 else r"D:\mine\BaPiMiner.exe"
WD = os.path.dirname(EXE)
LOG = os.path.join(WD, "client.log")
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0


def procs(name):
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + name, "/NH"],
                         capture_output=True, timeout=15).stdout.decode("gbk", "replace")
    return out.lower().count(name.lower())


def logsize():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return -1


def main():
    # 清理旧实例
    for n in ("BaPiMiner.exe", "SRBMiner-MULTI.exe"):
        subprocess.run(["taskkill", "/IM", n, "/F"], capture_output=True, timeout=15)
    time.sleep(2)

    base = {w["hwnd"] for w in consoles()}
    start_log = logsize()
    print("基线: 控制台窗口 %d 个, 日志 %d 字节" % (len(base), start_log))
    print("启动: %s" % EXE)
    subprocess.Popen([EXE], cwd=WD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    t0 = time.time()
    new_win = 0
    saw_kernel = False
    last_report = 0.0
    while time.time() - t0 < DUR:
        el = time.time() - t0
        n_cli = procs("BaPiMiner.exe")
        n_krn = procs("SRBMiner-MULTI.exe")
        if n_krn:
            saw_kernel = True
        for w in consoles():
            if w["hwnd"] not in base:
                base.add(w["hwnd"])
                new_win += 1
                print("  [%.0fs] ⚠ 新增控制台窗口 pid=%d title=%r" % (el, w["pid"], w["title"]))
        if el - last_report >= 15:
            last_report = el
            print("  [%3.0fs] 客户端 %d 进程 | 内核 %d 进程 | 控制台窗口 +%d"
                  % (el, n_cli, n_krn, new_win))
        time.sleep(1.0)

    print("--- 日志尾部 ---")
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(max(0, start_log - 200))
            print(f.read()[-1200:])
    except OSError as e:
        print("读取失败", e)

    n_cli = procs("BaPiMiner.exe")
    print("---")
    print("客户端存活: %d 进程 | 内核曾拉起: %s | 新增控制台窗口: %d"
          % (n_cli, saw_kernel, new_win))
    if new_win:
        print("RESULT=FAIL 出现控制台窗口")
    elif not n_cli:
        print("RESULT=WARN 无窗口但客户端已退出")
    elif not saw_kernel:
        print("RESULT=WARN 无窗口但内核未拉起（检查挖矿配置）")
    else:
        print("RESULT=PASS 静默运行：无控制台窗口，客户端与内核均正常")


if __name__ == "__main__":
    main()
