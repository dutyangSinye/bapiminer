# -*- coding: utf-8 -*-
"""多用户模式部署到 NAS Docker：服务端 + 看板 + 客户端 exe 一把推。

内容：
  ⓪ 远端自动探测部署目录（/opt/minrigs 优先，其次 /data_7HKERMAF/minrigs）
  ① 上传 server.py / static/index.html / static/bapiminer.svg
  ② 预置 admin 账号（D1：无 TTY 环境用 config.json 的 admin_user/admin_password）
  ③ 替换宿主机文件 + docker restart（不重建镜像，volumes/端口不丢）
  ④ 回读校验：users 表 / rigs.owner_id / samples 行数 / 看板接口
  ⑤ 可选发布客户端 exe（--client）

用法：
    set SSH_HOST=192.168.112.188 SSH_PORT=10000 SSH_USER=root SSH_PASS=***
    python deploy/deploy_multiuser.py --admin-user admin --admin-password 'xxx' \
        --client --notes '中控多用户模式'

    # 只打印将要执行的远端脚本，不连 NAS：
    python deploy/deploy_multiuser.py --admin-user admin --admin-password 'xxx' \
        --print-scripts all
"""
import argparse
import base64
import json
import os
import sys
import time

import paramiko

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCAL_SERVER = os.path.join(ROOT, "server")
BUNDLE = os.path.join(HERE, "bundle")
EXE = os.path.join(ROOT, "client", "dist", "BaPiMiner.exe")

REMOTE_DIR = ""                                  # 留空 → 远端自动探测
REMOTE_DIR_CANDIDATES = ["/opt/minrigs", "/data_7HKERMAF/minrigs"]
REMOTE_EXE = "/opt/minrigs/data/downloads/BaPiMiner.exe"
REMOTE_REL = "/opt/minrigs/data/client_release.json"
PUBLIC_API = "http://bapimine.click:8099/api/client_latest"
CLIENT_VERSION = "1.13.0.0"


def b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def normalize_remote_dir(p):
    """修正 Git Bash / MSYS 的路径转换副作用。

    在 Git Bash 下命令行参数里的 /opt/minrigs 会被自动转成
    D:/WorkBuddy/.../PortableGit/opt/minrigs。这里还原成原始的 POSIX 路径。
    """
    if not p:
        return p
    if ":/" in p:                       # 出现了盘符 → 说明被 MSYS 转过了
        for cand in REMOTE_DIR_CANDIDATES:
            if p.endswith(cand):
                return cand
        # 未知后缀：截掉盘符前缀后第一个以 / 开头的候选路径
        idx = p.find(":/")
        tail = p[idx + 1:]
        print("提示：检测到路径被 MSYS 转换，已修正为 %s" % tail)
        return tail
    return p


def run_sh(cli, script, tag, timeout=420):
    stdin, stdout, stderr = cli.exec_command("sh -s", timeout=timeout)
    stdin.write(script)
    stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    print("===== %s (rc=%d) =====" % (tag, rc))
    print(out)
    if err.strip():
        print("[stderr]", err[-1200:])
    return rc, out

def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(os.environ["SSH_HOST"], port=int(os.environ.get("SSH_PORT", "22")),
                username=os.environ["SSH_USER"], password=os.environ["SSH_PASS"],
                timeout=30, banner_timeout=30, look_for_keys=False, allow_agent=False)
    return cli


# --------------------------------------------------------------------------
# 远端脚本组装（纯离线计算，可在不连 NAS 的情况下打印）
# --------------------------------------------------------------------------

def _mk_upload(remote_dir):
    srv = b64(os.path.join(LOCAL_SERVER, "server.py"))
    idx = b64(os.path.join(LOCAL_SERVER, "static", "index.html"))
    svg = b64(os.path.join(LOCAL_SERVER, "static", "bapiminer.svg"))
    return """#!/bin/sh
set -e
export PW='%s'
B=/tmp/minrigs-mu
rm -rf $B && mkdir -p $B/static
cat > $B/server.py.b64 <<'EOFB64'
%s
EOFB64
cat > $B/index.html.b64 <<'EOFB64'
%s
EOFB64
cat > $B/svg.b64 <<'EOFB64'
%s
EOFB64
base64 -d $B/server.py.b64 > $B/server.py
base64 -d $B/index.html.b64 > $B/static/index.html
base64 -d $B/svg.b64 > $B/static/bapiminer.svg
echo "$PW" | sudo -S -p '' cp $B/server.py %s/server.py
echo "$PW" | sudo -S -p '' cp $B/static/index.html %s/static/index.html
echo "$PW" | sudo -S -p '' cp $B/static/bapiminer.svg %s/static/bapiminer.svg
echo UPLOAD_OK
""" % (os.environ.get("SSH_PASS", ""), srv, idx, svg,
       remote_dir, remote_dir, remote_dir)


