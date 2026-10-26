# -*- coding: utf-8 -*-
"""部署 server.py + static/index.html 到 NAS 并重启容器，验证健康与钱包数据。

用法：python deploy_server_wallet_fix.py
"""
import json, os, time, paramiko

HOST = __import__('os').environ.get("MINRIGS_HOST", "")
USER = "root"
PASS = os.environ.get("MINRIGS_SSH_PASS", "")
LOCAL = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs"
STAMP = time.strftime("%Y%m%d-%H%M%S")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, 22, USER, PASS, timeout=25, look_for_keys=False, allow_agent=False)
sftp = cli.open_sftp()


def sh(cmd, timeout=60):
    si, so, se = cli.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    err = se.read().decode("utf-8", "replace")
    return out, err


# 1) 备份
out, err = sh("cp /opt/minrigs/server.py /opt/minrigs/server.py.bak.%s && "
               "cp /opt/minrigs/static/index.html /opt/minrigs/static/index.html.bak.%s && "
               "ls -la /opt/minrigs/server.py.bak.%s" % (STAMP, STAMP, STAMP))
print("[backup]", out.strip() or err.strip())

# 2) 上传
sftp.put(os.path.join(LOCAL, "server", "server.py"), "/opt/minrigs/server.py")
sftp.put(os.path.join(LOCAL, "server", "static", "index.html"), "/opt/minrigs/static/index.html")
print("[upload] server.py =", sftp.stat("/opt/minrigs/server.py").st_size,
      "bytes | index.html =", sftp.stat("/opt/minrigs/static/index.html").st_size, "bytes")

# 3) 重启
out, err = sh("docker restart minrigs", timeout=90)
print("[restart]", out.strip() or err.strip())

# 4) 等健康
for i in range(30):
    time.sleep(2)
    out, _ = sh("docker inspect -f '{{.State.Health.Status}}' minrigs 2>/dev/null", timeout=20)
    st = out.strip()
    if st == "healthy":
        print("[health] healthy after %ds" % ((i + 1) * 2))
        break
else:
    print("[health] NOT healthy, logs:")
    print(sh("docker logs --tail 30 minrigs 2>&1", timeout=30)[0])

# 5) 等一轮钱包循环（5 分钟一轮，等 40 秒先看有没有报错）
time.sleep(40)
out, _ = sh("docker logs --tail 25 minrigs 2>&1", timeout=30)
print("[logs tail]\n" + out)
sftp.close()
cli.close()
print("DONE stamp =", STAMP)
