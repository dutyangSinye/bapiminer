# -*- coding: utf-8 -*-
"""排查：rigs 表 wallet/pool_url + last_report 快照内容（stdin 传脚本）。"""
import paramiko

PY = r'''
import sqlite3, json, time
c = sqlite3.connect("/app/data/fleet.db")
for n, w, p, cv, ls in c.execute(
        "SELECT name, wallet, pool_url, client_version, last_seen FROM rigs").fetchall():
    print(n, "| wallet=", (w[:16] + "...") if w else "(EMPTY)",
          "| pool=", p, "| cv=", cv, "| seen=", int(time.time() - ls), "s ago")
for n, lr in c.execute("SELECT name, last_report FROM rigs").fetchall():
    d = json.loads(lr or "{}")
    print("SNAP", n, "wallet=", repr((d.get("wallet") or "")[:16]),
          "kernel=", d.get("kernel"), "ver=", d.get("kernel_ver"),
          "pool_url=", d.get("pool_url"))
'''

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)
si, so, se = cli.exec_command("docker exec -i minrigs python -", timeout=40)
si.write(PY)
si.flush()
si.channel.shutdown_write()
out = so.read().decode("utf-8", "replace")
err = se.read().decode("utf-8", "replace")
print(out)
if err.strip():
    print("[ERR]", err)
cli.close()
