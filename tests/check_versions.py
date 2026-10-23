# -*- coding: utf-8 -*-
"""查中控 fleet 各矿机版本。"""
import os, paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)
cmd = r"""
cd /opt/minrigs
python3 - <<'PY'
import sqlite3, json, time
db = sqlite3.connect('data/fleet.db')
db.row_factory = sqlite3.Row
now = time.time()
for r in db.execute("select * from rigs"):
    d = dict(r)
    keep = {k: d.get(k) for k in ('name','client_version','status','last_seen','hostname') if k in d}
    if 'last_seen' in keep:
        keep['age_s'] = int(now - keep.pop('last_seen'))
    print(json.dumps(keep, ensure_ascii=False))
PY
"""
si, so, se = ssh.exec_command(cmd, timeout=30)
print(so.read().decode("utf-8", "replace"))
err = se.read().decode("utf-8", "replace")
if err.strip():
    print("STDERR:", err[:500])
ssh.close()
