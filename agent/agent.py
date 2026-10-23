# -*- coding: utf-8 -*-
"""
挖矿群控 · 采集端 Agent（纯标准库，零依赖，装在每台矿机上）

安装:
  1. 把 agent 目录整个拷到矿机，编辑 config.json:
     {
       "server":  "http://192.168.50.10:8099",   // 中控地址
       "token":   "rig-token-xxx",                // 与中控 rigs 表一致的口令
       "name":    "rig-4090-1",                   // 矿机名（全局唯一）
       "interval": 30,                            // 上报间隔秒
       "miners": [
         {
           "type": "srbminer",                    // srbminer | bzminer
           "api":  "http://127.0.0.1:21550",      // SRBMiner 需 --api-enable --api-port
           "process": "SRBMiner-MULTI.exe",       // 远程重启时 taskkill 的进程名
           "restart_cmd": "C:\\miners\\SRBMiner\\start.bat",
           "cwd": "C:\\miners\\SRBMiner"
         }
       ]
     }
  2. python agent.py   （建议注册成计划任务/服务开机自启）

职责:
  - 每 interval 秒采集 nvidia-smi GPU 指标 + 矿工 API（算力/份额/在线时长）
  - POST /api/report 上报中控
  - GET /api/poll 轮询远程命令，当前支持 restart_miner（杀进程 + 拉起 start 脚本）
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
LOG = os.path.join(BASE, "agent.log")

DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW

UNIT_THS = {"H": 1e-12, "K": 1e-9, "M": 1e-6, "G": 1e-3, "T": 1.0, "": 1e-12}


def log(msg):
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def http_json(url, timeout=8, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"User-Agent": "minrigs-agent/1.0"}
    if payload is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body)


# ------------------------------------------------------------ GPU 采集

def query_gpus():
    """nvidia-smi 采集。AMD 卡走 rocm-smi 的场景后续再扩。"""
    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,temperature.gpu,utilization.gpu,"
        "memory.used,memory.total,fan.speed,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=10).stdout.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log("nvidia-smi 失败: %s" % e)
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue
        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0
        gpus.append({
            "index": int(parts[0] or 0),
            "model": parts[1],
            "temp": _f(parts[2]),
            "util": _f(parts[3]),
            "mem_used": _f(parts[4]),
            "mem_total": _f(parts[5]),
            "fan": _f(parts[6]),
            "power": _f(parts[7]),
        })
    return gpus


# ------------------------------------------------------------ 矿工解析

def _to_ths(v):
    """'324.5 TH/s' / 324500000000 (H/s) / {'val':..} -> TH/s"""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v) * UNIT_THS[""]          # 裸数字按 H/s 处理
    if isinstance(v, dict):
        for k in ("value", "val", "hashrate", "total"):
            if k in v:
                return _to_ths(v[k])
        return 0.0
    m = re.search(r"([\d.]+)\s*([HKGTM])?H/s", str(v), re.I)
    if m:
        val = float(m.group(1))
        unit = (m.group(2) or "").upper()
        return val * UNIT_THS.get(unit, UNIT_THS[""])
    try:
        return float(v) * UNIT_THS[""]
    except (TypeError, ValueError):
        return 0.0


def _walk(obj, path=""):
    """深度优先产出 (路径, key, value)，供启发式字段提取。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("%s.%s" % (path, k), k, v)
            yield from _walk(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:16]):
            yield from _walk(v, "%s[%d]" % (path, i))


def parse_srbminer(data):
    """SRBMiner-MULTI: GET 根路径返回 JSON。字段名随版本浮动，这里启发式提取。"""
    m = {"type": "srbminer", "ths": 0.0, "accepted": 0, "rejected": 0,
         "uptime": None, "algo": None, "pool": None}
    for path, k, v in _walk(data):
        kl = k.lower()
        pl = path.lower()
        if m["ths"] == 0 and (re.search(r"(total)?hashrate|performance", kl)
                              or "hashrate" in pl or "hash_rate" in pl) \
                and not isinstance(v, (list, dict)) and kl not in ("hashrate_unit",):
            t = _to_ths(v)
            if t > 0:
                m["ths"] = round(t, 3)
        if isinstance(v, (int, float)):
            if "accepted" in kl and "share" in kl or kl == "accepted":
                m["accepted"] += int(v)
            elif "rejected" in kl:
                m["rejected"] += int(v)
            elif kl == "uptime" and m["uptime"] is None:
                m["uptime"] = int(v)
        elif isinstance(v, str):
            if m["algo"] is None and kl in ("algorithm", "algo"):
                m["algo"] = v
            if m["pool"] is None and kl in ("pool", "pool_url", "url") and ":" in v:
                m["pool"] = v
    m["raw"] = json.dumps(data, ensure_ascii=False)[:3000]
    return m


