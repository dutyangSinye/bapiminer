# -*- coding: utf-8 -*-
"""容器内先 kernel_check 再 kernel_download("wildrig")。"""
import os, paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)
cmd = r"""
docker exec minrigs python -c "
import server, time
server.kernel_check()
t0 = time.time()
path, err = server.kernel_download('wildrig')
print('elapsed %.1fs' % (time.time()-t0))
print('path:', path)
print('err:', err)
import os
if path and os.path.exists(path):
    print('size:', os.path.getsize(path))
"
echo '--- kernels dir ---'
ls -la /opt/minrigs/data/kernels/
"""
si, so, se = ssh.exec_command(cmd, timeout=600)
print(so.read().decode("utf-8", "replace"))
err = se.read().decode("utf-8", "replace")
if err.strip():
    print("STDERR:", err[:1000])
ssh.close()
