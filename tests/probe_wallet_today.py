# -*- coding: utf-8 -*-
"""排查「今日实际收入」与钱包实际到账不一致的问题。

产出：
1. rigs 表结构与 last_report 全文（确认矿机实际挖矿钱包地址）
2. /api/fleet 钱包聚合现状
3. Kryptex Pool API 直查：balance / stats / payouts / reward-chart
"""
import json

import os, paramiko

HOST, USER, PW = "116.169.217.29", "root", os.environ.get("MINRIGS_SSH_PASS", "")
WALLET = "prl1plthdf0l9mmpnvr3zlyqqk5jd38hzz0yue678kwmw2z6kg6l7u8nqfv7y38"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, 22, USER, PW, timeout=20)


def run(cmd, timeout=90):
    si, so, se = ssh.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    err = se.read().decode("utf-8", "replace")
    if err.strip():
        out += "\n[STDERR] " + err[:600]
    return out


print("########## 1. rigs 表 + last_report ##########")
print(run(r'''
cd /opt/minrigs && python3 - <<'PY'
import sqlite3, json
db = sqlite3.connect("data/fleet.db"); db.row_factory = sqlite3.Row
cols = [r[1] for r in db.execute("PRAGMA table_info(rigs)")]
print("COLUMNS:", cols)
for r in db.execute("select * from rigs").fetchall():
    d = dict(r); lr = d.pop("last_report", None)
    print("RIG:", json.dumps(d, ensure_ascii=False))
    if lr:
        snap = json.loads(lr)
        print("  SNAP KEYS:", sorted(snap.keys()))
        print("  SNAP:", json.dumps(snap, ensure_ascii=False)[:2000])
PY
'''))

print("########## 2. /api/fleet 钱包聚合 ##########")
print(run(r'''
cd /opt/minrigs && TOK=$(python3 -c "import json;print(json.load(open('config.json'))['admin_token'])") && curl -s -H "X-Admin-Token: $TOK" http://127.0.0.1:8099/api/fleet | python3 -c "
import json,sys
d=json.load(sys.stdin); t=d.get('total',{})
print('TOTAL:', json.dumps({k:t.get(k) for k in ('online','ths','wallet_total','wallet_paid','wallet_reward','wallet_reward_coin','wallet_coin','wallet_age')}, ensure_ascii=False))
for r in d.get('rigs',[]):
    w=r.get('wallet') or {}
    print(r['name'],'|',r['status'],'| coin:',w.get('coin'),'| addr:',(w.get('wallet') or '')[:40],'| total:',w.get('total'),'| paid:',json.dumps(w.get('paid'),ensure_ascii=False),'| rewards:',json.dumps(w.get('rewards'),ensure_ascii=False))
"
'''))

print("########## 3. Kryptex Pool API 直查 ##########")
print(run(r'''
W=prl1plthdf0l9mmpnvr3zlyqqk5jd38hzz0yue678kwmw2z6kg6l7u8nqfv7y38
for ep in balance stats payouts; do
  echo "=== $ep ==="
  curl -s --max-time 25 "https://pool.kryptex.com/prl/api/v1/miner/$ep/$W" | head -c 3000
  echo
done
echo "=== reward-chart (last 10 points) ==="
curl -s --max-time 25 "https://pool.kryptex.com/prl/api/v1/miner/reward-chart/$W" | python3 -c "
import json,sys,datetime
pts=json.load(sys.stdin)
print('points:',len(pts))
for p in pts[-10:]:
    ts=p.get('timestamp',0)/1000
    print(datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M UTC'), '->', p.get('reward'))
print('sum last 7 UTC days:', sum(p.get('reward',0) for p in pts[-7:]))
"
''', timeout=150))

ssh.close()
