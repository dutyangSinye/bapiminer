# -*- coding: utf-8 -*-
import paramiko

HOST, PORT = "bapimine.click", 10000
USER, PW = "13301116664", "YYyy..83"

py = (
    "import sqlite3;c=sqlite3.connect('/app/data/fleet.db');"
    "rows=c.execute('SELECT name FROM rigs').fetchall();"
    "print('all:',[r[0] for r in rows]);"
    "n=[r[0] for r in rows if r[0].startswith('verify')];"
    "print('targets:',n);"
    "[c.execute('DELETE FROM samples WHERE rig=?',(x,)) for x in n];"
    "[c.execute('DELETE FROM alerts WHERE rig=?',(x,)) for x in n];"
    "[c.execute('DELETE FROM commands WHERE rig=?',(x,)) for x in n];"
    "[c.execute('DELETE FROM rigs WHERE name=?',(x,)) for x in n];"
    "c.commit();"
    "print('after:',c.execute('SELECT count(*) FROM rigs').fetchone()[0])"
)
script = """#!/bin/sh
export PW='YYyy..83'
echo "$PW" | sudo -S -p '' docker exec minrigs python -c '%s'
echo CLEANUP_DONE
""" % (py.replace("'", "'\\''"),)

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, PORT, USER, PW, timeout=30, banner_timeout=30)
stdin, stdout, stderr = cli.exec_command("sh -s", timeout=120)
stdin.write(script)
stdin.channel.shutdown_write()
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
print(out)
if err.strip():
    print("[stderr]", err[-400:])
cli.close()
