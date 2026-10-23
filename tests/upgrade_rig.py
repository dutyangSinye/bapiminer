# -*- coding: utf-8 -*-
"""逐台下发 update_client（单触发防竞态），并轮询确认升级结果。"""
import os, paramiko, json, time, sys

RIG = sys.argv[1] if len(sys.argv) > 1 else "dutyangsinye"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)


def run(cmd, timeout=30):
    _si, so, _se = ssh.exec_command(cmd, timeout=timeout)
    return so.read().decode("utf-8", "replace")


TOK = run("python3 -c \"import json;print(json.load(open('/opt/minrigs/config.json'))['admin_token'])\"").strip()
print("token ok:", bool(TOK))

out = run(
    "curl -s -X POST -H 'Content-Type: application/json' -H 'X-Admin-Token: %s' "
    "-d '{\"rig\":\"%s\",\"cmd\":\"update_client\"}' http://127.0.0.1:8099/api/command" % (TOK, RIG))
print("下发[%s]:" % RIG, out.strip())

for i in range(10):
    time.sleep(15)
    st = run("python3 - <<'PY'\n"
             "import sqlite3, json\n"
             "db = sqlite3.connect('/opt/minrigs/data/fleet.db')\n"
             "db.row_factory = sqlite3.Row\n"
             "r = db.execute('select name,client_version,last_seen from rigs where name=?', ('%s',)).fetchone()\n"
             "d = dict(r) if r else {}\n"
             "d['age_s'] = round(__import__('time').time() - (d.get('last_seen') or 0))\n"
             "print(json.dumps(d))\n"
             "PY\n" % RIG)
    print("t+%ds:" % ((i + 1) * 15), st.strip())
    if '"client_version": "1.11.2.2"' in st and '"age_s": ' in st:
        m = [p for p in st.split('"age_s": ') if p]
        if m and m[0].split('')[0:1]:
            pass
    if '"client_version": "1.11.2.2"' in st:
        try:
            d = json.loads(st.strip().splitlines()[-1])
            if d.get("age_s", 999) < 60:
                print("✅ %s 已升级到 1.11.2.2 且心跳正常" % RIG)
                break
        except Exception:
            pass

ssh.close()
