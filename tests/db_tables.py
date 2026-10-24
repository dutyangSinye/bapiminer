# -*- coding: utf-8 -*-
"""查看 fleet.db 全部表 + 各表行数，定位快照存储位置。"""
import os
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)

script = (
    "import sqlite3\n"
    "c = sqlite3.connect('/app/data/fleet.db')\n"
    "tabs = [r[0] for r in c.execute(\"select name from sqlite_master where type='table'\")]\n"
    "print('TABLES:', tabs)\n"
    "for t in tabs:\n"
    "    cols = [r[1] for r in c.execute('pragma table_info(%s)' % t)]\n"
    "    n = c.execute('select count(*) from %s' % t).fetchone()[0]\n"
    "    print(t, 'rows=', n, 'cols=', cols)\n"
)
si, so, se = cli.exec_command("docker exec -i minrigs python3 -", timeout=30)
si.write(script)
si.flush()
si.channel.shutdown_write()
print(so.read().decode())
err = se.read().decode()
if err:
    print("ERR:", err[:800])
cli.close()
