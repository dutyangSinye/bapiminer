# -*- coding: utf-8 -*-
"""
扒皮矿工·矿机端 · 三步开挖客户端（Windows GUI）

对标 Vminer 的使用方式：选内核 -> 填钱包/选矿池 -> 开始挖。
内置：矿工进程守护（崩溃自动拉起）、GPU 监控（nvidia-smi）、
      群控上报（填中控地址即接入 minrigs 看板）、收益预估、开机自启。

命令行:
    python miner_client.py              启动 GUI
    python miner_client.py --selftest   无 GUI 自检（配置组装/解析/上报）
打包: pyinstaller --onefile --noconsole --name BaPiMiner miner_client.py
"""

import atexit
import ctypes
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, "frozen", False) \
    else os.path.dirname(sys.executable)
CONFIG_FILE = os.path.join(BASE, "client_config.json")
LOG_FILE = os.path.join(BASE, "client.log")

# ---------------------------------------------------------------- 预置数据

KERNELS = {
    "SRBMiner-MULTI": {
        "exe": "SRBMiner-MULTI.exe",
        "api_port": 21550,
        "api_type": "srbminer",
        "download": "https://www.srbminer.com/download.html",
        "label": "SRBMiner-MULTI",
        "key": "srbminer",
        "note": "多卡通用，Pearl 官方推荐内核",
    },
    "BzMiner": {
        "exe": "bzminer.exe",
        "api_port": 4014,
        "api_type": "bzminer",
        "download": "https://www.bzminer.com/",
        "label": "BzMiner",
        "key": "bzminer",
        "note": "低功耗优先，单卡省电",
    },
}

# PRL (Pearl) 矿池预置，费率为矿池公示值
POOLS = [
    # Kryptex（1% 费率，SSL 用同主机 8048 端口）
    {"name": "Kryptex · 全球 (1%)", "url": "stratum+tcp://prl.kryptex.network:7048"},
    {"name": "Kryptex · 香港 (1%)", "url": "stratum+tcp://prl-hk.kryptex.network:7048"},
    {"name": "Kryptex · 新加坡 (1%)", "url": "stratum+tcp://prl-sg.kryptex.network:7048"},
    {"name": "Kryptex · 欧洲 (1%)", "url": "stratum+tcp://prl-eu.kryptex.network:7048"},
    {"name": "Kryptex · 北美 (1%)", "url": "stratum+tcp://prl-us.kryptex.network:7048"},
    {"name": "Kryptex · 俄罗斯 (1%)", "url": "stratum+tcp://prl-ru.kryptex.network:7048"},
    {"name": "Kryptex · 中东 (1%)", "url": "stratum+tcp://prl-ae.kryptex.network:7048"},
    # F2Pool 飞鱼（1% PPLNS，用 F2Pool 账户名当钱包）
    {"name": "F2Pool 飞鱼 · 全球 (1%)", "url": "stratum+tcp://pearl.f2pool.com:5500"},
    # K1Pool（PPLNS 0% 费率）
    {"name": "K1Pool · 中国 (0%)", "url": "stratum+tcp://cn.pearl.k1pool.com:3360"},
    {"name": "K1Pool · 欧洲 (0%)", "url": "stratum+tcp://eu.pearl.k1pool.com:3360"},
    {"name": "K1Pool · 美国 (0%)", "url": "stratum+tcp://us.pearl.k1pool.com:3360"},
    {"name": "K1Pool SOLO · 欧洲 (1%)", "url": "stratum+tcp://eu.pearlsolo.k1pool.com:3362"},
    # 其他
    {"name": "HeroMiners · 全球", "url": "stratum+tcp://pearl.herominers.com:1200"},
    {"name": "AlphaPool · 新加坡 (1%)", "url": "stratum+tcp://sg1.alphapool.tech:5571"},
    {"name": "自定义矿池…", "url": ""},
]

ALGOS = {"PRL (Pearl)": "pearlhash"}

# 游戏模式（网吧式自动让卡）：检测到游戏进程出现即暂停挖矿让出显卡，
# 游戏进程消失后自动恢复。预设常见游戏进程名（小写），可在界面补充自定义。
GAME_PROCESSES = {
    "dota2.exe": "刀塔2",
    "tslgame.exe": "绝地求生",
    "pubglite-win64-shipping.exe": "绝地求生低配版",
    "cs2.exe": "CS2",
    "valorant-win64-shipping.exe": "无畏契约",
    "r5apex.exe": "Apex英雄",
    "overwatch.exe": "守望先锋",
    "league of legends.exe": "英雄联盟",
    "crossfire.exe": "穿越火线",
    "yuanshen.exe": "原神",
    "genshinimpact.exe": "原神国际服",
    "narakabladepoint.exe": "永劫无间",
    "escapefromtarkov.exe": "逃离塔科夫",
    "deltaforce.exe": "三角洲行动",
    "cod.exe": "使命召唤",
    "naraka.exe": "永劫无间(旧)",
}

def _default_kernel_dir():
    """内核目录默认跟着程序走，不可写时退回 %LOCALAPPDATA%\\BaPiMiner\\kernels。"""
    p = os.path.join(BASE, "kernels")
    try:
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, ".w")
        with open(probe, "w") as f:
            f.write("1")
        os.remove(probe)
        return p
    except OSError:
        alt = os.path.join(os.environ.get("LOCALAPPDATA") or BASE, "BaPiMiner", "kernels")
        try:
            os.makedirs(alt, exist_ok=True)
        except OSError:
            pass
        return alt


DEFAULT_CFG = {
    "kernel": "SRBMiner-MULTI",
    "kernel_dir": _default_kernel_dir(),
    "algo": "PRL (Pearl)",
    "pool_url": POOLS[0]["url"],
    "wallet": "",
    "worker": socket.gethostname().lower()[:20],
    "autostart": False,
    "autorestart": True,
    "gamemode": True,      # 游戏模式：检测到游戏进程自动暂停挖矿
    "games": [],           # 额外自定义游戏进程名（小写 exe 名，如 mygame.exe）
    "ignore_miners": [],   # 已确认忽略的「其他挖矿程序」进程名
    "check_other_miners": True,   # 检测本机其他挖矿程序并提示关闭
    "server": "http://nas.sinyerobot.com:8099",  # 中控地址已内置，留空不上报
    "token": "",           # 中控接入口令（留空则自动生成随机口令）
    "rig_name": socket.gethostname().lower(),
}

UNIT_THS = {"H": 1e-12, "K": 1e-9, "M": 1e-6, "G": 1e-3, "T": 1.0, "": 1e-12}


