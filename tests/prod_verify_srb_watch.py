# -*- coding: utf-8 -*-
"""补充验证：切到 SRBMiner-MULTI 后观察 3 分钟，区分「启动慢」与「起不来」。"""
import json
import time
import urllib.request

TOK = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")
BASE = "http://116.169.217.29:8099"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"X-Admin-Token": TOK,
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())


def sinyebim():
    d = api("/api/fleet")
    for r in d.get("rigs") or []:
        if r.get("name") == "sinyebim":
            return r
    return {}


print("== 切到 SRBMiner-MULTI，观察 3 分钟 ==")
print(json.dumps(api("/api/command", "POST",
                     {"rig": "sinyebim", "cmd": "set_kernel",
                      "arg": {"kernel": "SRBMiner-MULTI"}}), ensure_ascii=False))
t0 = time.time()
while time.time() - t0 < 210:
    time.sleep(20)
    r = sinyebim()
    snap = r.get("snapshot") or {}
    m0 = (snap.get("miners") or [{}])[0]
    t = api("/api/fleet").get("total") or {}
    print("[%3ds] kernel=%s ver=%s | miner=%s alive=%s | fleet_ths=%s"
          % (int(time.time() - t0), snap.get("kernel"), snap.get("kernel_ver"),
             m0.get("type"), m0.get("alive"), t.get("ths")))
