# -*- coding: utf-8 -*-
"""轮换中控 admin_token（旧 token 已在 GitHub 公开暴露过）。"""
import paramiko, os, secrets, time

new_tok = "adm_" + secrets.token_hex(8)
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root",
            os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)

py = (
    "import json\n"
    "c = json.load(open('config.json'))\n"
    "old = c['admin_token']\n"
    "c['admin_token'] = '" + new_tok + "'\n"
    "json.dump(c, open('config.json','w'), indent=1, ensure_ascii=False)\n"
    "print('old_ends:', old[-6:])\n"
)
cmd = ("cd /opt/minrigs && cp config.json config.json.bak.$(date +%m%d%H%M) "
       "&& python3 -c \"" + py.replace('"', '\\"') + "\" && docker restart minrigs")
si, so, se = ssh.exec_command(cmd, timeout=90)
print(so.read().decode())
time.sleep(8)

def try_tok(tok):
    si, so, _ = ssh.exec_command(
        'curl -s -o /dev/null -w "%{http_code}" -H "X-Admin-Token: '
        + tok + '" http://127.0.0.1:8099/api/fleet', timeout=30)
    return so.read().decode().strip()

print("old token ->", try_tok(os.environ.get("MINRIGS_OLD_TOKEN", "adm_OLD_TOKEN")))
print("new token ->", try_tok(new_tok))
ssh.close()
with open(r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\.new_admin_token.txt", "w") as f:
    f.write(new_tok)
print("saved to minrigs/.new_admin_token.txt (gitignored dir, do NOT commit)")
