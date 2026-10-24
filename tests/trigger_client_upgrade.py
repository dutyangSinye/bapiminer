# -*- coding: utf-8 -*-
"""给全部矿机下发 update_client，并跟踪客户端版本升级情况。"""
import json, os, time, urllib.request, urllib.error

TOKEN = os.environ.get("TOKEN", "")
BASE = "http://mine.sinyerobot.com:8099"


def api(path, payload=None, timeout=15):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"User-Agent": "bapiminer-check/1.0", "X-Admin-Token": TOKEN}
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


fleet = api("/api/fleet")
rigs = [r["name"] for r in fleet.get("rigs") or []]
print("rigs:", rigs)
print("versions before:", {r["name"]: (r.get("client_version") or "")[:20] for r in fleet.get("rigs") or []})

for name in rigs:
    try:
        res = api("/api/command", {"rig": name, "cmd": "update_client"})
        print("  cmd %-16s -> %s" % (name, json.dumps(res, ensure_ascii=False)[:120]))
    except Exception as e:
        print("  cmd %-16s -> FAIL %s" % (name, e))

print("\n等待升级（最多 240 秒，每 20 秒查一次）...")
seen = {r["name"]: (r.get("client_version") or "") for r in fleet.get("rigs") or []}
for i in range(12):
    time.sleep(20)
    f = api("/api/fleet")
    now = {r["name"]: (r.get("client_version") or "") for r in f.get("rigs") or []}
    changed = {k: (seen.get(k), v) for k, v in now.items() if seen.get(k) != v}
    print("  [%3ds] %s" % ((i + 1) * 20, json.dumps(now, ensure_ascii=False)))
    if changed:
        print("  changed:", json.dumps(changed, ensure_ascii=False))
    if all(str(v).startswith("1.12.0.4") for v in now.values() if v):
        print("  全部已升级到 1.12.0.4")
        break
    seen = now
