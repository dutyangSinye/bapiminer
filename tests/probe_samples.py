# -*- coding: utf-8 -*-
"""一次性：查 NAS 生产库 fleet.db 的 samples 按北京日期分布 + rigs 最近上报。"""
import os, paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)
cmd = ("docker exec minrigs python -c \""
       "import sqlite3,json,time;"
       "c=sqlite3.connect('/app/data/fleet.db');"
       "c.row_factory=sqlite3.Row;"
       "print('now_utc', time.time());"
       "print(json.dumps([dict(r) for r in c.execute(\\\"SELECT strftime('%Y-%m-%d', ts+28800, 'unixepoch') d, COUNT(*) n, ROUND(AVG(ths),1) aths FROM samples GROUP BY d ORDER BY d DESC LIMIT 6\\\")], ensure_ascii=False));"
       "print(json.dumps([dict(r) for r in c.execute('SELECT name,last_seen,wallet FROM rigs')], ensure_ascii=False))"
       "\"")
si, so, se = cli.exec_command(cmd, timeout=30)
print(so.read().decode("utf-8", "replace"))
err = se.read().decode("utf-8", "replace")
if err.strip():
    print("[STDERR]", err)
cli.close()
