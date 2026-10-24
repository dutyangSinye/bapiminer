# -*- coding: utf-8 -*-
"""验证分池口径上线：fleet 的 wallet_pools / rig.wallet.pool_brand / unpaid_supported。"""
import os
import json
import time
import urllib.request

TOK = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")


def api(path):
    req = urllib.request.Request(
        "http://116.169.217.29:8099" + path,
        headers={"X-Admin-Token": TOK})
    return json.loads(urllib.request.urlopen(req, timeout=25).read().decode())


# 等钱包循环首轮（重启后约 1-3 分钟）
for i in range(20):
    d = api("/api/fleet")
    r0 = (d.get("rigs") or [{}])[0]
    if r0.get("wallet") and not r0["wallet"].get("pending"):
        break
    print("[%ds] wallet 循环未就绪，等待…" % (i * 15))
    time.sleep(15)

t = d.get("total") or {}
print("wallet_reward:", json.dumps(t.get("wallet_reward"), ensure_ascii=False))
print("wallet_pools:", json.dumps(t.get("wallet_pools"), ensure_ascii=False, indent=1))
for r in d.get("rigs") or []:
    w = r.get("wallet") or {}
    print("rig", r["name"], "| pool_brand:", w.get("pool_brand"),
          "| unpaid_supported:", w.get("unpaid_supported"),
          "| total:", w.get("total"), "| pending:", w.get("pending"),
          "| rewards.today:", (w.get("rewards") or {}).get("today"))
