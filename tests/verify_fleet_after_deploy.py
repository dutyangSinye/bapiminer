# -*- coding: utf-8 -*-
"""复核生产 /api/fleet 的钱包收益字段与钱包循环日志。"""
import json
import urllib.request
import paramiko

TOK = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")

req = urllib.request.Request(
    "http://116.169.217.29:8099/api/fleet",
    headers={"X-Admin-Token": TOK})
d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
t = d.get("total") or {}
print("online:", t.get("online"), "| ths:", t.get("ths"))
print("wallet_reward:", json.dumps(t.get("wallet_reward")))
print("wallet_paid:", json.dumps(t.get("wallet_paid")))

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)
si, so, se = cli.exec_command(
    "docker logs --since 5m minrigs 2>&1 | grep -iE 'wallet|chain|Traceback' | tail -12",
    timeout=40)
out = so.read().decode()
print("---- container logs ----")
print(out if out.strip() else "(no wallet-related logs)")
cli.close()
