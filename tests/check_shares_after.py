# -*- coding: utf-8 -*-
"""检查各矿机在线状态 + 最新采样的 accepted/rejected。"""
import json, os, time, urllib.request, paramiko

TOKEN = os.environ.get("TOKEN", "")
BASE = "http://bapimine.click:8099"
req = urllib.request.Request(BASE + "/api/fleet",
                             headers={"User-Agent": "bapiminer-check/1.0",
                                      "X-Admin-Token": TOKEN})
with urllib.request.urlopen(req, timeout=20) as r:
    f = json.loads(r.read().decode())

print("--- 矿机状态 ---")
for rig in f.get("rigs") or []:
    snap = rig.get("snapshot") or {}
    mins = rig.get("miners") or (snap.get("miners") or [])
    m0 = mins[0] if mins else {}
    print("[%-18s] %-7s v%-9s kernel=%-9s pool=%-28s alive=%s ths=%.1f acc=%s rej=%s" % (
        rig.get("name"), rig.get("status"), rig.get("client_version") or "-",
        (snap.get("kernel") or "-"), (m0.get("pool") or "-")[:28],
        m0.get("alive"), m0.get("ths") or 0, m0.get("accepted"), m0.get("rejected")))

# samples 表最新一条
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(__import__('os').environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""),
            timeout=25, look_for_keys=False, allow_agent=False)
script = (
    "import sqlite3, time\n"
    "c = sqlite3.connect('/app/data/fleet.db')\n"
    "for rig in [r[0] for r in c.execute('select name from rigs')]:\n"
    "    r = c.execute('select ts,accepted,rejected from samples where rig=? order by ts desc limit 1', (rig,)).fetchone()\n"
    "    if r:\n"
    "        print('  %-18s %s 有效=%s 拒绝=%s' % (rig, time.strftime('%H:%M:%S', time.localtime(r[0])), r[1], r[2]))\n"
    "    else:\n"
    "        print('  %-18s 无采样' % rig)\n"
)
si, so, se = cli.exec_command("docker exec -i minrigs python3 -", timeout=40)
si.write(script)
si.flush()
si.channel.shutdown_write()
print("--- samples 最新 ---")
print(so.read().decode())
cli.close()
