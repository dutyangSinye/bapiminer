# -*- coding: utf-8 -*-
"""查三台机器最新几条采样的 accepted/rejected，确认 0/0 的影响范围。"""
import os
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)

script = (
    "import sqlite3, time\n"
    "c = sqlite3.connect('/app/data/fleet.db')\n"
    "for rig in ['dutyangbim', 'dutyyangsinye', 'sinyebim']:\n"
    "    rows = c.execute('select ts,ths,accepted,rejected,uptime,pool from samples where rig=? order by ts desc limit 3', (rig,)).fetchall()\n"
    "    print(rig, [(time.strftime('%H:%M:%S', time.localtime(t)), ths, a, rj, up, (p or '')[:30]) for t, ths, a, rj, up, p in rows])\n"
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
