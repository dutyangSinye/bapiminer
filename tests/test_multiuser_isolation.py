# -*- coding: utf-8 -*-
"""多用户模式隔离测试（服务端）。

覆盖：
  ① 注册/登录/会话
  ② 矿机端首次登录自动挂载 + 幂等 + 他人已绑定 409
  ③ 归属隔离：A 用户看不到 B 的矿机 / 钱包 / 告警 / 日收益 / 历史
  ④ admin 可见全部 + 可强制改绑
  ⑤ 改名（rig_rename）只改 display_name，不动机器标识
  ⑥ 鉴权矩阵：未登录全部拒绝
  ⑦ 表结构幂等：重复 init_db 不报错、行数不变

用法：
    python tests/test_multiuser_isolation.py
在独立临时库上跑，不碰生产库。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.join(os.path.dirname(HERE), "server")
sys.path.insert(0, SERVER_DIR)

FAILS = []
PASSES = []


def check(name, cond, extra=""):
    if cond:
        PASSES.append(name)
        print("  PASS  %s" % name)
    else:
        FAILS.append("%s %s" % (name, extra))
        print("  FAIL  %s %s" % (name, extra))


# ---------------------------------------------------------------- 准备隔离环境

_tmpdir = tempfile.mkdtemp(prefix="minrigs_mu_")
os.environ["MINRIGS_TEST_DB"] = os.path.join(_tmpdir, "test.db")

import server as S  # noqa: E402

# 用临时库覆盖生产路径
S.DATA_DIR = _tmpdir
S.DB_FILE = os.path.join(_tmpdir, "test.db")
S.CONFIG_FILE = os.path.join(_tmpdir, "config.json")
# ★ 隔离断言：绝不能连到真实生产库
assert os.path.abspath(S.DB_FILE).startswith(os.path.abspath(_tmpdir)), \
    "测试未隔离！DB_FILE=%s" % S.DB_FILE
assert os.path.abspath(S.DB_FILE) != os.path.abspath(
    os.path.join(SERVER_DIR, "data", "fleet.db")), "测试连到了生产库！"
S._load_config()
S.init_db()

PORT = 18899
S.PORT = PORT

# 造 admin
S._insert_user("boss", "boss123456", is_admin=1)


def api(path, method="GET", body=None, user_token=None, rig_token=None, admin_token=None):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if user_token:
        req.add_header("X-User-Token", user_token)
    if rig_token:
        req.add_header("X-Rig-Token", rig_token)
    if admin_token:
        req.add_header("X-Admin-Token", admin_token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


# 启动 HTTP 服务
from http.server import ThreadingHTTPServer  # noqa: E402

_httpd = ThreadingHTTPServer(("127.0.0.1", PORT), S.Handler)
_httpd.daemon_threads = True
import threading  # noqa: E402

threading.Thread(target=_httpd.serve_forever, daemon=True).start()
time.sleep(0.4)

print("\n=== ① 注册 / 登录 / 会话 ===")
st, r = api("/api/auth/register", "POST", {"username": "alice", "password": "alice123"})
check("alice 注册成功", st == 200 and r.get("ok"), r)
alice_tok = r.get("token")
st, r = api("/api/auth/register", "POST", {"username": "alice", "password": "alice123"})
check("重名注册被拒", st == 400 and "已存在" in str(r.get("error")), r)
st, r = api("/api/auth/register", "POST", {"username": "bob", "password": "bob12345"})
check("bob 注册成功", st == 200 and r.get("ok"), r)
bob_tok = r.get("token")
st, r = api("/api/auth/register", "POST", {"username": "x", "password": "x123456"})
check("用户名过短被拒", st == 400, r)
st, r = api("/api/auth/register", "POST", {"username": "root", "password": "root1234"})
check("保留词被拒", st == 400, r)
st, r = api("/api/auth/register", "POST", {"username": "admin", "password": "admin1234"})
check("普通用户不能注册保留词 admin", st == 400, r)
# 管理员账号本身可以用 admin（权限来自 is_admin 字段，不靠用户名）
ok, err = S.validate_username("admin")
check("普通校验仍拒绝 admin", not ok and "保留" in err, err)
ok, err = S.validate_username("admin", allow_reserved=True)
check("管理员校验放行 admin", ok, err)
st, r = api("/api/auth/register", "POST", {"username": "carol", "password": "123"})
check("弱密码被拒", st == 400, r)
st, r = api("/api/auth/login", "POST", {"username": "alice", "password": "wrongpass"})
check("错误密码登录被拒", st == 401, r)
st, r = api("/api/auth/login", "POST", {"username": "alice", "password": "alice123"})
check("正确密码登录成功", st == 200 and r.get("token"), r)
alice_tok = r.get("token")
st, r = api("/api/auth/me", user_token=alice_tok)
check("auth/me 返回用户信息", st == 200 and r["user"]["username"] == "alice", r)
st, r = api("/api/auth/me")
check("未登录 auth/me 被拒", st == 401, r)
st, r = api("/api/auth/logout", "POST", {}, user_token=alice_tok)
check("登出成功", st == 200, r)
st, r = api("/api/auth/me", user_token=alice_tok)
check("登出后 token 失效", st == 401, r)
# 重新登录拿 token
st, r = api("/api/auth/login", "POST", {"username": "alice", "password": "alice123"})
alice_tok = r.get("token")

print("\n=== ② 矿机端首次登录自动挂载 ===")
# 造两台矿机
now = time.time()
S.db().execute(
    "INSERT INTO rigs(name,token,first_seen,last_seen,status,wallet,display_name) "
    "VALUES(?,?,?,?,?,?,?)", ("rig-a", "tok-a", now, now, "online", "wAlice", "rig-a"))
S.db().execute(
    "INSERT INTO rigs(name,token,first_seen,last_seen,status,wallet,display_name) "
    "VALUES(?,?,?,?,?,?,?)", ("rig-b", "tok-b", now, now, "online", "wBob", "rig-b"))
S.db().commit()

st, r = api("/api/rig_login", "POST",
            {"rig": "rig-a", "username": "alice", "password": "alice123"},
            rig_token="tok-a")
check("rig-a 首次登录成功", st == 200 and r.get("ok"), r)
check("rig-a 自动挂载到 alice", r.get("rig", {}).get("owner_id") is not None, r)
owner = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-a'").fetchone()["owner_id"]
alice_uid = S.user_by_name("alice")["id"]
check("DB 中 owner_id = alice", owner == alice_uid, "owner=%s alice=%s" % (owner, alice_uid))
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-a", "username": "alice", "password": "alice123"},
            rig_token="tok-a")
check("重复登录幂等（不报错）", st == 200 and r.get("ok"), r)
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-a", "username": "bob", "password": "bob12345"},
            rig_token="tok-a")
check("他人登录已绑定矿机 → 409", st == 409, r)
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-a", "username": "alice", "password": "wrongpass"},
            rig_token="tok-a")
check("密码错误 → 401", st == 401, r)
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-a", "username": "alice", "password": "alice123"},
            rig_token="wrong-token")
check("机器接入码错误 → 401", st == 401, r)
# bob 绑自己的
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-b", "username": "bob", "password": "bob12345"},
            rig_token="tok-b")
check("rig-b 挂载到 bob", st == 200 and r.get("ok"), r)

print("\n=== ③ 归属隔离 ===")
st, r = api("/api/fleet", user_token=alice_tok)
names = [x["name"] for x in r.get("rigs", [])]
check("alice 只看到 rig-a", names == ["rig-a"], names)
check("alice 的 total 只统计自己", r["total"]["online"] == 1, r["total"])
st, r = api("/api/fleet", user_token=bob_tok)
names = [x["name"] for x in r.get("rigs", [])]
check("bob 只看到 rig-b", names == ["rig-b"], names)
# 钱包明文：自己的钱包能看到（D3）
st, r = api("/api/fleet", user_token=alice_tok)
w = (r["rigs"][0].get("wallet") or {})
check("alice 能看到自己矿机钱包地址明文",
      (w.get("wallet") == "wAlice") or (r["rigs"][0].get("snapshot") or {}).get("wallet") == "wAlice",
      w)
check("alice 的 total 里没有 bob 的钱包",
      "wBob" not in json.dumps(r.get("total"), ensure_ascii=False), r.get("total"))
# 告警隔离
S.add_alert("rig-a", "warn", "test", "A 的告警")
S.add_alert("rig-b", "warn", "test", "B 的告警")
st, r = api("/api/alerts", user_token=alice_tok)
msgs = [a["msg"] for a in r.get("alerts", [])]
check("alice 只看到自己矿机告警", "A 的告警" in msgs and "B 的告警" not in msgs, msgs)
st, r = api("/api/alerts", user_token=bob_tok)
msgs = [a["msg"] for a in r.get("alerts", [])]
check("bob 只看到自己矿机告警", "B 的告警" in msgs and "A 的告警" not in msgs, msgs)
# 历史隔离
st, r = api("/api/history?rig=rig-b", user_token=alice_tok)
check("alice 查 bob 历史 → 403", st == 403, r)
st, r = api("/api/history?rig=rig-a", user_token=alice_tok)
check("alice 查自己历史 → 200", st == 200, r)
# daily 隔离：给两台都插样本
S.db().execute("INSERT INTO samples(rig,ts,ths,power,temp,fan,accepted,rejected,uptime,pool) "
               "VALUES('rig-a',?,100,200,60,30,1,0,100,'poolA')", (now,))
S.db().execute("INSERT INTO samples(rig,ts,ths,power,temp,fan,accepted,rejected,uptime,pool) "
               "VALUES('rig-b',?,500,900,70,40,5,0,200,'poolB')", (now,))
S.db().commit()
st, r = api("/api/daily", user_token=alice_tok)
d_rigs = set()
for d in r.get("days", []):
    for e in d.get("rigs", []):
        d_rigs.add(e["rig"])
check("alice 的 daily 只含 rig-a", d_rigs == {"rig-a"}, d_rigs)
st, r = api("/api/daily", user_token=bob_tok)
d_rigs = set()
for d in r.get("days", []):
    for e in d.get("rigs", []):
        d_rigs.add(e["rig"])
check("bob 的 daily 只含 rig-b", d_rigs == {"rig-b"}, d_rigs)
# 命令隔离
st, r = api("/api/command", "POST", {"rig": "rig-b", "cmd": "restart_miner"}, user_token=alice_tok)
check("alice 命令 bob 的矿机 → 403", st == 403, r)
st, r = api("/api/command", "POST", {"rig": "rig-a", "cmd": "restart_miner"}, user_token=alice_tok)
check("alice 命令自己的矿机 → 200", st == 200 and r.get("targets") == ["rig-a"], r)
st, r = api("/api/command", "POST", {"rig": "*", "cmd": "restart_miner"}, user_token=alice_tok)
check("alice 广播只作用到自己", r.get("targets") == ["rig-a"], r)
st, r = api("/api/command", "POST", {"rig": "*", "cmd": "restart_miner"}, user_token=bob_tok)
check("bob 广播只作用到自己", r.get("targets") == ["rig-b"], r)
# wallet 隔离
st, r = api("/api/wallet", "POST", {"rig": "rig-b", "wallet": "hack"}, user_token=alice_tok)
check("alice 改 bob 钱包 → 403", st == 403, r)
# reset token 隔离
st, r = api("/api/rig_reset_token", "POST", {"rig": "rig-b"}, user_token=alice_tok)
check("alice 重置 bob 接入码 → 403", st == 403, r)

print("\n=== ④ admin 视图；归属只由矿机端首次登录产生 ===")
st, r = api("/api/fleet", admin_token=S._admin_token)
names = [x["name"] for x in r.get("rigs", [])]
check("admin 看到全部矿机", set(names) == {"rig-a", "rig-b"}, names)
check("admin 的 total 统计全部", r["total"]["online"] == 2, r["total"])
st, r = api("/api/users", admin_token=S._admin_token)
check("admin 能看用户列表", st == 200 and len(r.get("users", [])) == 3, r)
st, r = api("/api/users", user_token=alice_tok)
check("普通用户看用户列表 → 403", st == 403, r)

# ★ 新语义：不再有 admin 手工指定归属的接口
st, r = api("/api/rig_claim", "POST", {"username": "alice", "rigs": ["rig-b"]},
            admin_token=S._admin_token)
check("认领接口已移除 → 404", st == 404, st)
st, r = api("/api/rig_bind", "POST", {"rig": "rig-b", "username": "alice"},
            admin_token=S._admin_token)
check("admin 手动挂载接口已移除 → 404", st == 404, st)
st, r = api("/api/rig_rebind", "POST", {"rig": "rig-b", "username": "alice"},
            admin_token=S._admin_token)
check("换绑接口已移除 → 404", st == 404, st)
st, r = api("/api/unclaimed_rigs", admin_token=S._admin_token)
check("未挂载列表接口已移除 → 404", st == 404, st)

# 归属转移的唯一合法路径：原属主解绑 → 变无主 → 新用户在矿机端登录绑定
st, r = api("/api/rig_unbind", "POST", {"rig": "rig-b"}, user_token=bob_tok)
check("bob 解绑自己的矿机成功", st == 200 and r.get("ok"), r)
owner = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-b'").fetchone()["owner_id"]
check("rig-b 已变回无主", int(owner or 0) == 0, owner)
st, r = api("/api/fleet", user_token=bob_tok)
check("bob 看不到已解绑的矿机", len(r.get("rigs", [])) == 0, r)
st, r = api("/api/fleet", user_token=alice_tok)
check("alice 看不到无主矿机（不共享）", len(r.get("rigs", [])) == 1, r)
# alice 在矿机端登录 → 自动绑定
st, r = api("/api/rig_login", "POST",
            {"rig": "rig-b", "username": "alice", "password": "alice123"},
            rig_token="tok-b")
check("矿机端登录自动绑定成功", st == 200 and r.get("ok"), r)
owner = S.db().execute("SELECT owner_id FROM rigs WHERE name='rig-b'").fetchone()["owner_id"]
check("rig-b 归属已转给 alice", owner == alice_uid, owner)
st, r = api("/api/fleet", user_token=alice_tok)
names = [x["name"] for x in r.get("rigs", [])]
check("绑定后 alice 看到两台", set(names) == {"rig-a", "rig-b"}, names)
st, r = api("/api/fleet", user_token=bob_tok)
check("绑定后 bob 看不到矿机", len(r.get("rigs", [])) == 0, r)
# 审计
st, r = api("/api/owner_log", admin_token=S._admin_token)
acts = [x["action"] for x in r.get("log", [])]
check("归属变更有审计记录", "bind" in acts and "unbind" in acts, acts)
check("审计里没有 admin_force（无手工指定归属）", "admin_force" not in acts, acts)

print("\n=== ⑤ 改名只改 display_name ===")
st, r = api("/api/rig_rename", "POST", {"rig": "rig-a", "display_name": "客厅矿机"},
            user_token=alice_tok)
check("改名成功", st == 200 and r.get("display_name") == "客厅矿机", r)
row = S.db().execute("SELECT name,display_name FROM rigs WHERE name='rig-a'").fetchone()
check("name 未变", row["name"] == "rig-a", row)
check("display_name 已更新", row["display_name"] == "客厅矿机", row)
cmd = S.db().execute("SELECT cmd,arg FROM commands WHERE rig='rig-a' AND cmd='set_display_name' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
check("已下发 set_display_name 命令", cmd is not None, cmd)
st, r = api("/api/rig_rename", "POST", {"rig": "rig-a", "display_name": ""}, user_token=alice_tok)
check("空显示名被拒", st == 400, r)
st, r = api("/api/rig_rename", "POST", {"rig": "rig-a", "display_name": "x" * 40}, user_token=alice_tok)
check("超长显示名被拒", st == 400, r)

print("\n=== ⑥ 鉴权矩阵：未登录全部拒绝 ===")
for path, method in [("/api/fleet", "GET"), ("/api/history?rig=rig-a", "GET"),
                      ("/api/alerts", "GET"), ("/api/daily", "GET"),
                      ("/api/users", "GET"), ("/api/rigs", "GET"),
                      ("/api/command", "POST"), ("/api/wallet", "POST"),
                      ("/api/rig_rename", "POST"), ("/api/rig_reset_token", "POST")]:
    st, r = api(path, method, {} if method == "POST" else None)
    check("未登录 %s 被拒" % path, st == 401, "%s %s" % (st, r))
# 机器域不受影响
st, r = api("/api/report", "POST", {"name": "rig-a", "token": "tok-a", "ths": 1,
                                     "gpus": [], "miners": [{"ths": 1}]})
check("机器域 /api/report 免登录可用", st == 200 and r.get("ok"), r)

print("\n=== ⑦ 表结构幂等 + 迁移不破坏历史 ===")
before_samples = S.db().execute("SELECT COUNT(*) n FROM samples").fetchone()["n"]
before_rigs = S.db().execute("SELECT COUNT(*) n FROM rigs").fetchone()["n"]
before_cmds = S.db().execute("SELECT COUNT(*) n FROM commands").fetchone()["n"]
before_alerts = S.db().execute("SELECT COUNT(*) n FROM alerts").fetchone()["n"]
S.init_db()
S.init_db()
check("重复 init_db 无异常", True)
check("samples 行数不变", S.db().execute("SELECT COUNT(*) n FROM samples").fetchone()["n"] == before_samples)
check("rigs 行数不变", S.db().execute("SELECT COUNT(*) n FROM rigs").fetchone()["n"] == before_rigs)
check("commands 行数不变", S.db().execute("SELECT COUNT(*) n FROM commands").fetchone()["n"] == before_cmds)
check("alerts 行数不变", S.db().execute("SELECT COUNT(*) n FROM alerts").fetchone()["n"] == before_alerts)
# 样本仍按 name 关联（零断裂）
n = S.db().execute("SELECT COUNT(*) n FROM samples s JOIN rigs r ON s.rig=r.name").fetchone()["n"]
check("samples 全部仍能关联 rigs（零断裂）", n == before_samples, "%s vs %s" % (n, before_samples))

print("\n=== ⑧ D7 客户端只读约束（服务端不提供改 server/token 的接口）===")
st, r = api("/api/rig_set_server", "POST", {"server": "http://evil"}, user_token=alice_tok)
check("不存在改中控地址的接口", st == 404, r)

_httpd.shutdown()
shutil.rmtree(_tmpdir, ignore_errors=True)

print("\n" + "=" * 60)
print("PASS %d / FAIL %d" % (len(PASSES), len(FAILS)))
if FAILS:
    print("\n失败项：")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
