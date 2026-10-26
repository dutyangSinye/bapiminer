# -*- coding: utf-8 -*-
"""部署「更换矿池 / 切换内核」到生产中控：
备份 -> SFTP(server.py + index.html + BaPiMiner.exe + client_release.json)
-> 容器内编译自检 -> docker restart -> 验证。

用法: python deploy_pool_kernel.py [备份戳]
前置：先在本机 client/ 目录用 PyInstaller 打出 dist/BaPiMiner.exe（版本号以
client/miner_client.py 的 CLIENT_VERSION 与 spec 一致为准）。
"""
import json
import os
import sys
import time

import paramiko

HOST, PORT, USER, PW = os.environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", "")
LOCAL_SRV = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server"
LOCAL_EXE = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\client\dist\BaPiMiner.exe"
STAMP = sys.argv[1] if len(sys.argv) > 1 else "20261024-pool"
CLIENT_VER = "1.12.0.3"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, PORT, USER, PW, timeout=25, banner_timeout=30, auth_timeout=30,
            look_for_keys=False, allow_agent=False)
sftp = cli.open_sftp()


def run(cmd, timeout=90):
    si, so, se = cli.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    err = se.read().decode("utf-8", "replace")
    return out + (("\n[STDERR] " + err) if err.strip() else "")


print("=== 1. 备份线上文件 ===")
print(run(
    "cd /opt/minrigs && "
    "cp -p server.py server.py.bak-%s && "
    "cp -p static/index.html static/index.html.bak-%s && "
    "cp -p data/downloads/BaPiMiner.exe data/downloads/BaPiMiner.exe.bak-%s 2>/dev/null; "
    "cp -p data/client_release.json data/client_release.json.bak-%s 2>/dev/null; "
    "ls -la server.py.bak-%s static/index.html.bak-%s" % (
        STAMP, STAMP, STAMP, STAMP, STAMP, STAMP)))

print("=== 2. SFTP 上传 ===")
for rel, dst in (("server.py", "/opt/minrigs/server.py"),
                 ("static/index.html", "/opt/minrigs/static/index.html")):
    src = os.path.join(LOCAL_SRV, rel.replace("/", os.sep))
    sftp.put(src, dst)
    print("PUT %-18s -> %s (%d bytes)" % (rel, dst, sftp.stat(dst).st_size))
if not os.path.isfile(LOCAL_EXE):
    print("!! 本地找不到 %s（先打包）" % LOCAL_EXE)
    sys.exit(1)
sftp.put(LOCAL_EXE, "/opt/minrigs/data/downloads/BaPiMiner.exe")
print("PUT BaPiMiner.exe -> data/downloads/ (%d bytes)"
      % sftp.stat("/opt/minrigs/data/downloads/BaPiMiner.exe").st_size)
release = {"version": CLIENT_VER,
           "notes": "中控新增更换矿池 / 切换内核（set_pool / set_kernel 命令）；"
                    "上报 pool_url 供看板展示",
           "uploaded": time.time()}
with sftp.open("/opt/minrigs/data/client_release.json", "w") as f:
    f.write(json.dumps(release, ensure_ascii=False, indent=2))
print("PUT client_release.json -> %s" % CLIENT_VER)

print("=== 3. 语法自检（容器内 python 编译） ===")
print(run("docker exec minrigs python -m py_compile /app/server.py && echo CONTAINER_COMPILE_OK"))

print("=== 4. 重启容器 ===")
print(run("docker restart minrigs && sleep 6 && docker ps --filter name=minrigs "
          "--format '{{.Names}} | {{.Status}}'"))

print("=== 5. 验证新接口 ===")
tok = run("python3 -c \"import json;print(json.load(open('/opt/minrigs/config.json'))['admin_token'])\"").strip()
print(run("curl -s -H 'X-Admin-Token: %s' http://127.0.0.1:8099/api/pools | "
          "python3 -c \"import json,sys;d=json.load(sys.stdin);"
          "print('pools ok:',d.get('ok'),'| brands:',[b['id'] for b in d.get('brands') or []])\"" % tok))
print(run("curl -s http://127.0.0.1:8099/api/client_latest"))
print(run("curl -s http://127.0.0.1:8099/api/downloads | python3 -c \""
          "import json,sys;d=json.load(sys.stdin);"
          "[print('dl',i['name'],i['ready'],i['size'],i.get('version')) for i in d['items']]\""))

print("=== 6. /api/fleet 结构（含 pool_url）===")
print(run(
    "curl -s -H 'X-Admin-Token: %s' http://127.0.0.1:8099/api/fleet | python3 -c \""
    "import json,sys;"
    "d=json.load(sys.stdin);"
    "t=d.get('total') or {};"
    "print('online:',t.get('online'),'| ths:',t.get('ths'));"
    "print('wallet_reward:',json.dumps(t.get('wallet_reward')));"
    "[print(' rig',r['name'],'| online:',r['status'],'| kernel:',(r.get('snapshot') or {}).get('kernel'),"
    "'| pool:',r.get('pool_url') or '(未上报)') for r in d.get('rigs') or []]"
    "\"" % tok))

print("=== 7. 日志 ===")
print(run("docker logs --since 3m minrigs 2>&1 | grep -E 'Traceback|Error|pool_url' | tail -6"))

sftp.close()
cli.close()
print("DEPLOY DONE")