def _mk_preset(remote_dir, admin_user, admin_password):
    admin_b64 = base64.b64encode(json.dumps({
        "admin_user": admin_user,
        "admin_password": admin_password,
    }, ensure_ascii=False).encode("utf-8")).decode()
    # 注意：
    #  1) config.json 是 bind-mount :ro 挂进容器的，改宿主机文件后必须 docker restart 才生效；
    #     且 bind-mount 的是「文件」→ 必须用 os.replace() 换新 inode，否则容器里看不到新内容
    #     （docker restart 会重新建立挂载，届时才生效）；
    #  2) 这里不用 sudo（root 登录本就有权限），避免 sudo -S 抢 stdin 导致 heredoc 被吞；
    #  3) ★ 只有「库里还没有管理员」时才写 admin_password 明文；
    #     库里已有 admin 时**删掉**明文密码 —— 密码改由 SEED-ADMIN 直接写库。
    #     （服务端 ensure_admin_user() 看到已有 admin 会直接返回，读不到这个键）
    #     探测用宿主机上的 data/fleet.db（它 bind-mount 进容器 /app/data，同一个文件）。
    return """#!/bin/sh
set -e
export PW='%s'
B=/tmp/minrigs-mu
mkdir -p $B
cat > $B/admin.json.b64 <<'EOFB64'
%s
EOFB64
base64 -d $B/admin.json.b64 > $B/admin.json
python3 - "$B/admin.json" %s/config.json %s/data/fleet.db <<'PYEOF'
import json, os, sqlite3, sys
src, dst, dbpath = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as f:
    admin = json.load(f)
try:
    with open(dst, encoding="utf-8") as f:
        cfg = json.load(f)
except Exception:
    cfg = {}
cfg["admin_user"] = admin["admin_user"]
cfg.setdefault("port", 8099)

# 库里是否已有管理员？
has_admin = False
try:
    c = sqlite3.connect(dbpath, timeout=10)
    has_admin = c.execute("SELECT COUNT(*) FROM users WHERE is_admin=1").fetchone()[0] > 0
    c.close()
except Exception as e:
    print("探测 users 表失败（按无管理员处理）: %%s" %% e)

if has_admin:
    # 已有管理员 → 清掉明文密码，避免长期留盘
    cfg.pop("admin_password", None)
    print("config.json: 已清除明文 admin_password（管理员已存在）")
else:
    cfg["admin_password"] = admin["admin_password"]
    print("config.json: 写入临时 admin_password（首次启动预置用）")

tmp = dst + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, dst)          # ← 原子替换，缺这行配置不会生效
print("config.json 已更新（admin_user=%%s）" %% cfg["admin_user"])
PYEOF
echo '--- config.json now ---'
cat %s/config.json
echo PRESET_OK
""" % (os.environ.get("SSH_PASS", ""), admin_b64, remote_dir, remote_dir, remote_dir)


# --------------------------------------------------------------------------
# SEED-ADMIN：在容器内直接把管理员密码重置为临时密码，并打上「首次登录须改密」标记
#
# 为什么不能只靠 config.json 的 admin_user/admin_password：
#   server.ensure_admin_user() 的第一行就是「users 表已有 admin → 直接返回」，
#   生产库里 admin 早就存在了，所以改完 server.py 重启后预置逻辑根本不会跑，
#   must_change_pwd 标记也就永远不会被打上。
#
# 这里直接用与服务端同一套 PBKDF2 参数（200k 轮 / SHA256 / 16 字节盐）
# 重算哈希写库，并清掉该用户的旧会话，强制下次登录走改密流程。
# --------------------------------------------------------------------------

