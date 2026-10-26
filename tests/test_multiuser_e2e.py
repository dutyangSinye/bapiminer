# -*- coding: utf-8 -*-
"""客户端 ↔ 中控 多用户流程端到端测试。

覆盖：
  ① 客户端 rig_login → 中控自动挂载
  ② 中控改名 → 客户端收到 set_display_name 命令 → 静默保存（不弹窗）
  ③ 客户端改名 → 随 report 同步到中控 display_name（name 不变）
  ④ D7：客户端 set_field 拒绝写 server / token / rig_name
  ⑤ D6：改名事件不产生弹窗类事件（只有 display_name + log）
  ⑥ 客户端改名后中控仍在线（不掉线）

不启动真实矿工进程（用假引擎），跑在临时库上。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "client"))

FAILS = []
PASSES = []


def check(name, cond, extra=""):
    if cond:
        PASSES.append(name)
        print("  PASS  %s" % name)
    else:
        FAILS.append("%s %s" % (name, extra))
        print("  FAIL  %s %s" % (name, extra))


# ---------------------------------------------------------------- 中控服务端
_tmp = tempfile.mkdtemp(prefix="minrigs_e2e_")
import server as S  # noqa: E402

S.DATA_DIR = _tmp
S.DB_FILE = os.path.join(_tmp, "test.db")
S.CONFIG_FILE = os.path.join(_tmp, "config.json")
# ★ 隔离断言：绝不能连到真实生产库
assert os.path.abspath(S.DB_FILE).startswith(os.path.abspath(_tmp)), \
    "测试未隔离！DB_FILE=%s" % S.DB_FILE
assert os.path.abspath(S.DB_FILE) != os.path.abspath(
    os.path.join(ROOT, "server", "data", "fleet.db")), "测试连到了生产库！"
S._load_config()
S.init_db()
S._insert_user("boss", "boss123456", is_admin=1)

PORT = 18901
S.PORT = PORT
from http.server import ThreadingHTTPServer  # noqa: E402
import threading  # noqa: E402

_httpd = ThreadingHTTPServer(("127.0.0.1", PORT), S.Handler)
_httpd.daemon_threads = True
threading.Thread(target=_httpd.serve_forever, daemon=True).start()
time.sleep(0.4)


def srv(path, method="GET", body=None, headers=None):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        if v is not None:
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


# ---------------------------------------------------------------- 客户端
import miner_client as MC  # noqa: E402

MC.CONFIG_FILE = os.path.join(_tmp, "client_config.json")
MC.CLIENT_VERSION = "1.13.0.0-test"

cfg = {
    "kernel": "SRBMiner-MULTI",
    "kernel_dir": os.path.join(_tmp, "kernels"),
    "algo": "PRL (Pearl)",
    "pool_url": "stratum+tcp://prl-hk.kryptex.network:7048",
    "wallet": "prl1test",
    "worker": "testrig",
    "autostart": False,
    "autorestart": True,
    "gamemode": True,
    "games": [],
    "ignore_miners": [],
    "check_other_miners": False,
    "auto_update": False,
    "server": "http://127.0.0.1:%d" % PORT,
    "token": "rig-e2etoken",
    "rig_name": "e2e-rig",
    "display_name": "",
}
with open(MC.CONFIG_FILE, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

import queue  # noqa: E402

q = queue.Queue()
engine = MC.MiningEngine(cfg, q)

# 让引擎不真挖矿：只跑命令处理与上报
events = []


def fake_emit(event=None, **kw):
    events.append((event, kw))


engine.emit = fake_emit
engine.cfg = cfg


# 生产里 run_gui() 会注入 engine.persist = persist_cfg（把运行中的变更落盘）。
# 测试里同样接上，否则改名/换池这类「改了要落盘」的行为测不到。
def _persist():
    with open(MC.CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


engine.persist = _persist


def drain():
    while not q.empty():
        ev = q.get()
        events.append((ev.get("event"), ev))


# 先让矿机上报一次（服务端注册它）
payload = {
    "name": "e2e-rig", "token": "rig-e2etoken",
    "display_name": "e2e-rig",
    "client_version": "1.13.0.0-test",
    "pool_url": cfg["pool_url"], "wallet": cfg["wallet"],
    "gpus": [{"index": 0, "model": "NVIDIA GeForce RTX 5080", "temp": 50,
              "fan": 40, "power": 200, "ths": 1.0}],
    "miners": [{"type": "srbminer", "ths": 1.0, "accepted": 10, "rejected": 0,
                "uptime": 100, "alive": True, "pool": cfg["pool_url"]}],
}
st, r = srv("/api/report", "POST", payload)
check("客户端上报成功（矿机注册）", st == 200 and r.get("ok"), r)

print("\n=== ① 矿机端登录 → 自动挂载 ===")
# 先注册矿机现场操作员账号（矿机端注册 → 登录的同一条路径）
st, r = srv("/api/auth/register", "POST",
            {"username": "operator", "password": "oper12345"})
check("矿机端注册账号成功", st == 200 and r.get("ok"), r)
st, r = srv("/api/rig_login", "POST",
            {"rig": "e2e-rig", "username": "operator", "password": "oper12345"},
            {"X-Rig-Token": "rig-e2etoken"})
check("rig_login 成功", st == 200 and r.get("ok"), r)
check("返回 token", bool(r.get("token")), r)
check("返回用户信息", (r.get("user") or {}).get("username") == "operator", r)
client_user_token = r.get("token")
row = S.db().execute("SELECT owner_id FROM rigs WHERE name='e2e-rig'").fetchone()
check("本机已自动挂载到该账号", row["owner_id"] > 0, "owner_id=%s" % row["owner_id"])

# 客户端落盘登录态
cfg["user_token"] = client_user_token
cfg["username"] = "operator"
cfg["user_id"] = (r.get("user") or {}).get("id")

print("\n=== ② 中控改名 → 客户端静默保存 ===")
st, r = srv("/api/rig_rename", "POST",
            {"rig": "e2e-rig", "display_name": "客厅矿机"},
            {"X-User-Token": client_user_token})
check("中控改名成功", st == 200 and r.get("display_name") == "客厅矿机", r)
# 客户端拉命令
st, r = srv("/api/poll?name=e2e-rig", headers={"X-Rig-Token": "rig-e2etoken"})
cmds = r.get("commands") or []
check("客户端拉到 set_display_name 命令",
      any(c["cmd"] == "set_display_name" for c in cmds), cmds)
for c in cmds:
    arg = c.get("arg")
    if isinstance(arg, str):
        arg = json.loads(arg)
    ok, note = engine._handle_cmd(c["cmd"], arg or {})
    if c["cmd"] == "set_display_name":
        check("客户端执行改名成功", ok, note)
check("客户端本地已保存新显示名", cfg.get("display_name") == "客厅矿机", cfg.get("display_name"))
# D6：不产生弹窗类事件
kinds = [e[0] for e in events]
check("改名只产生 display_name + log 事件（无弹窗）",
      "display_name" in kinds and "toast" not in kinds and "confirm" not in kinds, kinds)
# 落盘验证
saved = json.load(open(MC.CONFIG_FILE, encoding="utf-8"))
check("改名已落盘到 client_config.json",
      saved.get("display_name") == "客厅矿机", saved.get("display_name"))

print("\n=== ③ 客户端改名 → report 同步（name 不变）===")
cfg["display_name"] = "书房矿机"
st, r = srv("/api/report", "POST", dict(payload, display_name="书房矿机"))
check("带 display_name 上报成功", st == 200 and r.get("ok"), r)
row = S.db().execute("SELECT name, display_name FROM rigs WHERE name='e2e-rig'").fetchone()
check("中控 display_name 已同步", row["display_name"] == "书房矿机", row)
check("机器标识 name 未变", row["name"] == "e2e-rig", row)

print("\n=== ⑥ 改名后矿机仍在线（不掉线）===")
row = S.db().execute("SELECT status, last_seen FROM rigs WHERE name='e2e-rig'").fetchone()
check("矿机仍在线", row["status"] == "online", row)
st, r = srv("/api/fleet", headers={"X-User-Token": client_user_token})
check("中控看板能看到该矿机", len(r.get("rigs", [])) == 1, r)
check("看板显示的是 display_name",
      r["rigs"][0]["display_name"] == "书房矿机", r["rigs"][0])

print("\n=== ④ D7：客户端只读字段拒绝写入 ===")
# 模拟 set_field 的服务端判定逻辑（与 miner_client 中的 READONLY_FIELDS 一致）
import re as _re
src = open(os.path.join(ROOT, "client", "miner_client.py"), encoding="utf-8").read()
m = _re.search(r"READONLY_FIELDS\s*=\s*\(([^)]*)\)", src)
check("客户端存在 READONLY_FIELDS 白名单", m is not None)
if m:
    ro = [x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()]
    check("server/token/rig_name 均在只读名单", set(ro) == {"server", "token", "rig_name"}, ro)
# 客户端 HTML 上这三个框带 readonly
html = open(os.path.join(ROOT, "client", "web", "index.html"), encoding="utf-8").read()
for fid in ("server", "token", "rig_name"):
    mm = _re.search(r'id="%s"[^>]*' % fid, html)
    check("#%s 已标 readonly" % fid, mm is not None and "readonly" in mm.group(0),
          mm.group(0) if mm else "not found")
mm = _re.search(r'id="display_name"[^>]*', html)
check("#display_name 存在且可编辑", mm is not None and "readonly" not in mm.group(0),
      mm.group(0) if mm else "not found")
# JS 不再绑定这三个字段的输入事件
js = open(os.path.join(ROOT, "client", "web", "app.js"), encoding="utf-8").read()
check("app.js 不再 bindField 写 server/token/rig_name",
      not _re.search(r'bindField\("(server|token|rig_name)"', js),
      [x for x in _re.findall(r'bindField\("(\w+)"', js)])

print("\n=== ⑦ 命令白名单含 set_display_name ===")
check("服务端允许 set_display_name",
      '"set_display_name"' in open(os.path.join(ROOT, "server", "server.py"), encoding="utf-8").read())

_httpd.shutdown()
shutil.rmtree(_tmp, ignore_errors=True)

print("\n" + "=" * 60)
print("PASS %d / FAIL %d" % (len(PASSES), len(FAILS)))
if FAILS:
    print("\n失败项：")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("全部通过")
