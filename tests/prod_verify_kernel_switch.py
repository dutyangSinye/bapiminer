# -*- coding: utf-8 -*-
"""生产功能验证：set_kernel 真实切换（WildRig -> SRBMiner-MULTI -> 切回）。"""
import json
import time
import urllib.request

TOK = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")
BASE = "http://%s:8099" % __import__('os').environ.get("MINRIGS_HOST", "")


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"X-Admin-Token": TOK,
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())


def rig_state(name="sinyebim"):
    d = api("/api/fleet")
    for r in d.get("rigs") or []:
        if r.get("name") == name:
            return r
    return {}


def wait_kernel(expect, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = rig_state()
        snap = r.get("snapshot") or {}
        if snap.get("kernel") == expect:
            return True, snap
        time.sleep(15)
    return False, rig_state().get("snapshot") or {}


print("== 1. set_kernel: WildRig -> SRBMiner-MULTI ==")
print(json.dumps(api("/api/command", "POST",
                     {"rig": "sinyebim", "cmd": "set_kernel",
                      "arg": {"kernel": "SRBMiner-MULTI"}}), ensure_ascii=False))
ok, snap = wait_kernel("SRBMiner-MULTI")
print("kernel 已切换:", ok, "| kernel_ver:", snap.get("kernel_ver"))
m0 = (snap.get("miners") or [{}])[0]
print("miner:", m0.get("type"), "| alive:", m0.get("alive"))

print("\n== 2. 稳定 90s 后确认在线与算力 ==")
time.sleep(90)
r = rig_state()
snap = r.get("snapshot") or {}
print("status:", r.get("status"), "| kernel:", snap.get("kernel"),
      "| kernel_ver:", snap.get("kernel_ver"))
t = api("/api/fleet").get("total") or {}
print("fleet total: online=%s ths=%s" % (t.get("online"), t.get("ths")))

print("\n== 3. 切回 WildRig ==")
print(json.dumps(api("/api/command", "POST",
                     {"rig": "sinyebim", "cmd": "set_kernel",
                      "arg": {"kernel": "WildRig"}}), ensure_ascii=False))
ok, snap = wait_kernel("WildRig")
print("kernel 已切回:", ok, "| kernel_ver:", snap.get("kernel_ver"))

print("\n== 4. 最终状态 ==")
time.sleep(30)
r = rig_state()
snap = r.get("snapshot") or {}
print("status:", r.get("status"), "| pool:", r.get("pool_url"),
      "| kernel:", snap.get("kernel"))
t = api("/api/fleet").get("total") or {}
print("fleet total: online=%s ths=%s" % (t.get("online"), t.get("ths")))
