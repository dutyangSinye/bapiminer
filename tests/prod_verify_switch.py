# -*- coding: utf-8 -*-
"""生产功能验证（修正版）：arg.kernel 传 label（与客户端 KERNELS 键一致）。
在 sinyebim 上实测 set_kernel 幂等 + set_pool 恢复 PearlHash。"""
import json
import time
import urllib.request

TOK = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")
BASE = "http://%s:8099" % __import__('os').environ.get("MINRIGS_HOST", "")
KRYP = "stratum+tcp://prl-hk.kryptex.network:7048"
PEARL = "stratum+tcp://pool.pearlhash.xyz:9000"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"X-Admin-Token": TOK,
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())


def rig_state(name):
    d = api("/api/fleet")
    for r in d.get("rigs") or []:
        if r.get("name") == name:
            return r
    return None


def wait_pool(name, expect, timeout=150):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = rig_state(name) or {}
        last = r.get("pool_url")
        if last == expect:
            return True, last
        time.sleep(15)
    return False, last


print("== 0. 当前状态 ==")
r = rig_state("sinyebim")
print("pool:", r.get("pool_url"), "| kernel:", (r.get("snapshot") or {}).get("kernel"))

print("\n== 1. set_kernel 幂等（label: WildRig，当前正在用）==")
print(json.dumps(api("/api/command", "POST",
                     {"rig": "sinyebim", "cmd": "set_kernel",
                      "arg": {"kernel": "WildRig"}}), ensure_ascii=False))

print("\n== 2. set_pool 切回 PearlHash ==")
print(json.dumps(api("/api/command", "POST",
                     {"rig": "sinyebim", "cmd": "set_pool",
                      "arg": {"pool_url": PEARL}}), ensure_ascii=False))
ok, last = wait_pool("sinyebim", PEARL)
print("pool_url 恢复 PearlHash:", ok, "| 当前:", last)

print("\n== 3. 等 60s 确认矿工恢复 ==")
time.sleep(60)
r = rig_state("sinyebim")
snap = r.get("snapshot") or {}
print("status:", r.get("status"), "| pool:", r.get("pool_url"),
      "| kernel:", snap.get("kernel"))
t = api("/api/fleet").get("total") or {}
print("fleet total: online=%s ths=%s" % (t.get("online"), t.get("ths")))
