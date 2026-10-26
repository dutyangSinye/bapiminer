# -*- coding: utf-8 -*-
"""拉线上 /api/fleet，检查 rigs[].wallet 与 total.wallet_* 实况。"""
import os
import json
import urllib.request

req = urllib.request.Request(
    "http://%s:8099/api/fleet" % os.environ.get("MINRIGS_HOST", ""),
    headers={"X-Admin-Token": os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")})
d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
t = d.get("total") or {}
print("total.wallet_reward:", json.dumps(t.get("wallet_reward")))
print("total.wallet_paid:", json.dumps(t.get("wallet_paid")))
print("total.wallet_age:", t.get("wallet_age"))
for r in d.get("rigs") or []:
    w = r.get("wallet")
    print("rig", r["name"], "| wallet=", ("OBJ" if isinstance(w, dict) else repr(w)),
          "| rewards.today=", (w or {}).get("rewards", {}).get("today") if isinstance(w, dict) else None)
