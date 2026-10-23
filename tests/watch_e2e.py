# -*- coding: utf-8 -*-
"""观察 D:\\mine 客户端的自动升级事件序列：进程、exe 指纹、替换脚本、日志增量。"""
import hashlib
import os
import subprocess
import sys
import time

D = r"D:\mine"
EXE = os.path.join(D, "BaPiMiner.exe")


def sha(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]
    except OSError:
        return "-"


def marks():
    return {
        "new": os.path.exists(os.path.join(D, "BaPiMiner.exe.new")),
        "bat": os.path.exists(os.path.join(D, "_update_client.bat")),
        "bak": os.path.exists(os.path.join(D, "BaPiMiner.exe.bak")),
    }


def logtail(n=2):
    try:
        with open(os.path.join(D, "client.log"), encoding="utf-8", errors="replace") as f:
            return f.read().strip().splitlines()[-n:]
    except OSError:
        return []


p = subprocess.Popen([EXE], cwd=D)
print("launched pid=%s sha0=%s" % (p.pid, sha(EXE)))
seen_sha = sha(EXE)
for i in range(100):
    time.sleep(2)
    rc = p.poll()
    m = marks()
    cur = sha(EXE)
    changed = cur != seen_sha
    if changed:
        seen_sha = cur
    if rc is not None or changed or m["new"] or m["bat"]:
        print("T+%03ds rc=%s sha=%s new=%s bat=%s bak=%s"
              % (i * 2, rc, cur, m["new"], m["bat"], m["bak"]))
        for ln in logtail():
            print("   |", ln[-110:])
    if rc is not None and m["bat"]:
        print(">>> 主进程已退出且替换脚本在场，等待替换+重启…")
        time.sleep(12)
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq BaPiMiner.exe"],
                           capture_output=True, text=True)
        print("tasklist:", (r.stdout or "").strip().splitlines()[-1:])
        print("final sha:", sha(EXE))
        break
    if rc is not None and not m["bat"]:
        print(">>> 进程退出但没有替换脚本（升级未触发），T+%ds" % (i * 2))
        for ln in logtail(4):
            print("   |", ln[-110:])
        break
else:
    print("100s 内无事件；final sha:", sha(EXE))
