# -*- coding: utf-8 -*-
"""核实 wildrig 预下载状态。"""
import os, paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)
cmd = r"""
echo '--- /tmp/wildrig_dl.log ---'
cat /tmp/wildrig_dl.log 2>/dev/null || echo '(no log)'
echo '--- docker exec processes ---'
ps aux | grep -i 'kernel_download\|wildrig' | grep -v grep || echo '(none)'
echo '--- kernels dir ---'
ls -la /opt/minrigs/data/kernels/ 2>/dev/null
"""
si, so, se = ssh.exec_command(cmd, timeout=30)
print(so.read().decode("utf-8", "replace"))
err = se.read().decode("utf-8", "replace")
if err.strip():
    print("STDERR:", err[:500])
ssh.close()
