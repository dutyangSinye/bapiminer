# -*- coding: utf-8 -*-
"""SSH 查中控矿机状态（SINYEBIM 升级失败排查）。"""
import os, paramiko, json, sys

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)
cmd = r"""
cd /opt/minrigs
TOK=$(python3 -c "import json;print(json.load(open('config.json'))['admin_token'])" 2>/dev/null)
curl -s -H "X-Admin-Token: $TOK" http://127.0.0.1:8099/api/fleet 2>/dev/null | head -c 3000
echo
echo '--- db ---'
python3 - <<'PY'
import sqlite3, json
try:
    db = sqlite3.connect('data/fleet.db')
    db.row_factory = sqlite3.Row
    rows = db.execute("select * from rigs").fetchall()
    for r in rows:
        d = dict(r)
        keep = {k: d.get(k) for k in ('name','client_version','online','last_seen','hostname') if k in d}
        print(json.dumps(keep, ensure_ascii=False))
except Exception as e:
    print('db err:', e)
PY
"""
si, so, se = ssh.exec_command(cmd, timeout=30)
print(so.read().decode("utf-8", "replace"))
err = se.read().decode("utf-8", "replace")
if err.strip():
    print("STDERR:", err[:500])
ssh.close()
