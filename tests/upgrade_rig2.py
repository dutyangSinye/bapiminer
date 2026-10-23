# -*- coding: utf-8 -*-
"""逐台下发 update_client 并轮询确认升级到目标版本（通用版）。"""
import os, paramiko, json, time, sys

TARGET = sys.argv[1] if len(sys.argv) > 1 else "1.12.0.0"
RIGS = sys.argv[2].split(",") if len(sys.argv) > 2 else ["dutyangbim", "dutyangsinye", "sinyebim"]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)


def run(cmd, timeout=30):
    _si, so, _se = ssh.exec_command(cmd, timeout=timeout)
    return so.read().decode("utf-8", "replace")


TOK = run("python3 -c \"import json;print(json.load(open('/opt/minrigs/config.json'))['admin_token'])\"").strip()

for RIG in RIGS:
    out = run(
        "curl -s -X POST -H 'Content-Type: application/json' -H 'X-Admin-Token: %s' "
        "-d '{\"rig\":\"%s\",\"cmd\":\"update_client\"}' http://127.0.0.1:8099/api/command" % (TOK, RIG))
    print("下发[%s]:" % RIG, out.strip())
    ok = False
    for i in range(16):
        time.sleep(15)
        st = run("python3 - <<'PY'\n"
                 "import sqlite3, json, time\n"
                 "db = sqlite3.connect('/opt/minrigs/data/fleet.db')\n"
                 "db.row_factory = sqlite3.Row\n"
                 "r = db.execute('select name,client_version,last_seen from rigs where name=?', ('%s',)).fetchone()\n"
                 "d = dict(r) if r else {}\n"
                 "d['age_s'] = round(time.time() - (d.get('last_seen') or 0))\n"
                 "print(json.dumps(d))\n"
                 "PY\n" % RIG)
        try:
            d = json.loads(st.strip().splitlines()[-1])
        except Exception:
            continue
        ver, age = d.get("client_version"), d.get("age_s", 999)
        print("  t+%ds: %s age=%ss" % ((i + 1) * 15, ver, age))
        if ver == TARGET and age < 90:
            print("✅ %s 已升级到 %s 且心跳正常" % (RIG, TARGET))
            ok = True
            break
    if not ok:
        print("⚠️ %s 未在 4 分钟内确认升级到 %s" % (RIG, TARGET))
        break  # 出问题就停下，不要继续推下一台

ssh.close()
print("DONE")