def list_processes():
    """枚举当前所有进程的 exe 文件名（小写列表）。非 Windows 或失败返回空表。"""
    if os.name != "nt":
        return []
    try:
        k32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x2

        class _PE32W(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_uint), ("cntUsage", ctypes.c_uint),
                        ("th32ProcessID", ctypes.c_uint),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", ctypes.c_uint), ("cntThreads", ctypes.c_uint),
                        ("th32ParentProcessID", ctypes.c_uint),
                        ("pcPriClassBase", ctypes.c_ssize_t),
                        ("dwFlags", ctypes.c_uint),
                        ("szExeFile", ctypes.c_wchar * 260)]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (0, -1):
            return []
        pe = _PE32W()
        pe.dwSize = ctypes.sizeof(_PE32W)
        names = []
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            names.append(pe.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
        k32.CloseHandle(snap)
        return names
    except Exception:  # noqa: BLE001
        return []


def find_game_process(extra, procs=None):
    """匹配游戏进程，返回 (进程名, 游戏名)；无匹配返回 (None, None)。

    extra: 用户自定义进程名列表；procs: 传入进程名列表用于测试，缺省实时枚举。
    """
    procs = set(p.lower() for p in procs) if procs is not None else set(list_processes())
    if not procs:
        return None, None
    for exe, label in GAME_PROCESSES.items():
        if exe in procs:
            return exe, label
    for exe in extra or []:
        exe = (exe or "").strip().lower()
        if exe and exe in procs:
            return exe, exe
    return None, None


def log_write(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


# ---------------------------------------------------------------- 进程检测
# 本机其他挖矿程序会抢显卡，是「算力比别的软件低」最常见的真实原因；
# 重复运行的客户端实例同理。这里枚举进程 + 完整路径，把「不是自己拉的」找出来。

MINER_PROCESSES = {
    "xmrig.exe": "XMRig 门罗",
    "t-rex.exe": "T-Rex",
    "trex.exe": "T-Rex",
    "lolminer.exe": "lolMiner",
    "gminer.exe": "GMiner",
    "nbminer.exe": "NBMiner",
    "phoenixminer.exe": "PhoenixMiner",
    "teamredminer.exe": "TeamRedMiner",
    "excavator.exe": "NiceHash Excavator",
    "ethminer.exe": "Ethminer",
    "nanominer.exe": "Nanominer",
    "rigel.exe": "Rigel",
    "kawpowminer.exe": "kawpowminer",
    "cryptodredge.exe": "CryptoDredge",
    "srbminer-multi.exe": "SRBMiner-MULTI",
    "bzminer.exe": "BzMiner",
    "vminer.exe": "Vminer",
    "vminer-gui.exe": "Vminer",
    "minerstat.exe": "Minerstat",
}

_SELF_NAMES = {"bapiminer.exe", "miner_client.py"}
_mutex_handle = None


def acquire_single_instance(name="BaPiMinerClient"):
    """同机只允许一个客户端实例（重复实例各拉一个矿工，算力直接对半砍）。"""
    global _mutex_handle
    if os.name != "nt":
        return True
    try:
        k32 = ctypes.windll.kernel32
        h = k32.CreateMutexW(None, False, ctypes.c_wchar_p("Local\\" + name))
        if not h:
            return True
        _mutex_handle = h          # 保活，进程退出自动释放
        return k32.GetLastError() != 183      # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return True


def _proc_image_path(k32, pid):
    try:
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.c_uint(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return buf.value
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass
    return ""


def list_processes_ex():
    """枚举进程：[{pid, name, path}]。"""
    if os.name != "nt":
        return []
    out = []
    try:
        k32 = ctypes.windll.kernel32

        class _PE(ctypes.Structure):
            _fields_ = [("dwSize", ctypes.c_uint), ("cntUsage", ctypes.c_uint),
                        ("th32ProcessID", ctypes.c_uint),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", ctypes.c_uint), ("cntThreads", ctypes.c_uint),
                        ("th32ParentProcessID", ctypes.c_uint),
                        ("pcPriClassBase", ctypes.c_ssize_t),
                        ("dwFlags", ctypes.c_uint),
                        ("szExeFile", ctypes.c_wchar * 260)]

        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if snap in (0, -1):
            return []
        pe = _PE()
        pe.dwSize = ctypes.sizeof(_PE)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            pid = int(pe.th32ProcessID)
            name = pe.szExeFile.lower()
            if pid and name:
                out.append({"pid": pid, "name": name,
                            "path": _proc_image_path(k32, pid)})
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
        k32.CloseHandle(snap)
    except Exception:  # noqa: BLE001
        return []
    return out


def detect_conflicts(own_pids=(), own_dirs=(), procs=None):
    """找本机其他挖矿程序与其他客户端实例。

    返回 [{pid, name, label, path, kind}]，kind: miner（第三方矿工）/ dup（重复实例）。
    自己拉起的矿工进程（PID 或所在内核目录）会被排除。
    """
    procs = procs if procs is not None else list_processes_ex()
    own = set(int(p) for p in own_pids if p)
    dirs = [os.path.normcase(os.path.abspath(d)) for d in (own_dirs or []) if d]
    me = os.getpid()
    out = []
    for p in procs:
        try:
            pid = int(p.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        name = (p.get("name") or "").lower()
        if not pid or pid == me or pid in own:
            continue
        path = p.get("path") or ""
        np = os.path.normcase(os.path.abspath(path)) if path else ""
        if np and any(np == d or np.startswith(d + os.sep) for d in dirs):
            continue      # 自己内核目录里的进程 = 我们自己的矿工
        label = MINER_PROCESSES.get(name)
        if label:
            out.append({"pid": pid, "name": name, "label": label,
                        "path": path, "kind": "miner"})
        elif name in _SELF_NAMES:
            out.append({"pid": pid, "name": name, "label": "BaPiMiner 重复实例",
                        "path": path, "kind": "dup"})
    return out


def kill_pids(pids):
    """结束进程，返回 {pid: ok}。"""
    res = {}
    if os.name != "nt":
        return res
    try:
        k32 = ctypes.windll.kernel32
    except Exception:  # noqa: BLE001
        return res
    for pid in pids:
        try:
            h = k32.OpenProcess(0x0001, False, int(pid))   # PROCESS_TERMINATE
            if not h:
                res[int(pid)] = False
                continue
            ok = bool(k32.TerminateProcess(h, 1))
            k32.CloseHandle(h)
            res[int(pid)] = ok
        except Exception:  # noqa: BLE001
            res[int(pid)] = False
    return res


# ---------------------------------------------------------------- 命令组装

def build_cmd(kernel, kernel_dir, algo_label, pool_url, wallet, worker):
    """返回 (exe_path, [args])；配置不完整时抛 ValueError。"""
    k = KERNELS.get(kernel)
    if not k:
        raise ValueError("未知内核: %s" % kernel)
    if not kernel_dir or not os.path.isdir(kernel_dir):
        raise ValueError("请先选择内核目录（含 %s）" % k["exe"])
    exe = os.path.join(kernel_dir, k["exe"])
    if not os.path.isfile(exe):
        raise ValueError("未找到 %s，请确认内核目录" % exe)
    algo = ALGOS.get(algo_label)
    if not algo:
        raise ValueError("未知算法: %s" % algo_label)
    if not pool_url:
        raise ValueError("请填写矿池地址")
    if not wallet:
        raise ValueError("请填写钱包地址")
    w = (wallet or "").strip()
    if kernel == "SRBMiner-MULTI":
        args = ["--algorithm", algo, "--pool", pool_url, "--wallet", "%s.%s" % (w, worker),
                "--api-enable", "--api-port", str(k["api_port"]), "--disable-cpu"]
    else:
        args = ["-a", algo, "-p", pool_url, "-w", w, "-r", worker,
                "--http_enabled", "true", "--http_port", str(k["api_port"])]
    return exe, args


# ---------------------------------------------------------------- 采集/解析

def query_gpus():
    cmd = ["nvidia-smi", "--query-gpu=index,name,temperature.gpu,utilization.gpu,"
           "memory.used,memory.total,fan.speed,power.draw",
           "--format=csv,noheader,nounits"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=10).stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
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
        gpus.append({"index": int(parts[0] or 0), "model": parts[1], "temp": _f(parts[2]),
                     "util": _f(parts[3]), "mem_used": _f(parts[4]), "mem_total": _f(parts[5]),
                     "fan": _f(parts[6]), "power": _f(parts[7])})
    return gpus


def _to_ths(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v) * UNIT_THS[""]
    if isinstance(v, dict):
        for k in ("value", "val", "hashrate", "total"):
            if k in v:
                return _to_ths(v[k])
        return 0.0
    m = re.search(r"([\d.]+)\s*([HKGTM])?H/s", str(v), re.I)
    if m:
        return float(m.group(1)) * UNIT_THS.get((m.group(2) or "").upper(), UNIT_THS[""])
    try:
        return float(v) * UNIT_THS[""]
    except (TypeError, ValueError):
        return 0.0


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("%s.%s" % (path, k), k, v)
            yield from _walk(v, "%s.%s" % (path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:16]):
            yield from _walk(v, "%s[%d]" % (path, i))


def _flatten_hr(obj, path="", key=""):
    """像 _walk 一样遍历，但把数组里的标量也吐出来。

    hashrate 常是 [10 秒, 60 秒, 15 分钟] 数组，_walk 会跳过数组内的数字，
    导致整台机的算力取不到值。这里保留数组下标以区分窗口。
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (path, k)
            yield (p, k, v)
            yield from _flatten_hr(v, p, k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:16]):
            p = "%s[%d]" % (path, i)
            if isinstance(v, (dict, list)):
                yield from _flatten_hr(v, p, key)
            else:
                yield (p, key, v)


def _hr_candidates(data):
    """收集 hashrate 候选并打分。

    SRBMiner/BzMiner 的 API 里 hashrate 有多处（总量/每卡/CPU），且总量常是
    [10 秒, 60 秒, 15 分钟] 的数组。取「第一个命中的字段」会拿到 10 秒瞬时值、
    甚至只拿到单张卡，看起来就像算力偏低。这里按路径打分：total 优先、
    单卡/CPU 降权，同一字段的多个窗口取最后一个（最长的 15 分钟窗口最稳）。
    """
    groups = {}
    for path, k, v in _flatten_hr(data):
        kl, pl = k.lower(), path.lower()
        if not (re.search(r"hashrate|hash_rate|performance", kl)
                or "hashrate" in pl or "hash_rate" in pl):
            continue
        if "unit" in kl or isinstance(v, (list, dict)):
            continue
        val = _to_ths(v)
        if val <= 0:
            continue
        norm = re.sub(r"\[\d+\]", "[]", pl)
        mt = re.search(r"\[(\d+)\]", pl)
        idx = int(mt.group(1)) if mt else -1
        score = 0
        if "total" in norm:
            score += 100
        if "summary" in norm or "miner" in norm:
            score += 20
        if "gpu" in norm or "device" in norm or "card" in norm:
            score -= 40
        if "cpu" in norm:
            score -= 120
        if isinstance(v, str):
            score += 10      # "123.4 MH/s" 这种带单位串比裸数字可信
        prev = groups.get(norm)
        if prev is None or idx >= prev[0]:
            groups[norm] = (idx, val, score)
    return [(s, v) for (_i, v, s) in groups.values()]


def _best_hashrate(data):
    cands = _hr_candidates(data)
    if not cands:
        return 0.0
    cands.sort(key=lambda x: (-x[0], -x[1]))
    return round(cands[0][1], 3)


def parse_gpu_rates(data, n):
    """取每张卡的算力，按 GPU 索引归位（长度 = n）。"""
    if n <= 0:
        return []
    found = {}
    for path, k, v in _flatten_hr(data):
        pl, kl = path.lower(), k.lower()
        if not (re.search(r"hashrate|hash_rate|performance", kl) or "hashrate" in pl):
            continue
        if isinstance(v, (list, dict)) or "unit" in kl:
            continue
        mt = re.search(r"(?:gpus?|devices?|cards?)\[(\d+)\]", pl)
        if not mt:
            continue
        val = _to_ths(v)
        if val > 0:
            found[int(mt.group(1))] = val      # 后写覆盖前值：窗口越后越稳
    return [round(found.get(i, 0.0), 3) for i in range(n)]


def parse_miner(api_type, data):
    m = {"ths": 0.0, "accepted": 0, "rejected": 0, "uptime": None, "algo": None, "pool": None}
    m["ths"] = _best_hashrate(data)
    for _path, k, v in _walk(data):
        kl = k.lower()
        pl = _path.lower()
        if isinstance(v, (int, float)):
            if ("accepted" in kl and "share" in kl) or kl == "accepted":
                m["accepted"] += int(v)
            elif "rejected" in kl:
                m["rejected"] += int(v)
            elif kl == "uptime" and m["uptime"] is None:
                m["uptime"] = int(v)
        elif isinstance(v, str):
            if m["algo"] is None and kl in ("algorithm", "algo"):
                m["algo"] = v
            if m["pool"] is None and kl in ("pool", "pool_url") and ":" in v:
                m["pool"] = v
    return m


def poll_miner_api(api_type, port, ngpu=0):
    url = "http://127.0.0.1:%d/%s" % (port, "api/snapshot" if api_type == "bzminer" else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bapiminer-client/1.0"})
        with _open(req, 6) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        m = parse_miner(api_type, data)
        m["gpu_rates"] = parse_gpu_rates(data, ngpu)
        m["alive"] = True
        m["type"] = api_type
        return m
    except Exception as e:  # noqa: BLE001
        return {"type": api_type, "alive": False, "error": "%s: %s" % (e.__class__.__name__, e),
                "ths": 0.0, "accepted": 0, "rejected": 0, "gpu_rates": []}


_opener_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _is_local_url(url):
    try:
        h = (urllib.parse.urlparse(url).hostname or "").lower()
        return h in ("127.0.0.1", "localhost", "::1") \
            or h.startswith("192.168.") or h.startswith("10.") or h.startswith("172.")
    except Exception:  # noqa: BLE001
        return False


def _open(req, timeout):
    """本机/内网地址绕过系统代理（系统代理常把 127.0.0.1 劫持成 502）。"""
    if _is_local_url(req.full_url):
        return _opener_local.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def http_json(url, timeout=10, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"User-Agent": "bapiminer-client/1.0"}
    if payload is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with _open(req, timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


_rate_cache = {"map": {}, "ts": 0.0}


# ---------------------------------------------------------------- 内核自动下载
# 矿机端往往直连不上境外源，由中控（NAS）代下缓存后转发。

def http_download(url, dest, timeout=1800, headers=None, meta=None):
    h = {"User-Agent": "bapiminer-client/1.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, method="GET")
    with _open(req, timeout) as r, open(dest, "wb") as f:
        if isinstance(meta, dict):
            for hk in ("X-Kernel-Version", "X-Kernel-Name", "X-Kernel-Key"):
                meta[hk] = r.headers.get(hk) or ""
        total = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
            f.write(chunk)
    return total


# ---- 内核版本落地：{kernel_key: {version, file, ts}}
KERNEL_VER_FILE = ".bapiminer_kernel.json"


def kernel_versions(kernel_dir):
    if not kernel_dir:
        return {}
    try:
        with open(os.path.join(kernel_dir, KERNEL_VER_FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def kernel_local_version(kernel_key, kernel_dir):
    return str((kernel_versions(kernel_dir).get(kernel_key) or {}).get("version") or "")


def save_kernel_version(kernel_key, kernel_dir, version, file_name=""):
    d = kernel_versions(kernel_dir)
    d[kernel_key] = {"version": version or "", "file": file_name or "", "ts": time.time()}
    try:
        os.makedirs(kernel_dir, exist_ok=True)
        with open(os.path.join(kernel_dir, KERNEL_VER_FILE), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    return version or ""


def kernel_installed(kernel_key, kernel_dir):
    """(是否已装, 版本号, exe 路径)。"""
    k = KERNELS.get(kernel_key) or {}
    exe = os.path.join(kernel_dir, k.get("exe") or "") if kernel_dir else ""
    ok = bool(exe and os.path.isfile(exe))
    return ok, kernel_local_version(kernel_key, kernel_dir), exe


def extract_kernel_zip(zpath, kernel_dir, exe_name):
    """解压内核 zip（自动剥掉官方压缩包的顶层目录），成功返回 None。"""
    import shutil
    import tempfile
    import zipfile
    exe_l = exe_name.lower()
    tmp = tempfile.mkdtemp(dir=kernel_dir)
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        src_root = None
        for root, _dirs, files in os.walk(tmp):
            if exe_l in (f.lower() for f in files):
                src_root = root
                break
        if not src_root:
            return "压缩包里找不到 %s" % exe_name
        for f in os.listdir(src_root):
            src = os.path.join(src_root, f)
            dst = os.path.join(kernel_dir, f)
            if os.path.isdir(src):
                import shutil as _sh
                _sh.copytree(src, dst, dirs_exist_ok=True)
            else:
                import shutil as _sh
                _sh.copy2(src, dst)
        return None
    except Exception as e:  # noqa: BLE001
        return "解压失败: %s" % e
    finally:
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)


def ensure_kernel(kernel_key, kernel_dir, server, token, emit=None, rig_name="",
                  force=False):
    """确保 kernel_dir 里有所选内核；force=True 时强制拉取中控最新版并覆盖。

    返回 (错误信息或 None, 当前版本号)。
    force 时先重命名正在运行的 exe（Windows 允许改名运行中的 exe），再解压覆盖，
    覆盖完成才写版本号，避免「看起来更新了其实还是旧内核」。
    """
    k = KERNELS.get(kernel_key)
    if not k:
        return "未知内核: %s" % kernel_key, ""
    if not kernel_dir:
        return "请先选择内核目录", ""
    exe_path = os.path.join(kernel_dir, k["exe"])
    cur_ver = kernel_local_version(kernel_key, kernel_dir)
    if os.path.isfile(exe_path) and not force:
        return None, cur_ver
    if not server or not token:
        if os.path.isfile(exe_path):
            return None, cur_ver
        return "缺少 %s，且未配置中控无法自动下载" % k["exe"], ""
    try:
        os.makedirs(kernel_dir, exist_ok=True)
        key = k.get("key") or ("srbminer" if kernel_key == "SRBMiner-MULTI" else "bzminer")
        url = server.rstrip("/") + "/api/kernel_dl?kernel=" + key
        if rig_name:
            url += "&rig=" + urllib.parse.quote(rig_name)
        if force:
            url += "&t=%d" % int(time.time())      # 绕过任何中间层缓存
        zpath = os.path.join(kernel_dir, "_dl_%s.zip" % kernel_key)
        if emit:
            emit(event="log", msg="⬇ 正在从中控获取 %s%s ..." % (
                k.get("label") or kernel_key, " 最新版" if force else ""))
        meta = {}
        size = http_download(url, zpath, headers={"X-Rig-Token": token}, meta=meta)
        new_ver = (meta.get("X-Kernel-Version") or "").strip()
        if emit:
            emit(event="log", msg="⬇ 下载完成 %.1f MB%s，解压中..." % (
                size / 1048576.0, ("，版本 " + new_ver) if new_ver else ""))
        if force and new_ver and new_ver == cur_ver and os.path.isfile(exe_path):
            try:
                os.remove(zpath)
            except OSError:
                pass
            if emit:
                emit(event="log", msg="✓ 已是最新版本 %s，无需覆盖" % cur_ver)
            return None, cur_ver
        if force and os.path.isfile(exe_path):
            try:
                os.replace(exe_path, exe_path + ".old")   # 允许改名运行中的 exe
            except OSError:
                pass
        err = extract_kernel_zip(zpath, kernel_dir, k["exe"])
        try:
            os.remove(zpath)
        except OSError:
            pass
        if err:
            return err, cur_ver
        if not os.path.isfile(exe_path):
            return "下载解压后仍缺少 %s" % k["exe"], cur_ver
        try:
            for junk in (".old",):
                p = exe_path + junk
                if os.path.isfile(p):
                    os.remove(p)
        except OSError:
            pass
        save_kernel_version(kernel_key, kernel_dir, new_ver,
                            meta.get("X-Kernel-Name") or "")
        if emit:
            emit(event="log", msg="✓ 内核已就绪%s" % (
                ("（%s → %s）" % (cur_ver or "未记录", new_ver)) if new_ver else ""))
        return None, new_ver or cur_ver
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "401" in msg or "Unauthorized" in msg:
            msg = ("本机接入码被中控拒绝（常见于重装系统、更换客户端目录或新旧版本并存）。"
                   "请在中控该矿机卡片点『🔑 重置接入码』，等待约 15 秒自动重试即可恢复")
        return "自动下载内核失败: %s" % msg, cur_ver


def fetch_kernel_catalog(server, token, timeout=20):
    """向中控要最新内核版本表（矿机端连不上 GitHub，靠中控代查）。失败返回 {}。"""
    if not server:
        return {}
    try:
        d = http_json(server.rstrip("/") + "/api/kernel_catalog", timeout=timeout,
                      headers={"X-Rig-Token": token or ""})
        return d.get("catalog") or {}
    except Exception:  # noqa: BLE001
        return {}


def refresh_rates():
    """从性价比榜服务拉 PRL 收益率（USDT/TH/s/天），10 分钟缓存。"""
    if time.time() - _rate_cache["ts"] < 600:
        return
    try:
        d = http_json("https://gpu-value-rank.app.workbuddy.host/api/rank", timeout=20)
        m = {}
        for it in d.get("items", []):
            if it.get("model") and it.get("ths") and it.get("usdt_day") and it.get("price_cny"):
                m[it["model"]] = it["usdt_day"] / it["ths"]
        if m:
            _rate_cache["map"] = m
            _rate_cache["ts"] = time.time()
    except Exception:  # noqa: BLE001
        _rate_cache["ts"] = time.time() - 540  # 1 分钟后重试


def estimate_income(model, ths):
    refresh_rates()
    rate = _rate_cache["map"].get(model) \
        or _rate_cache["map"].get((model or "").replace("NVIDIA GeForce ", "").replace("NVIDIA ", ""))
    if rate:
        return round(ths * rate * 7.10, 2)
    return None


# ---------------------------------------------------------------- 挖矿引擎

class MiningEngine:
    """矿工进程守护 + 统计采集 + 群控上报。GUI 通过 queue 拿状态。"""

    def __init__(self, cfg, ui_queue):
        self.cfg = dict(cfg)
        self.q = ui_queue
        self.proc = None
        self._proc_lock = threading.Lock()   # UI 线程与引擎线程可能同时 _kill
        self.stop_flag = threading.Event()
        self.state = "stopped"       # stopped/starting/running/error
        self.paused = False          # 游戏模式：检测到游戏进程，暂停挖矿让卡
        self.game_proc = None        # 当前检测到的游戏进程名
        self.remote_stop = False     # 中控远程停止（start_miner 前不自动恢复）
        self.conflicts = []          # 本机检测到的其他挖矿程序 / 重复实例
        self.kernel_ver = ""         # 当前内核版本（由 ensure_kernel 写入）
        self._threads = []
        self.starts = 0
        self.stats = {"ths": 0.0, "accepted": 0, "rejected": 0, "uptime": None,
                      "income": None, "gpus": [], "miner_alive": False, "error": None}

    def emit(self, **kw):
        self.q.put(kw)

    def start(self, cfg):
        """启动引擎线程。已在运行则只更新配置 —— 重复点击「开始」不会再起一个矿工。"""
        self.cfg = dict(cfg)
        alive = [t for t in self._threads if t.is_alive()]
        if alive and not self.stop_flag.is_set():
            return False
        if alive:
            # 刚点过停止：先让旧线程收尾，否则两个引擎会各拉一个矿工抢卡
            self.stop_flag.set()
            for t in alive:
                t.join(3)
            if any(t.is_alive() for t in alive):
                return False
        self._kill(wait=2)
        self.stop_flag.clear()
        self.remote_stop = False     # 本地点「开始」是明确意图，解除远程停止态
        self.paused = False
        self.game_proc = None
        self._threads = [
            threading.Thread(target=self._run, daemon=True),
            threading.Thread(target=self._poll_cmds, daemon=True),
        ]
        for t in self._threads:
            t.start()
        return True

    def stop(self):
        """用户点停止 / 托盘停止：立刻结束矿工，不必等引擎下一轮循环。"""
        self.stop_flag.set()
        try:
            threading.Thread(target=self._kill, kwargs={"wait": 2}, daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    def _poll_cmds(self):
        """轮询中控命令队列（30 秒一轮），支持远程游戏模式开关 / 停止启动矿工。"""
        while not self.stop_flag.wait(30):
            if not (self.cfg.get("server") and self.cfg.get("token")):
                continue
            try:
                from urllib.parse import quote
                d = http_json(
                    self.cfg["server"].rstrip("/") + "/api/poll?name="
                    + quote(self.cfg.get("rig_name") or socket.gethostname().lower()),
                    timeout=10, headers={"X-Rig-Token": self.cfg["token"]})
                for c in d.get("commands") or []:
                    ok, note = self._handle_cmd(c.get("cmd") or "")
                    self.emit(event="log", msg="收到中控命令 %s：%s" % (c.get("cmd"), note))
                    try:
                        http_json(self.cfg["server"].rstrip("/") + "/api/cmd_result",
                                  timeout=10, method="POST",
                                  headers={"X-Rig-Token": self.cfg["token"]},
                                  payload={"rig": self.cfg.get("rig_name"),
                                           "id": c.get("id"), "ok": ok, "result": note})
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

    def _handle_cmd(self, cmd):
        """执行中控命令，返回 (ok, 备注)。"""
        if cmd == "gamemode_off":
            self.cfg["gamemode"] = False
            if self.paused:
                self.paused = False
                self.emit(event="log", msg="游戏模式已由中控关闭，恢复挖矿")
            self.emit(event="gamemode", enabled=False)
            return True, "游戏模式已关闭"
        if cmd == "gamemode_on":
            self.cfg["gamemode"] = True
            self.emit(event="gamemode", enabled=True)
            return True, "游戏模式已开启"
        if cmd == "stop_miner":
            self.remote_stop = True
            self._kill()
            self.emit(event="log", msg="中控远程停止挖矿")
            return True, "已停止矿工"
        if cmd == "start_miner":
            self.remote_stop = False
            self.paused = False
            self.emit(event="log", msg="中控远程启动挖矿")
            return True, "已恢复矿工"
        if cmd == "update_kernel":
            old_v = kernel_local_version(self.cfg["kernel"], self.cfg["kernel_dir"])
            # 先停矿工：Windows 下运行中的 exe 虽可改名，但文件替换仍可能被锁
            self._kill()
            time.sleep(1.5)
            err, new_v = ensure_kernel(self.cfg["kernel"], self.cfg["kernel_dir"],
                                       self.cfg.get("server"), self.cfg.get("token"),
                                       emit=self.emit,
                                       rig_name=self.cfg.get("rig_name") or "",
                                       force=True)
            if err:
                self.emit(event="log", msg="✕ 内核更新失败：%s" % err)
                return False, err
            self.emit(event="kernel", version=new_v)
            self.kernel_ver = new_v or old_v
            if new_v and new_v == old_v:
                return True, "已是最新内核 %s（矿工已用该版本重启）" % new_v
            return True, "内核已更新 %s → %s，矿工重启中" % (
                old_v or "未记录", new_v or "未知")
        if cmd == "restart_miner":
            self._kill()  # 引擎主循环 10 秒内自动拉起
            return True, "矿工已重启"
        return False, "未知命令: %s" % cmd

    def _kill(self, wait=10):
        """结束矿工进程（幂等、可并发调用）。返回 True 表示确认已无残留。"""
        with self._proc_lock:
            p, self.proc = self.proc, None
        if p is None:
            return True
        pid = getattr(p, "pid", None)
        if p.poll() is not None:
            return True
        try:
            p.terminate()
            p.wait(timeout=min(wait, 5))
        except Exception:  # noqa: BLE001
            pass
        if p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
        if p.poll() is None and pid:
            # 最后兜底：连子进程树一起清掉，避免矿工残留继续占卡
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               creationflags=0x08000000,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10)
            except Exception:  # noqa: BLE001
                pass
        try:
            if p.stdout:
                p.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        alive = p.poll() is None
        log_write("内核进程 PID %s %s" % (pid, "未能结束" if alive else "已结束"))
        return not alive

    def shutdown(self, timeout=12):
        """真正退出程序时调用：停线程 → 杀矿工 → 再确认一次，保证内核不会留在后台。"""
        self.stop_flag.set()
        self.remote_stop = True
        try:
            self._kill(wait=3)
        except Exception:  # noqa: BLE001
            pass
        end = time.time() + timeout
        for t in list(self._threads):
            left = end - time.time()
            if left <= 0:
                break
            try:
                t.join(min(left, 3))
            except Exception:  # noqa: BLE001
                pass
        # 引擎线程退出前可能又拉起过一次，收尾再确认
        ok = self._kill(wait=3)
        self.state = "stopped"
        return ok

    def _run(self):
        try:
            exe, args = build_cmd(self.cfg["kernel"], self.cfg["kernel_dir"],
                                  self.cfg["algo"], self.cfg["pool_url"],
                                  self.cfg["wallet"], self.cfg["worker"])
        except ValueError as e:
            # 内核 exe 缺失时尝试从中控自动下载
            if "未找到" in str(e) or "请确认内核目录" in str(e):
                err, _kv = ensure_kernel(self.cfg["kernel"], self.cfg["kernel_dir"],
                                         self.cfg.get("server"), self.cfg.get("token"),
                                         emit=self.emit, rig_name=self.cfg.get("rig_name") or "")
                if not err:
                    try:
                        exe, args = build_cmd(self.cfg["kernel"], self.cfg["kernel_dir"],
                                              self.cfg["algo"], self.cfg["pool_url"],
                                              self.cfg["wallet"], self.cfg["worker"])
                    except ValueError as e2:
                        e = e2
                        err = None
                if err:
                    self.state = "error"
                    self.emit(event="error", msg="%s（%s）" % (e, err))
                    return
            else:
                self.state = "error"
                self.emit(event="error", msg=str(e))
                return
        self.state = "running"
        self.kernel_ver = kernel_local_version(self.cfg["kernel"],
                                              self.cfg.get("kernel_dir") or "")
        k = KERNELS[self.cfg["kernel"]]
        api_port, api_type = k["api_port"], k["api_type"]
        restarts = 0

        while not self.stop_flag.is_set():
            try:
                # ---- 游戏模式状态机（网吧式自动让卡：按游戏进程触发）
                if self.cfg.get("gamemode") and not self.remote_stop:
                    gp, glabel = find_game_process(self.cfg.get("games") or [])
                    if gp and not self.paused:
                        self.paused = True
                        self.game_proc = gp
                        self._kill()
                        self.emit(event="log", msg="🎮 游戏模式：检测到「%s」(%s)，暂停挖矿让出显卡" % (glabel, gp))
                    elif not gp and self.paused:
                        self.paused = False
                        self.game_proc = None
                        self.emit(event="log", msg="🎮 游戏模式：游戏进程已退出，恢复挖矿")
                elif self.paused and not self.remote_stop and not self.cfg.get("gamemode"):
                    # 游戏模式被关闭时解除暂停
                    self.paused = False
                    self.game_proc = None

                hold = self.paused or self.remote_stop
                if hold and self.proc and self.proc.poll() is None:
                    self._kill()

                if (not hold) and (self.proc is None or self.proc.poll() is not None):
                    if self.proc is not None:
                        restarts += 1
                        self.emit(event="log", msg="矿工进程退出(rc=%s)，%.0f 秒后自动拉起（第 %d 次）"
                                  % (self.proc.returncode, 10, restarts))
                        self._kill()
                        if self.stop_flag.wait(10):
                            break
                    self.emit(event="log", msg="启动: %s %s" % (exe, " ".join(args)))
                    self.proc = subprocess.Popen(
                        [exe] + args, cwd=os.path.dirname(exe),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        creationflags=0x08000000)  # CREATE_NO_WINDOW
                    self.starts += 1
                    self.emit(event="log", msg="矿工已启动 (PID %d)" % self.proc.pid)

                # 其他挖矿程序 / 重复实例检测（每 30 秒一轮）
                self._tick = getattr(self, "_tick", 0) + 1
                if self.cfg.get("check_other_miners", True) and self._tick % 3 == 1:
                    try:
                        own_pids = [self.proc.pid] if (
                            self.proc and self.proc.poll() is None) else []
                        found = detect_conflicts(own_pids,
                                                 [self.cfg.get("kernel_dir") or ""])
                        ign = set(x.lower() for x in (self.cfg.get("ignore_miners") or []))
                        found = [c for c in found if c["name"] not in ign]
                        if found != self.conflicts:
                            self.conflicts = found
                            self.emit(event="conflicts", items=found)
                            for c in found:
                                self.emit(event="log", msg="⚠ 检测到%s：%s（%s，PID %d）— 会抢占显卡，建议关闭"
                                          % ("本程序重复实例" if c["kind"] == "dup" else "其他挖矿程序",
                                             c["label"], c["name"], c["pid"]))
                    except Exception as ex:  # noqa: BLE001
                        log_write("冲突检测失败: %s" % ex)

                # 采集
                gpus = query_gpus()
                mi = poll_miner_api(api_type, api_port, len(gpus))
                rates = mi.get("gpu_rates") or []
                for _g in gpus:
                    _i = int(_g.get("index") or 0)
                    _g["ths"] = rates[_i] if _i < len(rates) else 0.0
                model = (gpus[0].get("model") if gpus else "") or ""
                self.stats.update({
                    "ths": mi.get("ths") or 0.0,
                    "accepted": mi.get("accepted") or 0,
                    "rejected": mi.get("rejected") or 0,
                    "uptime": mi.get("uptime"),
                    "miner_alive": mi.get("alive", False),
                    "gpus": gpus, "model": model,
                    "income": estimate_income(model, mi.get("ths") or 0.0),
                    "error": mi.get("error"),
                    "gamemode": bool(hold),
                    "game_proc": self.game_proc if hold else None,
                })
                self.emit(event="stats", **self.stats)

                # 群控上报
                if self.cfg.get("server") and self.cfg.get("token"):
                    try:
                        http_json(self.cfg["server"].rstrip("/") + "/api/report",
                                  timeout=12, method="POST", payload={
                                      "name": self.cfg.get("rig_name") or socket.gethostname().lower(),
                                      "token": self.cfg["token"],
                                      "hostname": socket.gethostname(),
                                      "ts": time.time(),
                                      "game_mode": bool(hold),
                                      "game_proc": self.game_proc if hold else None,
                                      "kernel": self.cfg.get("kernel"),
                                      "kernel_ver": self.kernel_ver or kernel_local_version(
                                          self.cfg.get("kernel"), self.cfg.get("kernel_dir") or ""),
                                      "conflicts": [c["label"] for c in self.conflicts],
                                      "wallet": self.cfg.get("wallet") or "",
                                      "gpus": gpus,
                                      "miners": [mi],
                                  })
                    except Exception as e:  # noqa: BLE001
                        msg = str(e)
                        if "401" in msg or "token mismatch" in msg:
                            msg = ("接入码被中控拒绝（重装/更换目录/新旧版本并存导致）。"
                                   "在中控该矿机卡片点『🔑 重置接入码』即可自动恢复")
                            if not getattr(self, "_auth_warned", False):
                                self._auth_warned = True
                                self.emit(event="log", msg="⚠ " + msg)
                        log_write("中控上报失败: %s" % msg)

                # 矿工日志尾巴
                if self.proc and self.proc.stdout:
                    deadline = time.time() + 3
                    while time.time() < deadline:
                        line = self.proc.stdout.readline()
                        if line:
                            self.emit(event="log", msg=line.decode("utf-8", "replace").rstrip()[:200])
                        else:
                            break
            except Exception as e:  # noqa: BLE001
                log_write("engine: %s" % e)
                self.emit(event="log", msg="引擎异常: %s" % e)
            self.stop_flag.wait(10)

        self._kill()
        self.state = "stopped"
        self.emit(event="log", msg="已停止挖矿")
        self.emit(event="stopped")


def set_autostart(enable, exe_path=None):
    """HKCU Run 键开机自启（GUI/注册表失败只返回错误信息，不抛异常）。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        target = exe_path or (sys.executable if getattr(sys, "frozen", False)
                              else '"%s" "%s"' % (sys.executable, os.path.abspath(__file__)))
        if enable:
            winreg.SetValueEx(key, "BaPiMiner", 0, winreg.REG_SZ, target)
        else:
            try:
                winreg.DeleteValue(key, "BaPiMiner")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return None
    except Exception as e:  # noqa: BLE001
        return str(e)


# ---------------------------------------------------------------- 系统托盘

def res_path(name):
    """资源文件路径：打包后取 _MEIPASS，源码运行取脚本目录。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def brand_logo(size=64):
    """品牌徽章 PNG（打包内置），缺文件返回 None。"""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    p = res_path("bapiminer-%d.png" % size)
    if not os.path.isfile(p):
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def make_tray_image(size=64):
    """托盘图标：优先用品牌徽章，缺资源时退回程序化绘制的绿色矿镐。"""
    img = brand_logo(size)
    if img is not None:
        return img
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, size - 2, size - 2], radius=size // 4,
                        fill=(32, 197, 100, 255))
    w = max(3, size // 9)
    top, bot = int(size * 0.30), int(size * 0.72)
    lx, rx, mx = int(size * 0.26), int(size * 0.74), size // 2
    for a, b in (((lx, bot), (lx, top)), ((lx, top), (mx, int(size * 0.56))),
                 ((mx, int(size * 0.56)), (rx, top)), ((rx, top), (rx, bot))):
        d.line([a, b], fill=(255, 255, 255, 255), width=w)
    return img


class TrayIcon:
    """Windows 通知区图标。点关闭只收进托盘，右键菜单「退出」才是真退出。"""

    def __init__(self, root, engine, ui_queue):
        self.root = root
        self.engine = engine
        self.q = ui_queue
        self.icon = None

    def _post(self, kind):
        # 托盘线程不能直接碰 tkinter，统一丢回 UI 队列由主线程处理
        try:
            self.q.put({"event": kind})
        except Exception:  # noqa: BLE001
            pass

    def start(self):
        try:
            import pystray
        except Exception as e:  # noqa: BLE001
            log_write("托盘不可用（pystray 缺失）: %s" % e)
            return False
        img = make_tray_image()
        if img is None:
            log_write("托盘不可用（Pillow 缺失）")
            return False
        try:
            menu = pystray.Menu(
                pystray.MenuItem("打开主界面", lambda *_: self._post("tray_show"), default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("▶ 开始挖矿", lambda *_: self._post("tray_start")),
                pystray.MenuItem("■ 停止挖矿", lambda *_: self._post("tray_stop")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出（并关闭挖矿内核）", lambda *_: self._post("tray_quit")),
            )
            self.icon = pystray.Icon("BaPiMiner", img, "扒皮矿工·矿机端 · 后台运行中", menu)
            threading.Thread(target=self._run, daemon=True).start()
            return True
        except Exception as e:  # noqa: BLE001
            log_write("托盘启动失败: %s" % e)
            return False

    def _run(self):
        try:
            self.icon.run()
        except Exception as e:  # noqa: BLE001
            log_write("托盘运行异常: %s" % e)

    def set_tip(self, text):
        try:
            if self.icon and text:
                self.icon.title = text
                self.icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def notify(self, msg, title="扒皮矿工·矿机端"):
        try:
            if self.icon:
                self.icon.notify(msg, title)
        except Exception:  # noqa: BLE001
            pass

    def stop(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
        self.icon = None


# ---------------------------------------------------------------- GUI

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    if not acquire_single_instance():
        # 重复实例会各拉一个矿工抢同一张卡，算力不升反降，直接拦住
        probe = tk.Tk()
        probe.withdraw()
        messagebox.showwarning(
            "扒皮矿工已在运行",
            "本机已经有一个 扒皮矿工·矿机端在运行。\n\n"
            "同时跑多个实例会互相抢显卡，算力不会翻倍，反而会明显下降。\n"
            "请切换到已打开的那个窗口（或先关掉它再启动本程序）。")
        probe.destroy()
        return 1

    cfg = {}
    if os.path.isfile(CONFIG_FILE):
        try:
            cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
    for k, v in DEFAULT_CFG.items():
        cfg.setdefault(k, v)

    # 旧配置 server 为空 → 自动补内置中控地址（升级免手动改）
    if not cfg.get("server"):
        cfg["server"] = DEFAULT_CFG["server"]
    # 老配置的内核目录若是别的软件留下的（里面没有任何支持的内核），自动切回内置目录
    if cfg.get("kernel_dir") and not any(
            os.path.isfile(os.path.join(cfg["kernel_dir"], k["exe"]))
            for k in KERNELS.values()):
        cfg["kernel_dir"] = DEFAULT_CFG["kernel_dir"]
    # 中控口令为空时自动生成随机口令并落盘（新矿机首次上报即注册，无需手动输入）
    if not cfg.get("token"):
        cfg["token"] = "rig-" + os.urandom(8).hex()
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    q = queue.Queue()
    engine = MiningEngine(cfg, q)

    # ---------------- 深色主题（Vminer 风） ----------------
    C_BG, C_SIDE = "#12151d", "#191e29"
    C_SIDE_ON, C_CARD, C_CARD2 = "#242c3f", "#1e2433", "#273049"
    C_LINE, C_TXT, C_MUT = "#2b3245", "#e9eef7", "#7c889e"
    C_GRN, C_CYN, C_RED, C_AMB = "#2fd56b", "#3cc9e8", "#ef5566", "#f2b544"
    FONT = "Microsoft YaHei UI"

    class Toggle(tk.Canvas):
        """Vminer 风格滑动开关。"""
        def __init__(self, master, var, cmd=None):
            self.var, self.cmd = var, cmd
            super().__init__(master, width=42, height=22,
                             bg=master.cget("bg"), highlightthickness=0, cursor="hand2")
            self.bind("<Button-1>", lambda _e: (self.var.set(not self.var.get()),
                                                self.cmd() if self.cmd else None))
            var.trace_add("write", lambda *_: self.draw())
            self.draw()

        def draw(self):
            self.delete("all")
            col = C_GRN if self.var.get() else "#3a4358"
            self.create_arc(0, 0, 22, 22, start=90, extent=180, fill=col, outline=col)
            self.create_arc(20, 0, 42, 22, start=-90, extent=180, fill=col, outline=col)
            self.create_rectangle(11, 0, 31, 22, fill=col, outline=col)
            cx = 31 if self.var.get() else 11
            self.create_oval(cx - 7, 4, cx + 7, 18, fill="#ffffff", outline="")

    def lab(parent, txt, color=C_TXT, size=10, bold=False, **kw):
        return tk.Label(parent, text=txt, bg=parent.cget("bg"), fg=color,
                        font=(FONT, size, "bold" if bold else "normal"), **kw)

    root = tk.Tk()
    root.title("扒皮矿工·矿机端")
    root.geometry("1120x720")
    root.minsize(980, 620)
    root.configure(bg=C_BG)

    # 窗口/任务栏图标：标题栏左上角、Alt-Tab、任务栏缩略图统一品牌徽章
    try:
        _icon_img = tk.PhotoImage(file=res_path("bapiminer-256.png"))
        root.iconphoto(True, _icon_img)
        root._brand_icon = _icon_img  # 持有引用防 GC
    except Exception:  # noqa: BLE001
        try:
            root.iconbitmap(res_path("bapiminer.ico"))
        except Exception:  # noqa: BLE001
            pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:  # noqa: BLE001
        pass
    style.configure(".", font=(FONT, 10), background=C_BG, foreground=C_TXT)
    style.configure("Dark.TCombobox", fieldbackground=C_CARD2, background=C_CARD2,
                    foreground=C_TXT, arrowcolor=C_TXT, bordercolor=C_LINE,
                    lightcolor=C_CARD2, darkcolor=C_CARD2,
                    selectbackground=C_CARD2, selectforeground=C_TXT)
    root.option_add("*TCombobox*Listbox.background", C_CARD2)
    root.option_add("*TCombobox*Listbox.foreground", C_TXT)
    root.option_add("*TCombobox*Listbox.selectBackground", "#33405c")
    root.option_add("*TCombobox*Listbox.selectForeground", C_TXT)

    state_var = tk.StringVar(value="已停止")

    # ---------------- 布局骨架：左侧栏 + 主区 ----------------
    body = tk.Frame(root, bg=C_BG)
    body.pack(fill="both", expand=True)
    side = tk.Frame(body, bg=C_SIDE, width=172)
    side.pack(side="left", fill="y")
    side.pack_propagate(False)
    main = tk.Frame(body, bg=C_BG)
    main.pack(side="left", fill="both", expand=True)

    logo_img = None
    try:
        logo_img = tk.PhotoImage(file=res_path("bapiminer-64.png"))
        head = tk.Frame(side, bg=C_SIDE)
        head.pack(anchor="w", pady=(16, 0))
        tk.Label(head, image=logo_img, bg=C_SIDE).pack(side="left", padx=(10, 8))
        htx = tk.Frame(head, bg=C_SIDE)
        htx.pack(side="left")
        lab(htx, "扒皮矿工", C_TXT, 14, True).pack(anchor="w")
        lab(htx, "BAPI MINER · PRL", C_MUT, 8).pack(anchor="w", pady=(1, 0))
    except Exception:  # noqa: BLE001
        logo_img = None
        lab(side, "  ⛏ 扒皮矿工", C_TXT, 13, True).pack(anchor="w", pady=(20, 0))
        lab(side, "  PRL · pearlhash", C_MUT, 9).pack(anchor="w", pady=(2, 18))

    pages, nav_btns = {}, {}

    def show_page(item):
        for k, bb in nav_btns.items():
            on = (k == item)
            bb.configure(bg=C_SIDE_ON if on else C_SIDE,
                         fg=C_TXT if on else C_MUT,
                         font=(FONT, 11, "bold" if on else "normal"))
            bb.indic.configure(bg=C_GRN if on else C_SIDE)
        for k, pf in pages.items():
            pf.place(x=0, y=0, relwidth=1, relheight=1) if k == item else pf.place_forget()

    def nav(item, icon, label):
        pages[item] = tk.Frame(main, bg=C_BG)
        row = tk.Frame(side, bg=C_SIDE)
        row.pack(fill="x")
        ind = tk.Frame(row, bg=C_SIDE, width=3, height=38)
        ind.pack(side="left", fill="y")
        b = tk.Label(row, text="%s  %s" % (icon, label), bg=C_SIDE, fg=C_MUT,
                     font=(FONT, 11), anchor="w", padx=19, pady=10, cursor="hand2")
        b.pack(fill="x", side="left")
        b.indic = ind
        b.bind("<Button-1>", lambda _e, it=item: show_page(it))
        row.bind("<Button-1>", lambda _e, it=item: show_page(it))
        ind.bind("<Button-1>", lambda _e, it=item: show_page(it))
        nav_btns[item] = b

    for it, ic, lb in [("mine", "⛏", "单机模式"), ("game", "🎮", "游戏模式"),
                       ("fleet", "☁", "群控中心"), ("sys", "⚙", "系统设置")]:
        nav(it, ic, lb)
    show_page(os.environ.get("MINRIGS_PAGE") or "mine")

    def card(parent):
        return tk.Frame(parent, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)

    def hbtn(parent, text, cmd, fg=C_TXT, bg=C_CARD2, hbg="#33405c", size=9, bold=False, padx=12):
        """悬停高亮的扁平按钮。"""
        b = tk.Label(parent, text=text, bg=bg, fg=fg, font=(FONT, size, "bold" if bold else "normal"),
                     padx=padx, pady=6, cursor="hand2")
        b.bind("<Enter>", lambda _e: b.configure(bg=hbg))
        b.bind("<Leave>", lambda _e: b.configure(bg=bg))
        b.bind("<Button-1>", lambda _e: cmd())
        return b

    # ================= 页：单机模式 =================
    pg = pages["mine"]

    # ---- 冲突提示条（检测到其他挖矿程序/重复实例时出现）
    conflict_bar = tk.Frame(pg, bg="#3a2a12", highlightbackground="#7a5a1e",
                            highlightthickness=1)
    cbi = tk.Frame(conflict_bar, bg="#3a2a12")
    cbi.pack(fill="x", padx=14, pady=10)
    conflict_txt = tk.StringVar(value="")
    lab(cbi, "⚠", C_AMB, 14, True).pack(side="left")
    tk.Label(cbi, textvariable=conflict_txt, bg="#3a2a12", fg="#f6d79a", font=(FONT, 9),
             anchor="w", justify="left", wraplength=620).pack(side="left", padx=10)
    conflict_items = []

    def on_kill_conflicts():
        items = list(conflict_items)
        if not items:
            return
        lines = "\n".join("  · %s（%s，PID %d）" % (c["label"], c["name"], c["pid"])
                          for c in items)
        if not messagebox.askyesno(
                "关闭这些程序？",
                "将要结束以下进程：\n\n%s\n\n它们正在占用显卡，关闭后本机算力会恢复正常。\n"
                "如果这是机主自己在用的程序，请选择「否」。" % lines):
            return
        res = kill_pids([c["pid"] for c in items])
        okn = sum(1 for v in res.values() if v)
        append_log("✓ 已关闭 %d 个冲突进程（共 %d 个）" % (okn, len(items)))
        conflict_items.clear()
        conflict_bar.pack_forget()

    def on_ignore_conflicts():
        names = [c["name"] for c in conflict_items]
        ig = list(cfg.get("ignore_miners") or [])
        for n in names:
            if n not in ig:
                ig.append(n)
        cfg["ignore_miners"] = ig
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        conflict_items.clear()
        conflict_bar.pack_forget()
        append_log("已忽略冲突提醒：%s" % "、".join(names))

    hbtn(cbi, "关闭这些程序", on_kill_conflicts, fg="#f6d79a", bg="#5a3f16",
         hbg="#6d4d1c", bold=True).pack(side="right", padx=(8, 0))
    hbtn(cbi, "忽略", on_ignore_conflicts, fg=C_MUT, bg="#3a2a12",
         hbg="#4a3718").pack(side="right")

    # ---- 顶栏：状态胶囊 + 开关
    top = tk.Frame(pg, bg=C_BG)
    top.pack(fill="x", padx=20, pady=(18, 8))
    autostart_var = tk.BooleanVar(value=bool(cfg.get("autostart")))
    autonmine_var = tk.BooleanVar(value=bool(cfg.get("autostart_mining")))
    gamemode_var = tk.BooleanVar(value=bool(cfg.get("gamemode")))
    pill = tk.Label(top, text="● 已停止 · 点击开始挖矿", bg=C_CARD2, fg=C_MUT,
                    font=(FONT, 11, "bold"), padx=18, pady=9, cursor="hand2")
    pill.pack(side="left")
    pill.bind("<Enter>", lambda _e: pill.configure(bg="#2f3a55"))
    pill.bind("<Leave>", lambda _e: pill.configure(bg=pill.stop_bg if hasattr(pill, "stop_bg") else C_CARD2))
    for txt, var in [("开机启动", autostart_var), ("启动后自动挖矿", autonmine_var),
                     ("游戏模式", gamemode_var)]:
        w = tk.Frame(top, bg=C_BG)
        w.pack(side="left", padx=(26, 0))
        Toggle(w, var).pack(side="left")
        lab(w, txt, C_MUT, 9).pack(side="left", padx=(6, 0))

    # ---- KPI 卡片行
    kpis = tk.Frame(pg, bg=C_BG)
    kpis.pack(fill="x", padx=20, pady=4)
    stat_vars = {
        "ths": tk.StringVar(value="0.0"), "acc": tk.StringVar(value="0 / 0"),
        "income": tk.StringVar(value="—"), "gpu": tk.StringVar(value="—"),
    }
    for i, (key, label) in enumerate([("ths", "总算力 TH/s"), ("acc", "份额 接受 / 拒绝"),
                                      ("income", "PRL 日收益 ¥"), ("gpu", "GPU 温度 / 风扇")]):
        box = card(kpis)
        box.grid(row=0, column=i, padx=(0, 10), sticky="nsew")
        kpis.columnconfigure(i, weight=1)
        lab(box, label, C_MUT, 9).pack(anchor="w", padx=14, pady=(10, 0))
        lab(box, "", {"ths": C_CYN, "acc": C_TXT, "income": C_GRN, "gpu": C_AMB}[key],
            19, True).pack(anchor="w", padx=14, pady=(2, 10))
        stat_vars[key + "_lbl"] = box.winfo_children()[-1]
        stat_vars[key + "_lbl"].configure(textvariable=stat_vars[key])

    # ---- GPU 行 + 矿工信息
    gpu_card = card(pg)
    gpu_card.pack(fill="x", padx=20, pady=6)
    gpu_row = tk.Frame(gpu_card, bg=C_CARD)
    gpu_row.pack(fill="x", padx=16, pady=12)
    gpu_text = tk.StringVar(value="未在挖矿 —— 填好钱包地址后点「开始挖矿」")
    lab(gpu_row, "", C_TXT, 11, True).pack(side="left")
    stat_vars["gpu_row"] = gpu_row.winfo_children()[-1]
    stat_vars["gpu_row"].configure(textvariable=gpu_text)

    miner_card = card(pg)
    miner_card.pack(fill="x", padx=20, pady=6)
    mi = tk.Frame(miner_card, bg=C_CARD)
    mi.pack(fill="x", padx=16, pady=10)
    for _c in range(6):
        mi.columnconfigure(_c, weight=1)

    def mfield(r, c, title, var, color=C_TXT):
        f = tk.Frame(mi, bg=C_CARD)
        f.grid(row=r, column=c, sticky="w")
        lab(f, title, C_MUT, 9).pack(anchor="w")
        lab(f, "", color, 12, True).pack(anchor="w")
        var[1] = f.winfo_children()[-1]
        var[1].configure(textvariable=var[0])

    mvar_kernel = [tk.StringVar(value=cfg["kernel"]), None]
    mvar_pool = [tk.StringVar(value="—"), None]
    mvar_wallet = [tk.StringVar(value=cfg["wallet"] or "未设置"), None]
    mvar_uptime = [tk.StringVar(value="—"), None]
    mfield(0, 0, "内核", mvar_kernel)
    mfield(0, 1, "矿池", mvar_pool, C_AMB)
    mfield(0, 2, "钱包", mvar_wallet)
    mfield(0, 3, "运行时长", mvar_uptime)

    # ---- 钱包 / 矿池选择行（主页面内嵌）
    pool_bar = card(pg)
    pool_bar.pack(fill="x", padx=20, pady=6)
    pbi = tk.Frame(pool_bar, bg=C_CARD)
    pbi.pack(fill="x", padx=16, pady=10)
    pool_names = [p["name"] for p in POOLS]
    pool_var = tk.StringVar()
    custom_pool_var = tk.StringVar(value=cfg["pool_url"])
    wallet_var = tk.StringVar(value=cfg["wallet"])

    def on_pool_chosen(_e=None):
        p = POOLS[[x["name"] for x in POOLS].index(pool_var.get())]
        if p["url"]:
            custom_pool_var.set(p["url"])
            custom_cb.configure(state="disabled")
            mvar_pool[0].set(p["name"].split(" · ")[0])
        else:
            custom_cb.configure(state="normal")
            mvar_pool[0].set("自定义")
    lab(pbi, "① 钱包地址", C_MUT, 9).grid(row=0, column=0, sticky="w")
    tk.Entry(pbi, textvariable=wallet_var, width=48, bg=C_CARD2, fg=C_TXT, insertbackground=C_TXT,
             relief="flat", font=(FONT, 10), highlightbackground=C_LINE, highlightthickness=1
             ).grid(row=0, column=1, sticky="we", padx=8, pady=2)
    pbi.columnconfigure(1, weight=1)
    lab(pbi, "② 矿池", C_MUT, 9).grid(row=1, column=0, sticky="w", pady=(8, 0))
    prow = tk.Frame(pbi, bg=C_CARD)
    prow.grid(row=1, column=1, sticky="we", pady=(8, 0))
    pool_cb = ttk.Combobox(prow, textvariable=pool_var, values=pool_names, state="readonly",
                           width=30, style="Dark.TCombobox")
    pool_cb.pack(side="left")
    pool_cb.bind("<<ComboboxSelected>>", on_pool_chosen)
    custom_cb = tk.Entry(prow, textvariable=custom_pool_var, width=30, bg=C_CARD2, fg=C_TXT,
                         insertbackground=C_TXT, relief="flat", font=(FONT, 9),
                         disabledbackground=C_CARD2, disabledforeground=C_MUT,
                         highlightbackground=C_LINE, highlightthickness=1)
    custom_cb.pack(side="left", padx=8)
    cur = next((p for p in POOLS if p["url"] == cfg["pool_url"]), POOLS[-1])
    pool_var.set(cur["name"])
    on_pool_chosen()

    # ---- 日志台（按级别着色）
    log_box = tk.Text(pg, height=12, bg="#0d1119", fg="#c8d4e5", insertbackground="#c8d4e5",
                      font=("Consolas", 9), relief="flat", state="disabled",
                      spacing1=2, spacing3=2,
                      highlightbackground=C_LINE, highlightthickness=1)
    log_box.pack(fill="both", expand=True, padx=20, pady=(6, 6))
    for tag, col in [("ts", "#5b6b82"), ("ok", C_GRN), ("err", C_RED),
                     ("warn", C_AMB), ("gm", C_CYN), ("dim", "#8fa0b8")]:
        log_box.tag_configure(tag, foreground=col)

    def append_log(msg):
        tag = "dim"
        if msg.startswith(("✓", "已恢复", "游戏模式已开启")):
            tag = "ok"
        elif msg.startswith(("错误", "失败", "✕")) or "失败:" in msg or "失败: " in msg:
            tag = "err"
        elif msg.startswith(("🎮",)):
            tag = "gm"
        elif msg.startswith(("⬇", "提示", "首次")):
            tag = "warn"
        log_box.configure(state="normal")
        log_box.insert("end", "[%s] " % time.strftime("%H:%M:%S"), "ts")
        log_box.insert("end", msg + "\n", tag)
        log_box.see("end")
        if log_box.index("end-1c").split(".")[0] and int(log_box.index("end-1c").split(".")[0]) > 800:
            log_box.delete("1.0", "200.0")
        log_box.configure(state="disabled")

    # ---- 底部状态栏
    sbar = tk.Frame(pg, bg=C_SIDE)
    sbar.pack(fill="x", side="bottom")
    tk.Frame(pg, bg=C_LINE, height=1).pack(fill="x", side="bottom")
    lab(sbar, "  扒皮矿工 v1.5", C_MUT, 8).pack(side="left", pady=5)
    lab(sbar, "  ·  内置 16 款游戏进程识别", C_MUT, 8).pack(side="left")
    lab(sbar, "", C_MUT, 8).pack(side="right", padx=12)
    sbar_right = sbar.winfo_children()[-1]
    sbar_right.configure(textvariable=state_var)

    # ================= 页：游戏模式 =================
    gp_page = pages["game"]
    gc = card(gp_page)
    gc.pack(fill="x", padx=20, pady=(18, 8))
    gi = tk.Frame(gc, bg=C_CARD)
    gi.pack(fill="x", padx=18, pady=14)
    Toggle(gi, gamemode_var).pack(side="left")
    gf = tk.Frame(gi, bg=C_CARD)
    gf.pack(side="left", padx=(12, 0))
    lab(gf, "游戏模式（检测到游戏进程自动停挖让卡）", C_TXT, 11, True).pack(anchor="w")
    lab(gf, "勾选后：电脑里出现下列游戏进程 → 停止挖矿；进程消失 → 自动恢复挖矿", C_MUT, 9).pack(anchor="w")

    pc = card(gp_page)
    pc.pack(fill="both", expand=True, padx=20, pady=8)
    pi = tk.Frame(pc, bg=C_CARD)
    pi.pack(fill="both", expand=True, padx=18, pady=12)
    lab(pi, "已内置游戏名单（自动检测）", C_MUT, 9).pack(anchor="w", pady=(0, 6))
    plist = tk.Frame(pi, bg=C_CARD)
    plist.pack(fill="x")
    preset_names = list(GAME_PROCESSES.values())
    for idx in range(0, len(preset_names), 6):
        rowf = tk.Frame(plist, bg=C_CARD)
        rowf.pack(fill="x", pady=1)
        for nm in preset_names[idx:idx + 6]:
            tk.Label(rowf, text=" " + nm + " ", bg=C_CARD2, fg=C_TXT,
                     font=(FONT, 9)).pack(side="left", padx=3, pady=1)
    lab(pi, "（进程名单内置在程序里，如有遗漏可在下方补充）", C_MUT, 8).pack(anchor="w", pady=(4, 8))
    lab(pi, "自定义游戏进程（逗号分隔，如 xxx.exe,yyy.exe）", C_MUT, 9).pack(anchor="w")
    games_var = tk.StringVar(value=", ".join(cfg.get("games") or []))
    tk.Entry(pi, textvariable=games_var, width=52, bg=C_CARD2, fg=C_TXT, insertbackground=C_TXT,
             relief="flat", font=(FONT, 10), highlightbackground=C_LINE, highlightthickness=1
             ).pack(anchor="w", pady=4)

    # ================= 页：群控中心 =================
    fp = pages["fleet"]
    fc = card(fp)
    fc.pack(fill="x", padx=20, pady=(18, 8))
    fi = tk.Frame(fc, bg=C_CARD)
    fi.pack(fill="x", padx=18, pady=12)
    lab(fi, "中控群控（地址已内置，一般无需修改）", C_TXT, 11, True).pack(anchor="w", pady=(0, 8))
    grid = tk.Frame(fi, bg=C_CARD)
    grid.pack(fill="x")
    grid.columnconfigure(1, weight=1)
    server_var = tk.StringVar(value=cfg["server"])
    token_var = tk.StringVar(value=cfg["token"])
    rig_var = tk.StringVar(value=cfg["rig_name"])
    worker_var = tk.StringVar(value=cfg["worker"])
    for r, (t, v, show) in enumerate([("中控地址", server_var, ""), ("接入口令", token_var, "•"),
                                      ("矿机名（看板显示）", rig_var, ""),
                                      ("矿工名（矿池显示）", worker_var, "")]):
        lab(grid, t, C_MUT, 9).grid(row=r, column=0, sticky="w", pady=3, padx=(0, 10))
        tk.Entry(grid, textvariable=v, width=42, show=show, bg=C_CARD2, fg=C_TXT,
                 insertbackground=C_TXT, relief="flat", font=(FONT, 10),
                 highlightbackground=C_LINE, highlightthickness=1).grid(row=r, column=1, sticky="we", pady=3)
    lab(fi, "矿机上线后自动注册到看板 nas.sinyerobot.com:8099，手机 APK 也能看", C_MUT, 9).pack(anchor="w", pady=(8, 0))

    def open_dashboard():
        import webbrowser
        webbrowser.open((server_var.get().strip() or "http://nas.sinyerobot.com:8099") + "/")
    hbtn(fi, "打开中控看板 ↗", open_dashboard).pack(anchor="w", pady=(10, 0))

    # ================= 页：系统设置 =================
    sp = pages["sys"]
    sc = card(sp)
    sc.pack(fill="x", padx=20, pady=(18, 8))
    si = tk.Frame(sc, bg=C_CARD)
    si.pack(fill="x", padx=18, pady=14)

    shead = tk.Frame(si, bg=C_CARD)
    shead.pack(fill="x")
    lab(shead, "挖矿内核", C_TXT, 11, True).pack(side="left")
    lab(shead, "  自动从中控下载安装，无需手动准备文件", C_MUT, 9).pack(side="left")

    kernel_var = tk.StringVar(value=cfg["kernel"])
    dir_var = tk.StringVar(value=cfg["kernel_dir"])
    latest_meta = {}          # kernel_key -> 中控版本条目
    ker_rows = {}

    def refresh_kernels():
        for key, w in ker_rows.items():
            ok, ver, _exe = kernel_installed(key, dir_var.get().strip())
            meta = latest_meta.get(key) or {}
            lv = str(meta.get("version") or "")
            if ok:
                w["state"].set("已安装 v%s" % (ver or "未知"))
                w["state_lbl"].configure(fg=C_GRN)
                w["dot"].configure(fg=C_GRN)
            else:
                w["state"].set("未安装")
                w["state_lbl"].configure(fg=C_MUT)
                w["dot"].configure(fg="#3a4358")
            bits = []
            if lv:
                bits.append("中控最新 v%s" % lv)
                if meta.get("size"):
                    bits.append("%.1f MB" % (meta["size"] / 1048576.0))
                if not ok:
                    bits.append("可安装")
                elif ver and ver != lv:
                    bits.append("有新版本，可更新")
                else:
                    bits.append("已是最新")
            else:
                bits.append("点右上「检测最新版」查看可用版本")
            w["info"].set("  ·  ".join(bits))
            if not ok:
                w["btn"].configure(text="⬇ 安装", fg=C_GRN)
            elif lv and ver and ver != lv:
                w["btn"].configure(text="⬆ 更新到 v%s" % lv, fg=C_AMB)
            else:
                w["btn"].configure(text="⟳ 重装", fg=C_TXT)
            on = (kernel_var.get() == key)
            w["ind"].configure(bg=C_GRN if on else C_LINE)
            w["frame"].configure(highlightbackground=C_GRN if on else C_LINE)

    def select_kernel(key):
        kernel_var.set(key)
        refresh_kernels()

    def do_kernel_action(key):
        kernel_var.set(key)
        refresh_kernels()
        k = KERNELS[key]
        ok, ver, _exe = kernel_installed(key, dir_var.get().strip())
        target = str((latest_meta.get(key) or {}).get("version") or "")
        if ok and ver and target and ver == target:
            if not messagebox.askyesno(
                    "已是最新",
                    "%s 已是中控最新版 v%s。\n\n仍要重新下载覆盖吗？" % (k["label"], ver)):
                return
        append_log("⬇ 正在从中控获取 %s ..." % k["label"])

        def work():
            running = bool(engine.proc and engine.proc.poll() is None)
            if running:
                engine._kill()
                time.sleep(1.5)
                q.put({"event": "log", "msg": "已暂停矿工，准备替换内核文件"})
            err, nv = ensure_kernel(key, dir_var.get().strip(),
                                    server_var.get().strip(), token_var.get().strip(),
                                    emit=lambda **kw: q.put(kw),
                                    rig_name=rig_var.get().strip() or "", force=True)
            if err:
                q.put({"event": "log", "msg": "✕ 内核操作失败：%s" % err})
            else:
                engine.kernel_ver = nv
                q.put({"event": "log", "msg": "✓ %s 就绪（v%s）%s" % (
                    k["label"], nv or "未知",
                    "，矿工将用新内核自动重启" if running else "")})
            q.put({"event": "refresh_kernels"})
        threading.Thread(target=work, daemon=True).start()

    def build_krow(key, k):
        f = tk.Frame(si, bg=C_CARD, highlightbackground=C_LINE, highlightthickness=1)
        f.pack(fill="x", pady=(10, 0))
        ind = tk.Frame(f, bg=C_LINE, width=3)
        ind.pack(side="left", fill="y")
        inner = tk.Frame(f, bg=C_CARD)
        inner.pack(side="left", fill="both", expand=True, padx=14, pady=10)
        tr = tk.Frame(inner, bg=C_CARD)
        tr.pack(fill="x")
        dot = tk.Label(tr, text="●", bg=C_CARD, fg="#3a4358", font=(FONT, 10))
        dot.pack(side="left")
        name_lbl = tk.Label(tr, text=k["label"], bg=C_CARD, fg=C_TXT,
                            font=(FONT, 11, "bold"), cursor="hand2")
        name_lbl.pack(side="left", padx=(6, 12))
        state_v = tk.StringVar(value="")
        st = tk.Label(tr, textvariable=state_v, bg=C_CARD, fg=C_MUT, font=(FONT, 10, "bold"))
        st.pack(side="left")
        btn = hbtn(tr, "⬇ 安装", lambda kk=key: do_kernel_action(kk), fg=C_GRN, bg=C_CARD2)
        btn.pack(side="right")
        note = lab(inner, k.get("note") or "", C_MUT, 9)
        note.pack(anchor="w", pady=(3, 0))
        info_v = tk.StringVar(value="")
        info_lbl = lab(inner, "", C_MUT, 9)
        info_lbl.pack(anchor="w")
        info_lbl.configure(textvariable=info_v)
        for wdg in (inner, tr, note, info_lbl, name_lbl):
            wdg.bind("<Button-1>", lambda _e, kk=key: select_kernel(kk))
        ker_rows[key] = {"frame": f, "ind": ind, "dot": dot, "state": state_v,
                         "state_lbl": st, "info": info_v, "btn": btn}

    for _key, _k in KERNELS.items():
        build_krow(_key, _k)

    def refresh_latest():
        sv, tv = server_var.get().strip(), token_var.get().strip()

        def work():
            q.put({"event": "log", "msg": "⟳ 正在向中控查询最新内核版本..."})
            cat = fetch_kernel_catalog(sv, tv)
            if not cat:
                q.put({"event": "log",
                       "msg": "✕ 未取到版本信息（中控离线，或中控还没检查过内核）"})
                return
            q.put({"event": "latest_kernels", "catalog": cat})
        threading.Thread(target=work, daemon=True).start()
    hbtn(shead, "⟳ 检测最新版", refresh_latest, fg=C_CYN, bg=C_CARD2).pack(side="right")

    # ---- 高级：内核目录
    dir_row = tk.Frame(si, bg=C_CARD)
    dir_row.pack(fill="x", pady=(12, 0))
    lab(dir_row, "内核目录", C_MUT, 9).pack(side="left")
    tk.Entry(dir_row, textvariable=dir_var, width=54, bg=C_CARD2, fg=C_MUT,
             insertbackground=C_TXT, relief="flat", font=(FONT, 9),
             highlightbackground=C_LINE, highlightthickness=1
             ).pack(side="left", padx=8)

    def pick_dir():
        d = filedialog.askdirectory(title="选择内核目录", initialdir=dir_var.get() or BASE)
        if d:
            dir_var.set(d)
            refresh_kernels()

    def open_kernel_dir():
        d = dir_var.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)  # noqa: S606
        else:
            messagebox.showinfo("内核目录", "目录还不存在：%s" % d)

    def open_dl():
        import webbrowser
        webbrowser.open(KERNELS[kernel_var.get()]["download"])
    hbtn(dir_row, "选择…", pick_dir).pack(side="left")
    hbtn(dir_row, "打开目录", open_kernel_dir).pack(side="left", padx=6)
    hbtn(dir_row, "查看官方页", open_dl).pack(side="left")

    # ---- 运行选项
    oc = card(sp)
    oc.pack(fill="x", padx=20, pady=8)
    oi = tk.Frame(oc, bg=C_CARD)
    oi.pack(fill="x", padx=18, pady=12)
    lab(oi, "运行选项", C_TXT, 11, True).pack(anchor="w", pady=(0, 8))
    opts = tk.Frame(oi, bg=C_CARD)
    opts.pack(fill="x")
    auto_restart_var = tk.BooleanVar(value=bool(cfg.get("autorestart")))
    Toggle(opts, auto_restart_var).pack(side="left")
    lab(opts, "矿工崩溃自动拉起", C_MUT, 9).pack(side="left", padx=(6, 26))
    check_other_var = tk.BooleanVar(value=bool(cfg.get("check_other_miners", True)))
    Toggle(opts, check_other_var).pack(side="left")
    lab(opts, "检测本机其他挖矿程序并提示关闭", C_MUT, 9).pack(side="left", padx=(6, 0))
    check_other_var.trace_add(
        "write", lambda *_a: engine.cfg.__setitem__("check_other_miners", check_other_var.get()))

    # ================= 配置读写 =================
    def current_cfg():
        c = dict(cfg)
        c.update({
            "kernel": kernel_var.get(), "kernel_dir": dir_var.get().strip(),
            "algo": "PRL (Pearl)",
            "pool_url": custom_pool_var.get().strip(),
            "wallet": wallet_var.get().strip(), "worker": worker_var.get().strip() or "rig",
            "autostart": autostart_var.get(), "autostart_mining": autonmine_var.get(),
            "autorestart": auto_restart_var.get(),
            "gamemode": gamemode_var.get(),
            "check_other_miners": check_other_var.get(),
            "games": [x.strip().lower() for x in re.split(r"[,，;；\s]+", games_var.get()) if x.strip()],
            "server": server_var.get().strip(), "token": token_var.get().strip(),
            "rig_name": rig_var.get().strip() or socket.gethostname().lower(),
        })
        return c

    def save_cfg():
        new_cfg = current_cfg()
        if new_cfg["autostart"] != cfg.get("autostart"):
            err = set_autostart(new_cfg["autostart"])
            if err:
                append_log("开机自启设置失败: %s" % err)
        cfg.clear(); cfg.update(new_cfg)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as e:
            append_log("配置保存失败: %s" % e)
        mvar_wallet[0].set(cfg["wallet"] or "未设置")
        mvar_kernel[0].set(cfg["kernel"])

    # ================= 启停逻辑 =================
    def set_running(on):
        if on:
            pill.configure(text="● 运行中，点击停止", bg="#173324", fg=C_GRN)
            pill.stop_bg = "#173324"
        else:
            pill.configure(text="● 已停止 · 点击开始挖矿", bg=C_CARD2, fg=C_MUT)
            pill.stop_bg = C_CARD2
        state_var.set("运行中" if on else "已停止")

    def on_start():
        try:
            save_cfg()
            build_cmd(cfg["kernel"], cfg["kernel_dir"], cfg["algo"],
                      cfg["pool_url"], cfg["wallet"], cfg["worker"])  # 预检
        except ValueError as e:
            messagebox.showwarning("还差一步", str(e))
            return
        if not engine.start(cfg):
            append_log("挖矿引擎已在运行，忽略重复启动（重复实例会抢显卡、拉低算力）")
        set_running(True)

    def on_stop():
        engine.stop()
        set_running(False)

    def on_pill(_e=None):
        live = any(t.is_alive() for t in engine._threads) and not engine.stop_flag.is_set()
        on_stop() if live else on_start()
    pill.bind("<Button-1>", on_pill)

    # ---------- 关闭 = 收进通知区；托盘右键「退出」才真退出 ----------
    tray = TrayIcon(root, engine, q)
    tray_ok = [False]
    quitting = [False]
    close_hits = [0.0]           # 上次点关闭的时间（连续两次 = 真退出）

    def show_win():
        try:
            root.deiconify()
            root.lift()
            root.focus_force()
        except Exception:  # noqa: BLE001
            pass

    def real_quit():
        """真退出：停线程 + 杀矿工内核，绝不把内核留在后台继续跑。"""
        if quitting[0]:
            return
        quitting[0] = True
        try:
            state_var.set("正在退出…")
        except Exception:  # noqa: BLE001
            pass
        append_log("⏻ 正在退出：停止挖矿内核…")
        tray.set_tip("扒皮矿工正在退出…")

        def work():
            ok = False
            try:
                ok = engine.shutdown()
            except Exception as e:  # noqa: BLE001
                log_write("退出清理失败: %s" % e)
            q.put({"event": "quit_done", "ok": ok})
        threading.Thread(target=work, daemon=True).start()

    def on_close():
        """点 ✕：缩到任务栏（挖矿继续）。任务栏右键「关闭窗口」才是真退出。"""
        if quitting[0]:
            return
        now = time.time()
        if now - close_hits[0] < 4:
            # 4 秒内连续两次点关闭：视为确实想退出（托盘找不到时的保底通道）
            append_log("⏻ 连续两次关闭 —— 直接退出并停止挖矿内核")
            real_quit()
            return
        close_hits[0] = now
        try:
            cur = root.state()
        except Exception:  # noqa: BLE001
            cur = "normal"
        if cur == "iconic":
            # 窗口已缩到任务栏还收到关闭请求 = 任务栏右键「关闭窗口」→ 真退出
            append_log("⏻ 任务栏关闭窗口 —— 退出程序并停止挖矿内核")
            real_quit()
            return
        if not tray_ok[0]:
            if not messagebox.askyesno(
                    "退出扒皮矿工？",
                    "退出会同时停止挖矿内核。\n\n"
                    "点「否」把窗口缩到任务栏，挖矿继续。"):
                try:
                    root.iconify()
                except Exception:  # noqa: BLE001
                    pass
                return
            real_quit()
            return
        root.iconify()
        append_log("窗口已缩到任务栏，挖矿继续 —— 要退出：任务栏图标右键「关闭窗口」，"
                   "或通知区托盘图标右键「退出」")
        tray.notify("已缩到任务栏，挖矿继续。退出：任务栏右键「关闭窗口」或托盘右键「退出」。")

    def _sync_gm_to_engine():
        engine.cfg["gamemode"] = bool(gamemode_var.get())
        engine.cfg["games"] = [x.strip().lower() for x in re.split(r"[,，;；\s]+", games_var.get()) if x.strip()]

    def on_gm_toggle(*_a):
        _sync_gm_to_engine()
        append_log("🎮 游戏模式已%s" % ("开启：检测到游戏进程时自动停挖让卡" if gamemode_var.get() else "关闭：始终挖矿"))

    gamemode_var.trace_add("write", on_gm_toggle)
    games_var.trace_add("write", lambda *_a: _sync_gm_to_engine())

    def pump():
        try:
            while True:
                ev = q.get_nowait()
                kind = ev.get("event")
                if kind == "log":
                    append_log(ev["msg"])
                elif kind == "latest_kernels":
                    latest_meta.update(ev.get("catalog") or {})
                    refresh_kernels()
                    append_log("✓ 已获取中控内核版本：%s" % "、".join(
                        "%s %s" % (KERNELS[k]["label"] if k in KERNELS else k,
                                   (v or {}).get("version") or "?")
                        for k, v in (ev.get("catalog") or {}).items()))
                elif kind == "refresh_kernels":
                    refresh_kernels()
                elif kind == "conflicts":
                    conflict_items.clear()
                    conflict_items.extend(ev.get("items") or [])
                    if conflict_items:
                        names = "、".join("%s（%s）" % (c["label"], c["name"])
                                          for c in conflict_items[:4])
                        more = " 等 %d 个程序" % len(conflict_items) if len(conflict_items) > 4 else ""
                        dup = any(c["kind"] == "dup" for c in conflict_items)
                        conflict_txt.set(
                            "本机检测到%s%s 正在占用显卡，这会让算力明显低于正常水平。"
                            "是否关闭？" % (names, more)
                            + ("（其中包含重复运行的 BaPiMiner 实例，建议只保留一个）" if dup else ""))
                        conflict_bar.pack(fill="x", padx=20, pady=(14, 0), before=top)
                    else:
                        conflict_bar.pack_forget()
                elif kind == "stats":
                    stat_vars["ths"].set("%.1f" % (ev.get("ths") or 0))
                    stat_vars["acc"].set("%d / %d" % (ev.get("accepted") or 0, ev.get("rejected") or 0))
                    inc = ev.get("income")
                    stat_vars["income"].set("¥%.0f" % inc if inc else "—")
                    gpus = ev.get("gpus") or []
                    if gpus:
                        g0 = gpus[0]
                        stat_vars["gpu"].set("%.0f°C / %.0f%%" % (
                            max(g["temp"] for g in gpus), max(g["fan"] or 0 for g in gpus)))
                        gpu_text.set("  ·  ".join(
                            "%s  %.1f TH/s  %.0f°  %.0fW" % (
                                (g.get("model") or "GPU").replace("NVIDIA GeForce ", ""),
                                g.get("ths") or 0, g.get("temp") or 0, g.get("power") or 0)
                            for g in gpus))
                    else:
                        stat_vars["gpu"].set("—")
                        gpu_text.set("未检测到 NVIDIA GPU")
                    up = ev.get("uptime") or 0
                    if up:
                        mvar_uptime[0].set("%d:%02d:%02d" % (up // 3600, up % 3600 // 60, up % 60))
                    else:
                        mvar_uptime[0].set("—")
                    if ev.get("gamemode"):
                        gp = ev.get("game_proc")
                        state_var.set("🎮 游戏模式让卡中" + ("（%s）" % gp if gp else ""))
                        pill.configure(text="🎮 让卡中" + ("（%s）" % gp if gp else ""),
                                       bg="#3a2f16", fg=C_AMB)
                    elif not ev.get("miner_alive") and engine.state == "running":
                        state_var.set("● 矿工未响应")
                elif kind == "gamemode":
                    gamemode_var.set(bool(ev.get("enabled")))
                elif kind == "error":
                    append_log("错误: " + ev["msg"])
                    state_var.set("● 出错")
                    set_running(False)
                elif kind == "stopped":
                    state_var.set("已停止")
                    set_running(False)
                elif kind == "tray_show":
                    show_win()
                elif kind == "tray_start":
                    on_start()
                elif kind == "tray_stop":
                    on_stop()
                elif kind == "tray_quit":
                    real_quit()
                elif kind == "quit_done":
                    if not ev.get("ok"):
                        append_log("⚠ 内核进程未能确认结束，请到任务管理器检查是否有 SRBMiner/bzminer 残留")
                    else:
                        append_log("✓ 挖矿内核已关闭，程序退出")
                    try:
                        set_running(False)
                    except Exception:  # noqa: BLE001
                        pass
                    tray.stop()
                    try:
                        root.destroy()
                    except Exception:  # noqa: BLE001
                        pass
                    return
        except queue.Empty:
            pass
        root.after(500, pump)

    append_log("扒皮矿工已就绪 —— 填好钱包地址、选好矿池，点顶部胶囊「开始挖矿」")
    refresh_kernels()
    _ok, _ver, _exe = kernel_installed(kernel_var.get(), dir_var.get().strip())
    if _ok:
        append_log("内核 %s 已就绪%s" % (
            KERNELS[kernel_var.get()]["label"], ("（v%s）" % _ver) if _ver else ""))
    else:
        append_log("首次使用：内核还没装，去「系统设置」点该内核的「⬇ 安装」，"
                   "程序会自动从中控下载，不需要手动准备文件")
    pump()
    if autonmine_var.get() and cfg.get("wallet"):
        root.after(1200, on_start)
    else:
        root.after(800, refresh_latest)
    root.protocol("WM_DELETE_WINDOW", on_close)
    atexit.register(lambda: engine.shutdown(timeout=4))   # 进程异常退出也清内核
    tray_ok[0] = tray.start()
    if tray_ok[0]:
        append_log("托盘已就绪：点右上角 ✕ 只是收进通知区（挖矿继续），"
                   "要退出请右键托盘图标选「退出」")
    else:
        append_log("系统托盘不可用，关闭窗口时会询问是否退出（退出会停止内核）")
    root.mainloop()
    # 主循环退出（含异常路径）兜底：确保矿工内核不会留在后台
    try:
        tray.stop()
        engine.shutdown(timeout=6)
    except Exception as e:  # noqa: BLE001
        log_write("退出兜底失败: %s" % e)


# ---------------------------------------------------------------- 自检

def selftest():
    # 打包成无控制台 exe 后被管道调用时，stdout 可能是 GBK 编码，统一转 UTF-8 防崩
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    print("== BaPiMiner selftest ==")
    ok = True

    # 1) 命令组装
    for kernel, exe in [("SRBMiner-MULTI", "SRBMiner-MULTI.exe"), ("BzMiner", "bzminer.exe")]:
        d = os.path.join(BASE, "_fake_kernel")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, exe), "w").close()
        try:
            e, args = build_cmd(kernel, d, "PRL (Pearl)",
                                "stratum+tcp://prl.kryptex.network:7048",
                                "prl123abcd", "rig01")
            print("[%s] OK  %s %s" % (kernel, os.path.basename(e), " ".join(args)))
        except ValueError as err:
            print("[%s] FAIL %s" % (kernel, err)); ok = False

    # 2) 解析器（SRBMiner 风格 / BzMiner 风格假报文）
    fake_srb = {"mining": {"total_hashrate": "324.5 TH/s",
                           "accepted_shares": 1200, "rejected_shares": 8,
                           "uptime": 3600, "algorithm": "pearlhash",
                           "pool": "stratum+tcp://prl.kryptex.network:7048"}}
    r1 = parse_miner("srbminer", fake_srb)
    assert abs(r1["ths"] - 324.5) < 0.01 and r1["accepted"] == 1200, r1
    print("[parse srbminer] OK ", r1)
    fake_bz = {"hashrate": {"total": 314000000000000.0},
               "devices": [{"accepted": 900}, {"accepted": 300}],
               "rejected": 5, "uptime": 120}
    r2 = parse_miner("bzminer", fake_bz)
    assert abs(r2["ths"] - 314.0) < 0.01 and r2["accepted"] == 1200, r2
    print("[parse bzminer] OK ", r2)

    # 3) 中控上报（如果本机 8099 在跑）
    try:
        resp = http_json("http://127.0.0.1:8099/api/report", timeout=8, method="POST", payload={
            "name": "SELFTEST", "token": "selftest-token", "hostname": "selftest",
            "gpus": [{"index": 0, "model": "NVIDIA GeForce RTX 4090", "temp": 74,
                      "util": 99, "mem_used": 21000, "mem_total": 24564,
                      "fan": 85, "power": 448}],
            "miners": [{"type": "srbminer", "alive": True, "ths": 324.5,
                        "accepted": 1200, "rejected": 8, "uptime": 3600,
                        "algo": "pearlhash", "pool": "prl.kryptex.network:7048"}],
        })
        print("[report] ", resp)
    except Exception as e:  # noqa: BLE001
        print("[report] 中控不可达（不影响使用）:", e)

    # 4) 收益估算
    inc = estimate_income("NVIDIA GeForce RTX 4090", 324.0)
    print("[income] RTX 4090 @324TH/s ->", ("¥%s/天" % inc) if inc else "收益率暂不可用")

    # 5) 游戏模式：进程枚举 + 名单匹配 + 命令状态机
    nproc = len(list_processes())
    print("[gamemode] 进程枚举 OK（当前 %d 个进程）" % nproc)
    gp, gl = find_game_process([], ["dota2.exe", "explorer.exe"])
    assert gp == "dota2.exe" and gl == "刀塔2", (gp, gl)
    gp2, gl2 = find_game_process(["mygame.exe"], ["chrome.exe", "MyGame.EXE"])
    assert gp2 == "mygame.exe" and gl2 == "mygame.exe", (gp2, gl2)
    gp3, _ = find_game_process([], ["chrome.exe", "code.exe"])
    assert gp3 is None, gp3
    print("[gamemode] 名单匹配 OK（预设命中 / 自定义命中 / 不误报）")
    eng = MiningEngine({"gamemode": True, "server": "", "token": "", "rig_name": "selftest"}, queue.Queue())
    ok_cmd, note = eng._handle_cmd("gamemode_off")
    assert ok_cmd and eng.cfg["gamemode"] is False, note
    ok_cmd, note = eng._handle_cmd("gamemode_on")
    assert ok_cmd and eng.cfg["gamemode"] is True, note
    eng.paused = True
    eng.remote_stop = False
    ok_cmd, note = eng._handle_cmd("gamemode_off")
    assert ok_cmd and eng.paused is False, "gamemode_off 应解除暂停"
    print("[gamemode] 命令状态机 OK ", note)

    # 6) 内核 zip 解压（模拟官方压缩包的顶层目录结构）
    import zipfile as _zf
    kd = os.path.join(BASE, "_fake_kernel")
    os.makedirs(kd, exist_ok=True)
    zpath = os.path.join(BASE, "_fake_kernel.zip")
    with _zf.ZipFile(zpath, "w") as z:
        z.writestr("SRBMiner-MULTI-2-9-0-x64/SRBMiner-MULTI.exe", "fakeexe")
        z.writestr("SRBMiner-MULTI-2-9-0-x64/kernel.cl", "fakecl")
    zerr = extract_kernel_zip(zpath, kd, "SRBMiner-MULTI.exe")
    assert zerr is None, zerr
    assert os.path.isfile(os.path.join(kd, "SRBMiner-MULTI.exe")), "解压后缺少 exe"
    os.remove(zpath)
    print("[kernel-zip] OK 顶层目录已剥离，exe 落到内核目录")

    # 7) 算力解析：总量优先、数组取最长窗口、每卡归位
    fake_real = {"algorithms": [{
        "algorithm": "pearlhash",
        "hashrate": {"total": [4.120e14, 4.185e14, 4.217e14],
                     "gpu": [[2.084e14, 2.091e14, 2.105e14],
                             [2.036e14, 2.094e14, 2.112e14]]},
        "shares": {"accepted": 42, "rejected": 1},
        "uptime": 900,
        "cpu": {"hashrate": [0.0, 0.0, 0.0]},
    }]}
    rr = parse_miner("srbminer", fake_real)
    assert abs(rr["ths"] - 421.7) < 0.01, rr        # 取 15 分钟窗口，不是 412 瞬时值
    gr = parse_gpu_rates(fake_real, 2)
    assert abs(gr[0] - 210.5) < 0.01 and abs(gr[1] - 211.2) < 0.01, gr
    print("[parse hashrate] OK 总量 %.1f TH/s（最长窗口），每卡 %s" % (rr["ths"], gr))

    # 8) 内核版本落地
    ver_dir = os.path.join(BASE, "_fake_ver")
    os.makedirs(ver_dir, exist_ok=True)
    save_kernel_version("SRBMiner-MULTI", ver_dir, "2.9.4", "srb.zip")
    assert kernel_local_version("SRBMiner-MULTI", ver_dir) == "2.9.4"
    print("[kernel-ver] OK 版本记录 v%s" % kernel_local_version("SRBMiner-MULTI", ver_dir))

    # 9) 其他挖矿程序 / 重复实例检测（注入进程表，不依赖真机）
    fake_procs = [
        {"pid": os.getpid(), "name": "miner_client.py", "path": ""},
        {"pid": 200, "name": "xmrig.exe", "path": r"C:\tools\xmrig.exe"},
        {"pid": 300, "name": "srbminer-multi.exe",
         "path": r"C:\BaPiMiner\kernels\SRBMiner-MULTI.exe"},
        {"pid": 400, "name": "chrome.exe", "path": r"C:\chrome.exe"},
        {"pid": 500, "name": "bapiminer.exe", "path": r"D:\other\BaPiMiner.exe"},
    ]
    cf = detect_conflicts([], [os.path.join(BASE, "kernels")], fake_procs)
    kinds = sorted((c["name"], c["kind"]) for c in cf)
    assert ("xmrig.exe", "miner") in kinds, kinds
    assert ("bapiminer.exe", "dup") in kinds, kinds
    assert not any(c["pid"] in (os.getpid(), 400) for c in cf), kinds
    print("[conflicts] OK 识别 %s（自身/无关进程已排除）" % kinds)

    # 10) 退出清理：关窗口/托盘退出必须把矿工进程真的结束，不能留在后台继续占卡
    eng2 = MiningEngine({"gamemode": False, "server": "", "token": "", "rig_name": "selftest2"},
                        queue.Queue())
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000)
    eng2.proc = kid
    assert kid.poll() is None, "测试子进程没起来"
    eng2.shutdown(timeout=6)
    time.sleep(0.4)
    assert kid.poll() is not None, "shutdown 后矿工进程仍在运行（关闭不彻底）"
    assert eng2.proc is None, "shutdown 后应清空 proc 引用"
    print("[shutdown] OK 内核进程 PID %d 已结束（rc=%s）" % (kid.pid, kid.returncode))

    t_img = make_tray_image()
    if t_img is not None:
        print("[tray] OK 托盘图标 %dx%d（关闭=收起，托盘右键=真退出）" % t_img.size)
    else:
        print("[tray] SKIP 无 Pillow —— 关闭窗口降级为询问是否退出")

    print("== selftest", "PASSED ==" if ok else "FAILED ==")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--tkcheck" in sys.argv:
        import tkinter as _tk
        _r = _tk.Tk(); _r.withdraw()
        print("tkcheck OK tkinter", _tk.TkVersion)
        sys.exit(0)
    run_gui()
