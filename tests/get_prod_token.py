# -*- coding: utf-8 -*-
"""一次性：从 NAS 读生产 admin_token 并打印。"""
import os, paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)
si, so, se = cli.exec_command(
    "python3 -c \"import json;print(json.load(open('/opt/minrigs/config.json'))['admin_token'])\"",
    timeout=30)
print(so.read().decode().strip())
cli.close()
