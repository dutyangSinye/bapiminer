# -*- coding: utf-8 -*-
"""给本机预览实例灌 3 台演示矿机 + 曲线样本，仅用于看板视觉验收。"""
import json
import os
import sqlite3
import time
import urllib.request

BASE = os.environ.get("SEED_BASE", "http://127.0.0.1:18099")
DB = os.environ.get("SEED_DB", r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server\data\fleet.db")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

RIGS = [
    ("DEMO-5080", "RTX 5080", 230.4, 389, 74, 24000, 380.0),
    ("DEMO-4090", "RTX 4090", 324.0, 448, 79, 21000, 520.0),
    ("DEMO-4070", "RTX 4070", 148.2, 168, 66, 3000, 210.0),
]

for name, model, ths, power, temp, mem, income in RIGS:
    tok = "demo-" + name.lower()
    payload = {
        "name": name, "token": tok, "hostname": name.lower(), "ts": time.time(),
        "game_mode": False,
        "gpus": [{"index": 0, "model": "NVIDIA GeForce " + model, "temp": temp,
                  "fan": 62, "power": power, "util": 98, "mem_used": mem, "mem_total": 16303}],
        "miners": [{"type": "srbminer", "alive": True, "ths": ths, "accepted": 827,
                    "rejected": 2, "uptime": 96600, "algo": "pearlhash",
                    "pool": "stratum+tcp://prl.kryptex.network:7048"}],
    }
    req = urllib.request.Request(BASE + "/api/report", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-Rig-Token": tok})
    with opener.open(req, timeout=10) as r:
        print(name, r.status)

# 曲线：过去 3 小时，每 5 分钟一个样本
c = sqlite3.connect(DB)
now = time.time()
for name, model, ths, power, temp, mem, income in RIGS:
    for i in range(36, 0, -1):
        ts = now - i * 300
        wobble = 1 + (0.06 * ((i * 7) % 5 - 2) / 2.0)
        c.execute("INSERT INTO samples(rig,ts,ths,power,temp,fan,accepted,rejected,uptime,pool)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (name, ts, round(ths * wobble, 1), power, temp, 62, 800 - i * 3, 2,
                   96000 + (36 - i) * 300, "prl.kryptex.network:7048"))
c.execute("INSERT INTO alerts(ts,rig,level,kind,msg,resolved) VALUES(?,?,?,?,?,?)",
          (now - 600, "DEMO-4090", "warn", "hot", "GPU 温度 79°C 接近阈值", 0))
c.execute("INSERT INTO alerts(ts,rig,level,kind,msg,resolved) VALUES(?,?,?,?,?,?)",
          (now - 3600, "DEMO-4070", "info", "gamemode", "🎮 游戏模式：检测到游戏进程 dota2.exe，已暂停挖矿让出显卡", 1))
c.commit()
print("samples", c.execute("SELECT count(*) FROM samples").fetchone()[0])
c.close()
