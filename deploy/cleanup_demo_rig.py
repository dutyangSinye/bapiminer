# -*- coding: utf-8 -*-
# Remove the demo rig rows from production NAS fleet.db via docker exec.
import sys
import paramiko

HOST, PORT = "bapimine.click", 10000
USER, PW = "13301116664", "YYyy..83"

py = (
    "import sqlite3;c=sqlite3.connect('/app/data/fleet.db');"
    "c.execute(\"PRAGMA journal_mode=WAL\");"
    "[c.execute(q,('GM-%E6%BC%94%E7%A4%BA%E6%9C%BA',)) for q in []];"
)
# name contains Chinese -> pass as parameter properly
py = (
    "import sqlite3;c=sqlite3.connect('/app/data/fleet.db');"
    "n='GM-\u6f14\u793a\u67ba';"
    "c.execute('DELETE FROM samples WHERE rig=?',(n,));"
    "c.execute('DELETE FROM alerts WHERE rig=?',(n,));"
    "c.execute('DELETE FROM commands WHERE rig=?',(n,));"
    "c.execute('DELETE FROM rigs WHERE name=?',(n,));"
    "c.commit();"
    "print('remaining rigs:',c.execute('SELECT count(*) FROM rigs').fetchone()[0])"
)
script = """#!/bin/sh
export PW='YYyy..83'
echo "$PW" | sudo -S -p '' docker exec minrigs python -c '%s'
echo "$PW" | sudo -S -p '' docker exec minrigs python -c "import json;print(open('/app/data/fleet.db','rb').read(16))"
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
    print("[stderr]", err[-500:])
cli.close()
sys.exit(0 if "CLEANUP_DONE" in out else 1)
