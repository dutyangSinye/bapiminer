# -*- coding: utf-8 -*-
# Deploy minrigs earnings update to NAS docker (server.py + static/index.html + bapiminer.svg).
# 内容：显卡型号匹配修复（Laptop 后缀变体）+ 矿池钱包真实余额（Kryptex API）+ 看板钱包 KPI。
import base64
import sys
import paramiko

HOST, PORT = "nas.sinyerobot.com", 10000
USER, PW = "13301116664", "YYyy..83"
LOCAL = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\server"
REMOTE_DIR = "/data_7HKERMAF/minrigs"


def b64file(p):
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


srv_b64 = b64file(LOCAL + r"\server.py")
idx_b64 = b64file(LOCAL + r"\static\index.html")
svg_b64 = b64file(LOCAL + r"\static\bapiminer.svg")
print("payload: server.py=%d b64-chars, index.html=%d b64-chars, svg=%d b64-chars"
      % (len(srv_b64), len(idx_b64), len(svg_b64)))

upload_script = """#!/bin/sh
set -e
export PW='YYyy..83'
B=/tmp/minrigs-earn
rm -rf $B && mkdir -p $B/static
cat > $B/server.py.b64 <<'EOFB64'
%s
EOFB64
cat > $B/index.html.b64 <<'EOFB64'
%s
EOFB64
cat > $B/bapiminer.svg.b64 <<'EOFB64'
%s
EOFB64
base64 -d $B/server.py.b64 > $B/server.py
base64 -d $B/index.html.b64 > $B/static/index.html
base64 -d $B/bapiminer.svg.b64 > $B/static/bapiminer.svg
echo "$PW" | sudo -S -p '' cp $B/server.py %s/server.py
echo "$PW" | sudo -S -p '' cp $B/static/index.html %s/static/index.html
echo "$PW" | sudo -S -p '' cp $B/static/bapiminer.svg %s/static/bapiminer.svg
echo '--- files on NAS ---'
echo "$PW" | sudo -S -p '' ls -l %s/server.py %s/static/index.html %s/static/bapiminer.svg
echo UPLOAD_OK
""" % (srv_b64, idx_b64, svg_b64, REMOTE_DIR, REMOTE_DIR, REMOTE_DIR, REMOTE_DIR, REMOTE_DIR, REMOTE_DIR)

build_script = """#!/bin/sh
set -e
export PW='YYyy..83'
cd %s
echo "$PW" | sudo -S -p '' docker build -t minrigs-server:1.0 . 2>&1 | tail -4
echo "$PW" | sudo -S -p '' docker rm -f minrigs 2>/dev/null || true
echo "$PW" | sudo -S -p '' docker run -d --name minrigs --restart always \\
  -p 8099:8099 \\
  -v %s/data:/app/data \\
  -v %s/config.json:/app/config.json \\
  minrigs-server:1.0
sleep 6
echo '--- logs ---'
echo "$PW" | sudo -S -p '' docker logs minrigs 2>&1 | tail -8
echo '--- health ---'
curl -s --noproxy '*' http://127.0.0.1:8099/api/health
echo
echo '--- ps ---'
echo "$PW" | sudo -S -p '' docker ps --filter name=minrigs --format '{{.Names}} {{.Status}} {{.Ports}}'
echo BUILD_RUN_OK
""" % (REMOTE_DIR, REMOTE_DIR, REMOTE_DIR)


def run_sh(cli, script, tag):
    stdin, stdout, stderr = cli.exec_command("sh -s", timeout=300)
    stdin.write(script)
    stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    print("===== %s (rc=%d) =====" % (tag, rc))
    print(out)
    if err.strip():
        print("[stderr]", err[-800:])
    return rc, out


cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, PORT, USER, PW, timeout=30, banner_timeout=30)

rc1, out1 = run_sh(cli, upload_script, "UPLOAD")
if "UPLOAD_OK" not in out1:
    print("UPLOAD FAILED")
    sys.exit(1)

rc2, out2 = run_sh(cli, build_script, "BUILD+RUN")
ok = "BUILD_RUN_OK" in out2 and '"ok":true' in out2.replace(" ", "")
cli.close()
print("DEPLOY", "OK" if ok and rc2 == 0 else "CHECK LOGS")
sys.exit(0 if ok and rc2 == 0 else 1)