def _mk_seed_admin(admin_user, admin_password):
    py = """
import hashlib, hmac, os, secrets, sqlite3, sys

USERNAME = %r
PASSWORD = %r
DB = '/app/data/fleet.db'
ROUNDS = 200000

def hash_password(pw, salt_hex=None):
    salt = salt_hex or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac('sha256', str(pw).encode('utf-8'),
                             bytes.fromhex(salt), ROUNDS)
    return dk.hex(), salt

c = sqlite3.connect(DB, timeout=20)
c.row_factory = sqlite3.Row
c.execute('PRAGMA busy_timeout=15000')

# must_change_pwd 列可能还不存在（首次部署时由 server.init_db 建）
cols = [r[1] for r in c.execute('PRAGMA table_info(users)')]
if 'must_change_pwd' not in cols:
    c.execute('ALTER TABLE users ADD COLUMN must_change_pwd INTEGER DEFAULT 0')
    c.commit()
    print('ALTER must_change_pwd OK')

h, s = hash_password(PASSWORD)
row = c.execute('SELECT id, is_admin FROM users WHERE username=?',
                (USERNAME,)).fetchone()
if row is None:
    now = __import__('time').time()
    c.execute('INSERT INTO users(username,pwd_hash,pwd_salt,is_admin,status,'
              'created_at,last_login_at,must_change_pwd) '
              'VALUES(?,?,?,?,?,?,?,?)',
              (USERNAME, h, s, 1, 'active', now, now, 1))
    c.commit()
    print('ADMIN_CREATED', USERNAME)
else:
    c.execute('UPDATE users SET pwd_hash=?, pwd_salt=?, is_admin=1, '
              'status=\\'active\\', must_change_pwd=1 WHERE id=?', (h, s, row['id']))
    c.commit()
    print('ADMIN_RESET', USERNAME)

# 吊销该用户的全部旧会话 —— 否则已登录的浏览器 token 还能继续用旧密码态
uid = c.execute('SELECT id FROM users WHERE username=?', (USERNAME,)).fetchone()['id']
n = c.execute('DELETE FROM sessions WHERE user_id=?', (uid,)).rowcount
c.commit()
print('SESSIONS_REVOKED', n)
print('SEED_ADMIN_OK')
""" % (admin_user, admin_password)
    b64 = base64.b64encode(py.encode("utf-8")).decode()
    return """#!/bin/sh
set -e
B=/tmp/minrigs-mu
mkdir -p $B
cat > $B/seed_admin.py.b64 <<'EOFB64'
%s
EOFB64
base64 -d $B/seed_admin.py.b64 > $B/seed_admin.py
docker cp $B/seed_admin.py minrigs:/tmp/seed_admin.py
docker exec minrigs python /tmp/seed_admin.py
docker exec minrigs rm -f /tmp/seed_admin.py
""" % b64


def _mk_build(remote_dir):
    # 不用 docker build 重建镜像：生产容器 bind-mount 宿主机上的 server.py/static，
    # 替换宿主机文件后重启容器即可生效 —— volumes / 镜像 / 端口映射都不丢，
    # 回滚也只需还原备份。
    return """#!/bin/sh
set -e
export PW='%s'
cd %s
STAMP=$(date +%%Y%%m%%d_%%H%%M%%S)
echo "$PW" | sudo -S -p '' cp server.py server.py.bak.$STAMP 2>/dev/null || true
echo "$PW" | sudo -S -p '' cp static/index.html static/index.html.bak.$STAMP 2>/dev/null || true
echo "BACKUP_STAMP=$STAMP"
echo "$PW" | sudo -S -p '' docker restart minrigs
for i in $(seq 1 20); do
  sleep 2
  if curl -s --noproxy '*' http://127.0.0.1:8099/api/health >/dev/null 2>&1; then break; fi
done
echo '--- logs ---'
echo "$PW" | sudo -S -p '' docker logs --tail 20 minrigs 2>&1
echo '--- health ---'
curl -s --noproxy '*' http://127.0.0.1:8099/api/health
echo
echo '--- ps ---'
echo "$PW" | sudo -S -p '' docker ps --filter name=minrigs --format '{{.Names}} {{.Status}} {{.Ports}}'
echo BUILD_RUN_OK
""" % (os.environ.get("SSH_PASS", ""), remote_dir)


