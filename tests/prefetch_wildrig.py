# -*- coding: utf-8 -*-
"""脱离会话在服务器上预下载 wildrig 内核缓存，轮询直到落地。"""
import os, paramiko, time, sys

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)


def run(cmd, timeout=30):
    _si, so, _se = ssh.exec_command(cmd, timeout=timeout)
    return so.read().decode("utf-8", "replace"), ""


# 杀掉可能残留的旧 docker exec
run("pkill -f 'kernel_download' 2>/dev/null; true")

# 脱离会话跑：nohup bash -c 包住 docker exec，日志落盘
run("nohup bash -c \"docker exec minrigs python -c \\\""
    "import server,time;server.kernel_check();t0=time.time();"
    "p,e=server.kernel_download('wildrig');"
    "print('elapsed %.1fs path:' % (time.time()-t0), p);print('err:',e)\\\"\" "
    "> /tmp/wildrig_dl.log 2>&1 &")
print("已启动后台下载，开始轮询缓存目录...")

for i in range(40):  # 最多 40 分钟
    time.sleep(60)
    out, _ = run("ls -la /opt/minrigs/data/kernels/ 2>/dev/null; echo ---; cat /tmp/wildrig_dl.log 2>/dev/null | tail -5")
    print("[poll %d] %s" % (i + 1, out.strip()))
    sys.stdout.flush()
    if "wildrig-0.51.7.zip" in out and ".part" not in out.split("wildrig-0.51.7.zip")[0][-40:]:
        # 再确认完整文件存在且无 err
        out2, _ = run("test -f /opt/minrigs/data/kernels/wildrig-0.51.7.zip && echo EXISTS || echo NOPE")
        if "EXISTS" in out2:
            print("✅ wildrig 内核缓存已落地")
            break
ssh.close()
print("DONE")
