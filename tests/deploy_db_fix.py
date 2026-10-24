# -*- coding: utf-8 -*-
"""部署 db autocommit 修复（根治 database is locked）并验证 sweep 恢复。"""
import paramiko, os, time

LOCAL = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server\server.py"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("116.169.217.29", 22, "root",
            os.environ.get("MINRIGS_SSH_PASS", ""), timeout=15)


def run(cmd, timeout=90):
    _si, so, _se = ssh.exec_command(cmd, timeout=timeout)
    return so.read().decode("utf-8", "replace")


run("cp /opt/minrigs/server.py /opt/minrigs/server.py.bak.$(date +%m%d%H%M)")
sftp = ssh.open_sftp()
sftp.put(LOCAL, "/opt/minrigs/server.py")
sftp.close()
out = run("grep -c 'isolation_level=None' /opt/minrigs/server.py")
print("[after upload] isolation_level hits:", out.strip())
if out.strip() != "1":
    print("!! upload mismatch, abort"); raise SystemExit(1)

run("docker restart minrigs")
for i in range(12):
    time.sleep(5)
    st = run("docker inspect --format '{{.State.Health.Status}}' minrigs").strip()
    print("[health %d] %s" % (i, st))
    if st == "healthy":
        break

# 等 sweep 跑两轮（30s/轮），检查 locked 错误是否消失 + 4080租赁被标离线
time.sleep(75)
out = run("docker logs minrigs --since 3m 2>&1 | grep -c 'database is locked' || true")
print("[locked errors last 3m]:", out.strip())
out = run("""python3 - <<'PY'
import sqlite3, time
db = sqlite3.connect('/opt/minrigs/data/fleet.db'); db.row_factory = sqlite3.Row
now = time.time()
for r in db.execute("select name,status,last_seen from rigs order by name"):
    age = int(now - (r['last_seen'] or 0))
    flag = ' <-- OFFLINE MARKED' if r['status'] == 'offline' else ''
    print('%-18s %-8s age=%ds%s' % (r['name'], r['status'], age, flag))
PY""")
print(out)
out = run("docker logs minrigs --since 3m 2>&1 | grep 'alert' | tail -5")
print("[alerts]", out)
ssh.close()
print("DONE")
