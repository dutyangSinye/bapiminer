# -*- coding: utf-8 -*-
"""拉生产 /api/fleet，dump 每台矿机的钱包对象，定位「矿池未付」拉不到的原因。"""
import json, os, urllib.request

TOKEN = os.environ.get("TOKEN", "")
URL = "http://bapimine.click:8099/api/fleet"
req = urllib.request.Request(URL, headers={"User-Agent": "bapiminer-check/1.0",
                                           "X-Admin-Token": TOKEN})
with urllib.request.urlopen(req, timeout=20) as r:
    d = json.loads(r.read().decode())

print("top keys:", list(d.keys()))
t = d.get("total") or {}
for k in ("wallet_total", "wallet_pools", "wallet_paid", "wallet_reward",
          "wallet_reward_coin", "cny_rate"):
    print("  total.%s = %s" % (k, json.dumps(t.get(k), ensure_ascii=False)[:300]))

print("\n--- 每台矿机钱包 ---")
for rig in d.get("rigs") or []:
    w = rig.get("wallet") or {}
    snap = rig.get("snapshot") or {}
    print("[%s] pool_url=%s snapshot.pool=%s" % (rig.get("name"), rig.get("pool_url"),
                                                (snap.get("pool") or "")[:40]))
    print("    wallet:", json.dumps(w, ensure_ascii=False)[:400])