def _mk_verify(remote_dir, admin_user, admin_password):
    # 注意：管理员被打了 must_change_pwd 标记后，登录接口返回的是
    #   {"ok":true,"need_password_change":true,...} —— **不发 token**
    # 所以这里不能用 token 去调 /api/fleet 做鉴权验证，改成只看 HTTP 码。
    return """#!/bin/sh
set -e
export PW='%s'
echo '--- users 表 ---'
echo "$PW" | sudo -S -p '' docker exec minrigs python -c "
import sqlite3
c=sqlite3.connect('/app/data/fleet.db'); c.row_factory=sqlite3.Row
for r in c.execute('SELECT id,username,is_admin,status,must_change_pwd FROM users'):
    print(dict(r))
print('rigs 总数:', c.execute('SELECT COUNT(*) FROM rigs').fetchone()[0])
print('owner_id=0 (未绑定):', c.execute('SELECT COUNT(*) FROM rigs WHERE owner_id=0 OR owner_id IS NULL').fetchone()[0])
print('已绑定:', c.execute('SELECT COUNT(*) FROM rigs WHERE owner_id>0').fetchone()[0])
print('samples:', c.execute('SELECT COUNT(*) FROM samples').fetchone()[0])
print('rig_owner_log:', c.execute('SELECT COUNT(*) FROM rig_owner_log').fetchone()[0])
print('sessions:', c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
"
echo '--- 看板接口 ---'
curl -s --noproxy '*' -o /dev/null -w 'fleet=%%{http_code} ' http://127.0.0.1:8099/api/fleet
curl -s --noproxy '*' -o /dev/null -w 'login=%%{http_code} ' -X POST -H 'Content-Type: application/json' \\
  -d '{"username":"%s","password":"%s"}' http://127.0.0.1:8099/api/auth/login
curl -s --noproxy '*' -o /dev/null -w 'authme=%%{http_code} ' http://127.0.0.1:8099/api/auth/me
curl -s --noproxy '*' -o /dev/null -w 'dash=%%{http_code}\\n' http://127.0.0.1:8099/
echo '--- 登录响应（应含 need_password_change 且无 token）---'
curl -s --noproxy '*' -X POST -H 'Content-Type: application/json' \\
  -d '{"username":"%s","password":"%s"}' http://127.0.0.1:8099/api/auth/login
echo
echo VERIFY_OK
""" % (os.environ.get("SSH_PASS", ""), admin_user, admin_password, admin_user,
       admin_password)


