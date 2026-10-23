# -*- coding: utf-8 -*-
"""观察 D:\mine\BaPiMiner.exe 自更新全过程：进程数 + 目录文件快照，每 4 秒一采样。"""
import os
import subprocess
import time

EXE = r"D:\mine\BaPiMiner.exe"
DIR_ = r"D:\mine"


def procs():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq BaPiMiner.exe", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    return len([l for l in out.splitlines() if "BaPiMiner" in l])


def snap():
    files = []
    for f in sorted(os.listdir(DIR_)):
        if f.lower().startswith("bapiminer") or f.startswith("_update"):
            p = os.path.join(DIR_, f)
            files.append("%s(%d)" % (f, os.path.getsize(p)))
    return " ".join(files)


print("T+0  start")
subprocess.Popen([EXE], cwd=DIR_)
t0 = time.time()
for i in range(1, 24):
    time.sleep(4)
    el = int(time.time() - t0)
    print("T+%-3d procs=%d  %s" % (el, procs(), snap()), flush=True)
