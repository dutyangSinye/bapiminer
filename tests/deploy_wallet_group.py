# -*- coding: utf-8 -*-
"""部署看板 index.html（按钱包分组）到中控并重启容器。"""
import paramiko

LOCAL = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server\static\index.html"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(__import__('os').environ.get("MINRIGS_HOST", ""), 22, "root", __import__("os").environ.get("MINRIGS_SSH_PASS", ""), timeout=15)


def run(cmd, timeout=60):
    _si, so, _se = ssh.exec_command(cmd, timeout=timeout)
    return so.read().decode("utf-8", "replace")


out = run("grep -c 'fGroup\\|maskWallet' /opt/minrigs/static/index.html")
print("[before] fGroup/maskWallet hits:", out.strip() or "0")

run("cp /opt/minrigs/static/index.html /opt/minrigs/static/index.html.bak.$(date +%m%d%H%M)")
sftp = ssh.open_sftp()
sftp.put(LOCAL, "/opt/minrigs/static/index.html")
sftp.close()
out = run("grep -c 'fGroup\\|maskWallet' /opt/minrigs/static/index.html")
print("[after upload] fGroup/maskWallet hits:", out.strip())
run("docker restart minrigs && sleep 4 && docker inspect --format '{{.State.Health.Status}}' minrigs")
out = run("docker inspect --format '{{.State.Health.Status}}' minrigs")
print("[health]", out.strip())

# 用 admin token 拉 fleet，确认 wallet 字段与看板可用
out = run(r"""cd /opt/minrigs && TOK=$(python3 -c "import json;print(json.load(open('config.json'))['admin_token'])") && curl -s -H "X-Admin-Token: $TOK" http://127.0.0.1:8099/api/fleet | python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d['rigs']:
    w = r.get('wallet') or {}
    print(r['name'], '->', (w.get('wallet') or '')[:16], w.get('coin'), w.get('pool_brand'))
""", timeout=60)
print(out)
ssh.close()
print("DONE")
