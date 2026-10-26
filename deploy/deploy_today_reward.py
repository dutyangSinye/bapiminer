# -*- coding: utf-8 -*-
"""部署「今日实际收入」到生产中控：备份 -> SFTP -> docker restart -> 验证。
用法: python deploy_today_reward.py [备份戳]   （重复部署用不同戳，保留旧备份）"""
import os
import sys
import time

import paramiko

HOST, PORT, USER, PW = os.environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", "")
LOCAL_SRV = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server"
STAMP = sys.argv[1] if len(sys.argv) > 1 else "20261023"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, PORT, USER, PW, timeout=25, banner_timeout=30, auth_timeout=30,
            look_for_keys=False, allow_agent=False)
sftp = cli.open_sftp()


def run(cmd, timeout=60):
    si, so, se = cli.exec_command(cmd, timeout=timeout)
    out = so.read().decode("utf-8", "replace")
    err = se.read().decode("utf-8", "replace")
    return out + (("\n[STDERR] " + err) if err.strip() else "")


print("=== 1. 备份线上文件 ===")
print(run(
    "cd /opt/minrigs && "
    "cp -p server.py server.py.bak-%s && "
    "cp -p static/index.html static/index.html.bak-%s && "
    "ls -la server.py.bak-%s static/index.html.bak-%s" % (STAMP, STAMP, STAMP, STAMP)))

print("=== 2. SFTP 上传 ===")
for rel, dst in (("server.py", "/opt/minrigs/server.py"),
                 ("static/index.html", "/opt/minrigs/static/index.html")):
    src = os.path.join(LOCAL_SRV, rel.replace("/", os.sep))
    sftp.put(src, dst)
    st = sftp.stat(dst)
    print("PUT %-18s -> %s (%d bytes)" % (rel, dst, st.st_size))

print("=== 3. 语法自检（容器内 python 编译） ===")
print(run("docker exec minrigs python -m py_compile /app/server.py && echo CONTAINER_COMPILE_OK"))

print("=== 4. 重启容器 ===")
print(run("docker restart minrigs && sleep 6 && docker ps --filter name=minrigs "
          "--format '{{.Names}} | {{.Status}}'"))

print("=== 5. 等钱包循环首刷（最多 40s） ===")
tok = run("python3 -c \"import json;print(json.load(open('/opt/minrigs/config.json'))['admin_token'])\"").strip()
deadline = time.time() + 40
fleet = {}
while time.time() < deadline:
    fleet = run("curl -s -H 'X-Admin-Token: %s' http://127.0.0.1:8099/api/fleet" % tok)
    try:
        import json as _j
        d = _j.loads(fleet)
        if ((d.get("total") or {}).get("wallet_reward") or {}).get("today", 0) > 0:
            break
    except Exception:
        pass
    time.sleep(5)

print("=== 6. 验证 /api/fleet ===")
print(run(
    "curl -s -H 'X-Admin-Token: %s' http://127.0.0.1:8099/api/fleet | python3 -c \""
    "import json,sys;"
    "d=json.load(sys.stdin);"
    "t=d.get('total') or {};"
    "print('online:',t.get('online'),'| ths:',t.get('ths'));"
    "print('wallet_total:',t.get('wallet_total'),t.get('wallet_coin'));"
    "print('wallet_paid:',json.dumps(t.get('wallet_paid')));"
    "print('wallet_reward:',json.dumps(t.get('wallet_reward')));"
    "print('reward_coin:',t.get('wallet_reward_coin'));"
    "print('wallet_age:',t.get('wallet_age'));"
    "[print(' rig',r['name'],'| coin:',(r.get('wallet') or {}).get('coin'),'| today:',((r.get('wallet') or {}).get('rewards') or {}).get('today')) for r in d.get('rigs') or []]"
    "\"" % tok))

print("=== 7. 看板页面与日志 ===")
print(run("curl -s http://127.0.0.1:8099/ | grep -c '今日实际收入'"))
print(run("docker logs --since 3m minrigs 2>&1 | grep -E 'wallet|Traceback|Error' | tail -8"))

sftp.close()
cli.close()
print("DEPLOY DONE")
