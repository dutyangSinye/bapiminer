# -*- coding: utf-8 -*-
"""一次性：列容器内 /app 数据目录 + config.json + 库内表清单。"""
import os, paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)
cmd = ("ls -la /app /app/data 2>&1 | head -30; "
       "echo ---CONFIG---; cat /opt/minrigs/config.json; "
       "echo ---TABLES---; "
       "docker exec minrigs python -c \""
       "import sqlite3,glob;"
       "fs=glob.glob('/app/**/*.db',recursive=True)+glob.glob('/app/data/*');"
       "print('files:',fs);"
       "import os;"
       "[print(f, os.path.getsize(f)) for f in fs if os.path.isfile(f)]"
       "\"")
si, so, se = cli.exec_command(cmd, timeout=30)
print(so.read().decode("utf-8", "replace"))
err = se.read().decode("utf-8", "replace")
if err.strip():
    print("[STDERR]", err)
cli.close()
