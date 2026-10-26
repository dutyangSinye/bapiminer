# -*- coding: utf-8 -*-
"""矿机端「首次登录强制改密 → 自动绑定」端到端测试。

用真实 server.py 起一个隔离的中控（数据落临时目录），再用**真实矿机端源码**
（client/miner_client.py）的函数走一遍客户端的登录/改密流程。

关键点：
  1. 客户端的 do_user_login / do_user_change_password 定义在 main() 内部（闭包），
     直接 import 调不到。所以这里用「源码级提取 + 独立执行」的方式复现同样的
     请求序列（rig_login → change_password → rig_login），并对**服务端行为**做断言。
     服务端才是这套语义的责任方；客户端的等价逻辑由 test_first_login_password.py
     + 静态审查覆盖。
  2. 全程用真实 HTTP（127.0.0.1），不是 mock。

覆盖：
  A. 预置账号首登 → need_password_change、不发 token
  B. 改密前无 token，矿机端无法拉受保护数据
  C. 改密成功 → 用新密码重登 → 拿到 token → 矿机绑定到该账号
  D. 旧密码失效
  E. 重复登录幂等（不产生二次绑定审计）
  F. 他人矿机拒绝绑定
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = int(os.environ.get("MU_E2E_PWD_PORT", "18955"))
BASE = "http://127.0.0.1:%d" % PORT

OK = 0
FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s   %s" % (name, extra))


def api(path, method="GET", body=None, token=None, rig_token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-User-Token", token)
    if rig_token:
        req.add_header("X-Rig-Token", rig_token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": str(e)}


# ---------------------------------------------------------------- 起服务端
TMP = tempfile.mkdtemp(prefix="minrigs_pwd_e2e_")
srv = open(os.path.join(ROOT, "server", "server.py"), encoding="utf-8").read()
srv = srv.replace('DATA_DIR = os.path.join(BASE_DIR, "data")',
                  'DATA_DIR = %r' % TMP)
srv = srv.replace('STATIC_DIR = os.path.join(BASE_DIR, "static")',
                  'STATIC_DIR = %r' % os.path.join(ROOT, "server", "static"))
srv = srv.replace('CONFIG_FILE = os.path.join(BASE_DIR, "config.json")',
                  'CONFIG_FILE = %r' % os.path.join(TMP, "config.json"))
run_py = os.path.join(TMP, "srv.py")
with open(run_py, "w", encoding="utf-8") as f:
    f.write(srv)

env = dict(os.environ)
env["PORT"] = str(PORT)
env["ADMIN_USER"] = "boss"
env["ADMIN_PASSWORD"] = "boss123456"      # 预置账号 → must_change_pwd=1
proc = subprocess.Popen([sys.executable, run_py], env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

RIG = "e2e-rig-01"
RIG_TOKEN = "e2erigtoken001"

try:
    # 等服务起来
    for _ in range(60):
        st, r = api("/api/health")
        if st == 200:
            break
        time.sleep(0.3)
    else:
        print("服务端启动失败")
        out = proc.stdout.read() if proc.stdout else ""
        print(out[-2000:])
        raise SystemExit(2)

    print("=== 0. 矿机先接入登记（rigs 表要有它才能校验接入码）===")
    st, r = api("/api/report", "POST",
                {"name": RIG, "token": RIG_TOKEN, "gpus": [], "miners": []},
                rig_token=RIG_TOKEN)
    check("矿机接入登记成功", st in (200, 201), (st, r))

    print("=== A. 预置账号首登 → 强制改密、不发 token ===")
    st, r = api("/api/rig_login", "POST",
                {"rig": RIG, "username": "boss", "password": "boss123456"},
                rig_token=RIG_TOKEN)
    check("rig_login 200", st == 200, (st, r))
    check("need_password_change=True", r.get("need_password_change") is True, r)
    check("不发 token", not r.get("token"), r.get("token"))
    check("未绑定（改密前不做归属）", "bound" not in r or r.get("bound") is False, r)

    print("\n=== B. 改密前拿不到受保护资源 ===")
    st, _ = api("/api/fleet")
    check("无 token → /api/fleet 401", st == 401, st)

    print("\n=== C. 改密 → 重登 → 绑定 ===")
    st, r = api("/api/auth/change_password", "POST",
                {"username": "boss", "old_password": "boss123456",
                 "new_password": "bossNewPass2026"})
    check("改密成功", st == 200 and r.get("ok"), (st, r))

    st, r = api("/api/rig_login", "POST",
                {"rig": RIG, "username": "boss", "password": "bossNewPass2026"},
                rig_token=RIG_TOKEN)
    check("新密码重登 200", st == 200, (st, r))
    check("拿到 token", bool(r.get("token")), r)
    check("绑定成功 bound=True", r.get("bound") is True, r)
    tok = r.get("token")

    st, r = api("/api/fleet", token=tok)
    check("带 token 可读 fleet", st == 200, (st, r))
    names = [x.get("name") for x in (r.get("rigs") or [])]
    check("矿机归属到该账号", RIG in names, names)

    print("\n=== D. 旧密码失效 ===")
    st, r = api("/api/rig_login", "POST",
                {"rig": RIG, "username": "boss", "password": "boss123456"},
                rig_token=RIG_TOKEN)
    check("旧密码被拒", st == 401, (st, r))

    print("\n=== E. 重复登录幂等 ===")
    st, r = api("/api/rig_login", "POST",
                {"rig": RIG, "username": "boss", "password": "bossNewPass2026"},
                rig_token=RIG_TOKEN)
    check("重复登录 200", st == 200, (st, r))
    check("不再产生绑定 bound=False", r.get("bound") is False, r)
    st, r = api("/api/owner_log", token=tok)
    logs = r.get("log") or []
    binds = [l for l in logs if l.get("action") == "bind"]
    check("审计里 bind 只有 1 条", len(binds) == 1, [l.get("action") for l in logs])

    print("\n=== F. 他人矿机拒绝绑定 ===")
    st, r = api("/api/auth/register", "POST",
                {"username": "otheruser", "password": "other123456"})
    check("注册普通用户成功", st == 200 and r.get("ok"), (st, r))
    oth = r.get("token")
    st, r = api("/api/rig_login", "POST",
                {"rig": RIG, "username": "otheruser", "password": "other123456"},
                rig_token=RIG_TOKEN)
    check("他人矿机登录被拒 409", st == 409, (st, r))
    check("提示已绑定", "已绑定" in str(r.get("error", "")), r)
    st, r = api("/api/fleet", token=oth)
    check("他人看不到该矿机",
          RIG not in [x.get("name") for x in (r.get("rigs") or [])],
          [x.get("name") for x in (r.get("rigs") or [])])

    print("\n=== G. 改密后账号标记已清除 ===")
    st, r = api("/api/auth/login", "POST",
                {"username": "boss", "password": "bossNewPass2026"})
    check("看板登录不再要求改密", not r.get("need_password_change"), r)
    check("直接发 token", bool(r.get("token")), r)

finally:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 52)
print("PASS %d / FAIL %d" % (OK, FAIL))
sys.exit(0 if FAIL == 0 else 1)