def parse_bzminer(data):
    """BzMiner 1.x: GET /api/snapshot -> devices/pools/hashrate/shares。"""
    m = {"type": "bzminer", "ths": 0.0, "accepted": 0, "rejected": 0,
         "uptime": None, "algo": None, "pool": None}
    for path, k, v in _walk(data):
        kl = k.lower()
        pl = path.lower()
        if m["ths"] == 0 and ("hashrate" in kl or "hash_rate" in kl or "hashrate" in pl) \
                and not isinstance(v, (list, dict)):
            t = _to_ths(v)
            if t > 0:
                m["ths"] = round(t, 3)
        if isinstance(v, (int, float)):
            if "accepted" in kl:
                m["accepted"] += int(v)
            elif "rejected" in kl:
                m["rejected"] += int(v)
            elif kl in ("uptime", "session_uptime", "run_time") and m["uptime"] is None:
                m["uptime"] = int(v)
        elif isinstance(v, str):
            if m["algo"] is None and kl in ("algorithm", "algo", "algo_name"):
                m["algo"] = v
            if m["pool"] is None and ("pool" in kl and "url" in kl or kl == "url") and ":" in v:
                m["pool"] = v
    m["raw"] = json.dumps(data, ensure_ascii=False)[:3000]
    return m


PARSERS = {"srbminer": parse_srbminer, "bzminer": parse_bzminer}


def collect_miners(miners_cfg):
    out = []
    for mc in miners_cfg or []:
        mtype = (mc.get("type") or "").lower()
        parser = PARSERS.get(mtype)
        entry = {"type": mtype, "api": mc.get("api"), "alive": False,
                 "ths": 0.0, "accepted": 0, "rejected": 0, "error": None}
        if not parser:
            entry["error"] = "未知矿工类型"
            out.append(entry)
            continue
        try:
            base = mc["api"].rstrip("/")
            url = base + "/api/snapshot" if mtype == "bzminer" else base + "/"
            data = http_json(url, timeout=6)
            parsed = parser(data)
            entry.update(parsed)
            entry["alive"] = True
        except Exception as e:  # noqa: BLE001
            entry["error"] = "API 无响应: %s" % e.__class__.__name__
        out.append(entry)
    return out


# ------------------------------------------------------------ 命令执行

def exec_command(cmd, arg, miners_cfg):
    """当前支持 restart_miner：按 arg.miner_type 或第一个矿工配置重启。"""
    if cmd != "restart_miner":
        return "不支持的命令: %s" % cmd
    mtype = (arg or {}).get("miner_type") or ""
    mcfg = None
    for mc in miners_cfg or []:
        if (mc.get("type") or "").lower() == mtype:
            mcfg = mc
            break
    if mcfg is None and miners_cfg:
        mcfg = miners_cfg[0]
    if not mcfg:
        return "config.json 未配置 miners"
    proc = mcfg.get("process")
    if proc:
        r = subprocess.run(["taskkill", "/F", "/IM", proc], capture_output=True, timeout=20)
        log("taskkill %s -> rc=%s" % (proc, r.returncode))
    time.sleep(2)
    flags = DETACHED if sys.platform == "win32" else 0
    subprocess.Popen(mcfg["restart_cmd"], cwd=mcfg.get("cwd"),
                     shell=True, creationflags=flags)
    log("已拉起矿工: %s" % mcfg.get("restart_cmd"))
    return "已杀进程并重新拉起 %s" % mcfg.get("restart_cmd")


# ------------------------------------------------------------ 主循环

def main():
    if not os.path.isfile(CONFIG):
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump({
                "server": "http://192.168.50.10:8099",
                "token": "change-me",
                "name": socket.gethostname().lower(),
                "interval": 30,
                "miners": [],
            }, f, ensure_ascii=False, indent=2)
        log("已生成默认配置 %s，请编辑后重新运行" % CONFIG)
        return
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    srv, token, name = cfg["server"].rstrip("/"), cfg["token"], cfg["name"]
    interval = int(cfg.get("interval") or 30)
    miners_cfg = cfg.get("miners") or []
    log("agent 启动 name=%s -> %s  interval=%ds" % (name, srv, interval))

    while True:
        gpus = query_gpus()
        miners = collect_miners(miners_cfg)
        payload = {
            "name": name, "token": token,
            "hostname": socket.gethostname(),
            "ts": time.time(),
            "gpus": gpus, "miners": miners,
        }
        try:
            resp = http_json(srv + "/api/report", timeout=15, method="POST",
                             payload=payload)
            if not resp.get("ok"):
                log("上报被拒: %s" % resp.get("error"))
        except Exception as e:  # noqa: BLE001
            log("上报失败: %s" % e)

        # 拉取并执行命令
        try:
            resp = http_json(srv + "/api/poll?name=%s" % name, timeout=10,
                             headers={"X-Rig-Token": token})
            for c in resp.get("commands") or []:
                log("执行命令 #%s %s" % (c["id"], c["cmd"]))
                try:
                    arg = json.loads(c.get("arg") or "{}")
                except ValueError:
                    arg = {}
                ok, result = True, ""
                try:
                    result = exec_command(c["cmd"], arg, miners_cfg)
                except Exception as e:  # noqa: BLE001
                    ok, result = False, "执行异常: %s" % e
                try:
                    http_json(srv + "/api/cmd_result", timeout=10, method="POST",
                              payload={"rig": name, "id": c["id"],
                                       "ok": ok, "result": result},
                              headers={"X-Rig-Token": token})
                except Exception as e:  # noqa: BLE001
                    log("回执失败: %s" % e)
        except Exception as e:  # noqa: BLE001
            log("拉取命令失败: %s" % e)

        time.sleep(interval)


if __name__ == "__main__":
    main()
