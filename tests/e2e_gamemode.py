# -*- coding: utf-8 -*-
"""Game Mode (游戏模式) end-to-end test against local minrigs server."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:18099"
ADMIN = os.environ.get("MINRIGS_ADMIN_TOKEN", "adm_REPLACE_ME")
RIG = "gametest-rig"
TOKEN = "tok-e2e-123"

# 本机系统代理会劫持 127.0.0.1，强制直连
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def req(path, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with opener.open(r, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def report(gm):
    return req("/api/report", "POST", headers={"X-Rig-Token": TOKEN}, payload={
        "name": RIG, "token": TOKEN, "hostname": "e2e-host", "ts": time.time(),
        "game_mode": gm, "game_proc": "dota2.exe" if gm else None,
        "gpus": [{"index": 0, "model": "NVIDIA GeForce RTX 4070", "temp": 66,
                  "fan": 70, "power": 180}],
        "miners": [{"type": "srbminer", "alive": not gm, "ths": 0 if gm else 121.5,
                    "accepted": 0 if gm else 500, "rejected": 0, "uptime": 100}],
    })


fails = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


# 1) 首次上报（非游戏模式）→ 自动注册
check("report#1 normal", report(False).get("ok") is True)

# 2) 上报 game_mode=true → 进入游戏模式 + 产生 info 告警
check("report#2 gamemode on", report(True).get("ok") is True)
alerts = req("/api/alerts", headers={"X-Admin-Token": ADMIN})["alerts"]
gm_alerts = [a for a in alerts if a["rig"] == RIG and a["kind"] == "gamemode" and not a["resolved"]]
check("alert gamemode created", len(gm_alerts) == 1, gm_alerts)
check("alert mentions game proc", gm_alerts and "dota2.exe" in gm_alerts[0]["msg"], gm_alerts[0]["msg"] if gm_alerts else "-")

# 3) fleet 聚合：game_mode 标记 + total.gamemode 计数 + snapshot.game_proc
fleet = req("/api/fleet", headers={"X-Admin-Token": ADMIN})
rig0 = next((r for r in fleet["rigs"] if r["name"] == RIG), None)
check("fleet rig.game_mode", rig0 is not None and rig0["game_mode"] is True)
check("fleet total.gamemode", fleet["total"].get("gamemode") == 1, fleet["total"])
check("fleet snapshot.game_proc", rig0 and rig0["snapshot"].get("game_proc") == "dota2.exe",
      rig0["snapshot"].get("game_proc") if rig0 else "-")

# 4) 下发 gamemode_off → 矿机轮询取到 → 回执 → 状态 sent/done
check("command dispatch", req("/api/command", "POST", headers={"X-Admin-Token": ADMIN},
                              payload={"rig": RIG, "cmd": "gamemode_off"}).get("ok") is True)
poll = req("/api/poll?name=" + RIG, headers={"X-Rig-Token": TOKEN})
cmds = poll.get("commands") or []
check("poll receives gamemode_off", len(cmds) == 1 and cmds[0]["cmd"] == "gamemode_off", cmds)
check("cmd_result", req("/api/cmd_result", "POST", headers={"X-Rig-Token": TOKEN},
                        payload={"rig": RIG, "id": cmds[0]["id"], "ok": True,
                                 "result": "游戏模式已关闭"}).get("ok") is True)
poll2 = req("/api/poll?name=" + RIG, headers={"X-Rig-Token": TOKEN})
check("poll consumed (no repeat)", not (poll2.get("commands") or []), poll2)

# 5) 上报 game_mode=false → 告警自动消除
check("report#3 gamemode off", report(False).get("ok") is True)
alerts = req("/api/alerts", headers={"X-Admin-Token": ADMIN})["alerts"]
open_gm = [a for a in alerts if a["rig"] == RIG and a["kind"] == "gamemode" and not a["resolved"]]
check("alert gamemode resolved", len(open_gm) == 0)

# 6) 错误口令被拒
try:
    bad = req("/api/report", "POST", headers={"X-Rig-Token": "wrong"}, payload={
        "name": RIG, "token": "wrong", "game_mode": True, "gpus": [], "miners": []})
    check("wrong token rejected", bad.get("ok") is False, bad)
except Exception as e:  # noqa: BLE001
    check("wrong token rejected", "401" in str(e) or "token" in str(e), e)

# 7) /api/daily 按天收益估算
daily = req("/api/daily?days=7", headers={"X-Admin-Token": ADMIN})
check("daily ok", daily.get("ok") is True and isinstance(daily.get("days"), list), len(daily.get("days", [])))
# 刚才 report#1/#3 上报过 ths=121.5 的样本
check("daily has data", len(daily["days"]) >= 1 and daily["days"][0]["rigs"], daily["days"][:1])

# 8) /api/kernels 结构 + kernel_dl（有版本信息→返回 zip；无→502，两者都算通过）
kn = req("/api/kernels", headers={"X-Admin-Token": ADMIN})
check("kernels ok", kn.get("ok") is True and "catalog" in kn and "cached" in kn)
try:
    r2 = urllib.request.Request(BASE + "/api/kernel_dl?kernel=srbminer&rig=" + urllib.parse.quote(RIG),
                                headers={"X-Rig-Token": TOKEN})
    with opener.open(r2, timeout=600) as resp:
        head = resp.read(4)
    check("kernel_dl zip delivered", head == b"PK\x03\x04", head)
except urllib.error.HTTPError as e:
    check("kernel_dl zip delivered", e.code == 502, "HTTP %d（无版本信息）" % e.code)

# 8b) 未注册矿机带 rig 参数下载 → 自动注册后放行
NRIG, NTOK = "kdl-autoreg", "tok-autoreg-001"
try:
    r3 = urllib.request.Request(BASE + "/api/kernel_dl?kernel=srbminer&rig=" + NRIG,
                                headers={"X-Rig-Token": NTOK})
    with opener.open(r3, timeout=600) as resp:
        head = resp.read(4)
    check("kernel_dl auto-register", head == b"PK\x03\x04", head)
except urllib.error.HTTPError as e:
    check("kernel_dl auto-register", e.code == 502, "HTTP %d" % e.code)
# 8c) 错误口令被拒
try:
    r4 = urllib.request.Request(BASE + "/api/kernel_dl?kernel=srbminer&rig=" + RIG,
                                headers={"X-Rig-Token": "WRONG"})
    opener.open(r4, timeout=30)
    check("kernel_dl wrong token 401", False, "expected 401")
except urllib.error.HTTPError as e:
    check("kernel_dl wrong token 401", e.code == 401, e.code)

# 9) 批量命令广播 rig="*"
check("broadcast dispatch", req("/api/command", "POST", headers={"X-Admin-Token": ADMIN},
                                payload={"rig": "*", "cmd": "gamemode_on"}).get("ok") is True)
poll3 = req("/api/poll?name=" + RIG, headers={"X-Rig-Token": TOKEN})
cmds3 = poll3.get("commands") or []
check("broadcast received", any(c["cmd"] == "gamemode_on" for c in cmds3), cmds3)

print("== E2E %s ==" % ("PASSED" if not fails else "FAILED: %s" % fails))
sys.exit(0 if not fails else 1)
