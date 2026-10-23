# -*- coding: utf-8 -*-
"""部署 server.py（加 wildrig 内核分发）到中控 + 触发 kernel_check + 验证缓存。"""
import os, paramiko, time, sys

LOCAL = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server\server.py"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)

def run(cmd, timeout=60):
    si, so, se = ssh.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    err = se.read().decode("utf-8", "replace")
    return out, err

# 1. 远端语法自检本地文件内容（先确认带 wildrig）
out, _ = run("grep -n 'wildrig' /opt/minrigs/server.py | head -5")
print("[before] remote server.py wildrig lines:", repr(out.strip()[:200]) or "(none)")

# 2. 备份 + 上传
run("cp /opt/minrigs/server.py /opt/minrigs/server.py.bak.$(date +%m%d%H%M)")
sftp = ssh.open_sftp()
sftp.put(LOCAL, "/opt/minrigs/server.py")
sftp.close()
out, _ = run("grep -n 'wildrig' /opt/minrigs/server.py | head -5")
print("[after upload] wildrig lines:")
print(out.strip())
if "wildrig" not in out:
    print("!! upload did not contain wildrig, abort"); sys.exit(1)

# 3. 重启容器
out, err = run("docker restart minrigs && sleep 3 && docker ps --filter name=minrigs --format '{{.Status}}'")
print("[restart]", out.strip(), err.strip()[:200])

# 4. 等 healthy 再触发 kernel_check
for i in range(10):
    out, _ = run("docker inspect --format '{{.State.Health.Status}}' minrigs")
    st = out.strip()
    print(f"[health {i}] {st}")
    if st == "healthy":
        break
    time.sleep(5)

# 5. 触发 kernel/check
out, err = run(r"""cd /opt/minrigs && TOK=$(python3 -c "import json;print(json.load(open('config.json'))['admin_token'])") && curl -s -m 60 -X POST -H "X-Admin-Token: $TOK" http://127.0.0.1:8099/api/kernel/check""", timeout=90)
print("[kernel/check]", out.strip()[:1500], err.strip()[:300])

# 6. 看容器日志里 kernel 相关行 + 缓存目录
time.sleep(5)
out, _ = run("docker logs minrigs --since 3m 2>&1 | grep -i kernel | tail -20")
print("[logs]")
print(out)
out, _ = run("ls -la /opt/minrigs/data/kernels/ 2>/dev/null")
print("[kernels dir]")
print(out)
ssh.close()
print("DONE")
