# -*- coding: utf-8 -*-
"""长期观察客户端存活与上报：每 10 秒采样，共 3 分钟。"""
import os
import subprocess
import time

EXE = r"D:\mine\BaPiMiner.exe"


def pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq BaPiMiner.exe", "/NH"],
                         capture_output=True, text=True, errors="replace").stdout
    ids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower().startswith("bapiminer"):
            ids.append(parts[1])
    return ids


print("start:", EXE)
subprocess.Popen([EXE], cwd=r"D:\mine")
t0 = time.time()
for _ in range(18):
    time.sleep(10)
    ids = pids()
    print("T+%-4d procs=%d %s" % (int(time.time() - t0), len(ids), ",".join(ids)), flush=True)
print("final procs:", pids())
