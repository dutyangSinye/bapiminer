# -*- coding: utf-8 -*-
"""模拟矿机：联调用。python sim_agent.py [--hot] [--drop]
--hot   温度顶到 88C 触发高温告警
--drop  算力骤降 60% 触发降算力告警
"""

import json
import random
import sys
import time
import urllib.request

SERVER = "http://127.0.0.1:8099"
NAME, TOKEN = "SIM-4090", "test-token-1"
START = time.time()
hot = "--hot" in sys.argv
drop = "--drop" in sys.argv


def post(path, payload, headers=None):
    req = urllib.request.Request(SERVER + path, method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def get(path, headers=None):
    req = urllib.request.Request(SERVER + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


n = 0
while True:
    n += 1
    drift = 1 + random.uniform(-0.04, 0.04)
    if drop and n >= 6:
        drift = 0.4
    ths = 324.0 * drift
    temp = 88.0 if hot else (74 + random.uniform(-2, 3))
    pwr = 447.8 * drift
    payload = {
        "name": NAME, "token": TOKEN, "hostname": "SIM-HOST", "ts": time.time(),
        "gpus": [
            {"index": 0, "model": "NVIDIA GeForce RTX 4090", "temp": temp,
             "util": 99, "mem_used": 21000, "mem_total": 24564,
             "fan": 85, "power": pwr * 0.52},
            {"index": 1, "model": "NVIDIA GeForce RTX 4090", "temp": temp - 3,
             "util": 99, "mem_used": 20500, "mem_total": 24564,
             "fan": 83, "power": pwr * 0.48},
        ],
        "miners": [{
            "type": "srbminer", "alive": True, "ths": ths,
            "accepted": 1200 + n * 7, "rejected": n // 12,
            "uptime": int(time.time() - START),
            "algo": "pearlhash", "pool": "stratum+tcp://prl.kryptex.network:7048",
            "raw": "{\"demo\": true}",
        }],
    }
    try:
        print("report:", post("/api/report", payload))
    except Exception as e:  # noqa: BLE001
        print("report fail:", e)

    # 轮询命令并模拟执行
    try:
        r = get("/api/poll?name=%s" % NAME, headers={"X-Rig-Token": TOKEN})
        for c in r.get("commands") or []:
            print("got cmd:", c["id"], c["cmd"], "-> done")
            post("/api/cmd_result", {"rig": NAME, "id": c["id"], "ok": True,
                                     "result": "模拟：已重启 SRBMiner-MULTI"},
                 headers={"X-Rig-Token": TOKEN})
    except Exception as e:  # noqa: BLE001
        print("poll fail:", e)

    time.sleep(5)
