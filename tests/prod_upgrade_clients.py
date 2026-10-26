# -*- coding: utf-8 -*-
"""生产验证：广播升级客户端命令到全部 rig，然后轮询等待升级完成。"""
import os
import json
import time
import urllib.request

TOK = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")
BASE = "http://%s:8099" % os.environ.get("MINRIGS_HOST", "")


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"X-Admin-Token": TOK,
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())


TARGET = os.environ.get("TARGET_CLIENT_VERSION", "1.13.0.0")

print("== 下发 update_client（广播）==")
r = api("/api/command", "POST", {"rig": "*", "cmd": "update_client"})
print(json.dumps(r, ensure_ascii=False))

print("\n== 轮询客户端版本（最多 5 分钟）==")
t0 = time.time()
while time.time() - t0 < 300:
    time.sleep(20)
    d = api("/api/fleet")
    vers = {}
    for rig in d.get("rigs") or []:
        vers[rig["name"]] = (rig.get("client_version")
                             or (rig.get("snapshot") or {}).get("client_version")
                             or "?")
    print("[%3ds] %s" % (int(time.time() - t0), json.dumps(vers, ensure_ascii=False)))
    if all(v == TARGET for v in vers.values()) and vers:
        print("== 全部升级到 %s ==" % TARGET)
        break
else:
    print("== 超时：仍有 rig 未升级 ==")