def build_scripts(remote_dir, admin_user, admin_password):
    return {
        "upload": _mk_upload(remote_dir),
        "preset": _mk_preset(remote_dir, admin_user, admin_password),
        "seed_admin": _mk_seed_admin(admin_user, admin_password),
        "build": _mk_build(remote_dir),
        "verify": _mk_verify(remote_dir, admin_user, admin_password),
    }


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-user", default="admin",
                    help="首个管理员用户名（默认 admin）")
    ap.add_argument("--admin-password", default="",
                    help="临时初始密码；留空则自动生成随机临时密码（首次登录须改密）")
    ap.add_argument("--client", action="store_true",
                    help="同时发布客户端 exe（★ 会触发全部矿机端静默自更新，"
                         "务必本机验证通过后再用）")
    ap.add_argument("--notes", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remote-dir", default="", help="手动指定部署目录（默认远端自动探测）")
    ap.add_argument("--print-scripts", default="",
                    help="只打印生成的远端脚本（upload/preset/seed_admin/build/verify/all），"
                         "不连接 NAS")
    ap.add_argument("--no-seed-admin", action="store_true",
                    help="不重置管理员密码（保留现有密码与 must_change_pwd 状态）")
    a = ap.parse_args()
    a.remote_dir = normalize_remote_dir(a.remote_dir)

    if not a.admin_user:
        print("必须提供 --admin-user（D1：生产无 TTY，需预置首个管理员）")
        return 2

    # 初始密码：留空则生成随机临时密码。
    # 服务端会给预置账号打上 must_change_pwd 标记 → 首次登录必须改密，
    # 因此这个初始密码只是「开箱用一次」，不是长期凭据。
    tmp_password = a.admin_password
    generated = False
    if not tmp_password:
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits
        tmp_password = "T" + "".join(secrets.choice(alphabet) for _ in range(11))
        generated = True

    need = [os.path.join(LOCAL_SERVER, "server.py"),
            os.path.join(LOCAL_SERVER, "static", "index.html"),
            os.path.join(LOCAL_SERVER, "static", "bapiminer.svg")]
    for p in need:
        if not os.path.isfile(p):
            print("缺少文件：%s" % p)
            return 2

    # --print-scripts：离线打印，不连 NAS
    if a.print_scripts:
        sc = build_scripts(a.remote_dir or REMOTE_DIR_CANDIDATES[0],
                           a.admin_user, tmp_password)
        keys = list(sc) if a.print_scripts == "all" else [a.print_scripts]
        for k in keys:
            if k not in sc:
                print("未知脚本名：%s（可选：upload / preset / build / verify / all）" % k)
                return 2
            print("########## %s ##########" % k)
            print(sc[k])
        return 0

    if a.dry_run:
        print("DRY-RUN：本地文件齐备（server.py / index.html / bapiminer.svg），未连接 NAS。")
        print("DRY-RUN：执行链路 = ⓪探测目录 → ①上传+备份 → ②预置admin → "
              "③重置admin密码(须改密) → ④替换+docker restart → ⑤回读校验")
        print("DRY-RUN：远端目录候选：%s" % " / ".join(REMOTE_DIR_CANDIDATES))
        print("DRY-RUN：客户端发布 = %s" % ("是（会触发矿机端静默自更新）" if a.client else "否"))
        return 0

    cli = connect()
    ok = False
    try:
        # ---------- ⓪ 远端探测：定位部署目录 ----------
        remote_dir = a.remote_dir or REMOTE_DIR
        if not remote_dir:
            probe = """#!/bin/sh
for d in %s; do
  if [ -f "$d/server.py" ]; then echo "REMOOTEDIR=$d"; break; fi
done
echo "CONTAINERS=$(docker ps --format '{{.Names}}' 2>/dev/null | tr '\\n' ',')"
""" % " ".join(REMOTE_DIR_CANDIDATES)
            rc, out = run_sh(cli, probe, "⓪ PROBE")
            for line in out.splitlines():
                if line.startswith("REMOOTEDIR="):
                    remote_dir = line.split("=", 1)[1].strip()
            if not remote_dir:
                print("未能在 NAS 上找到 minrigs 部署目录，请用 --remote-dir 指定")
                return 3
            print("探测到部署目录：%s" % remote_dir)

        sc = build_scripts(remote_dir, a.admin_user, tmp_password)

        rc, out = run_sh(cli, sc["upload"], "① UPLOAD")
        if "UPLOAD_OK" not in out:
            print("UPLOAD FAILED")
            return 1

        rc, out = run_sh(cli, sc["preset"], "② PRESET ADMIN")
        if "PRESET_OK" not in out:
            print("PRESET FAILED")
            return 1

        rc, out = run_sh(cli, sc["build"], "③ RESTART")
        if "BUILD_RUN_OK" not in out:
            print("RESTART FAILED")
            return 1

        # ---------- ④ 重置管理员密码 + 打「首次登录须改密」标记 ----------
        # 必须在 docker restart **之后**执行：
        #   a) 新 server.py 启动时 init_db() 才会建出 must_change_pwd 列；
        #   b) admin 早就存在，ensure_admin_user() 第一行就「已有 admin → 直接返回」，
        #      不走预置分支 → 标记永远打不上，只能在这里直接写库。
        # 顺带吊销该用户的旧会话，避免旧 token 继续可用。
        if not a.no_seed_admin:
            rc, out = run_sh(cli, sc["seed_admin"], "④ SEED ADMIN (强制改密)")
            if "SEED_ADMIN_OK" not in out:
                print("SEED_ADMIN FAILED")
                return 1

        rc, out = run_sh(cli, sc["verify"], "⑤ VERIFY")
        ok = "VERIFY_OK" in out

        # ---------- ⑥ 客户端发布（可选，默认不做）----------
        # ★ 重要：矿机端是按 exe 的 sha256 与 /api/client_latest 比对，
        #   不一致就**静默替换并重启**。也就是说只要动了 downloads/BaPiMiner.exe，
        #   全部矿机端就会自动升级 —— 不需要广播命令。
        #   所以发布客户端是「一键全网升级」的高危动作，必须本机验证通过后再用。
        if a.client:
            if not os.path.isfile(EXE):
                print("跳过客户端发布：本地 exe 不存在 %s" % EXE)
            else:
                print("=" * 60)
                print("★ 正在发布客户端 %s —— 全部矿机端将静默自动升级！" % CLIENT_VERSION)
                print("  （如仅想部署服务端，请去掉 --client 参数）")
                print("=" * 60)
                sftp = cli.open_sftp()
                sftp.put(EXE, REMOTE_EXE)
                got = sftp.stat(REMOTE_EXE).st_size
                if got != os.path.getsize(EXE):
                    print("客户端上传大小不一致：%d / %d" % (got, os.path.getsize(EXE)))
                else:
                    rel = {"version": CLIENT_VERSION,
                           "notes": a.notes or "中控多用户模式",
                           "uploaded": int(time.time())}
                    with sftp.open(REMOTE_REL, "w") as f:
                        f.write(json.dumps(rel, ensure_ascii=False, indent=1))
                    print("客户端已发布：", rel)
                sftp.close()
    finally:
        cli.close()

    if ok and not a.no_seed_admin:
        print("=" * 60)
        if generated:
            print("管理员初始密码（随机生成，临时）: %s" % tmp_password)
        else:
            print("管理员初始密码（临时）: %s" % tmp_password)
        print("★ 首次登录时必须修改密码 —— 登录后按提示设置新密码")
        print("  用户名: %s" % a.admin_user)
        print("=" * 60)
    print("DEPLOY", "OK" if ok else "CHECK LOGS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
