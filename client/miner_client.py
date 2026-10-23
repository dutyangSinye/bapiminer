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
import hashlib
import json
import os
import queue
import re
import shutil
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

# 发版时与 BaPiMiner.spec 的 version 一起改：中控端也用 data/client_release.json 里的同值
CLIENT_VERSION = "1.12.0.3"
EXE_PATH = sys.executable if getattr(sys, "frozen", False) else ""

# ---------------------------------------------------------------- 子进程静默启动
# 打包成 --noconsole 后本进程没有控制台，任何控制台子程序（nvidia-smi / taskkill /
# cmd.exe / 挖矿内核）只要没显式声明 CREATE_NO_WINDOW，Windows 都会给它配一个可见的
# CMD 窗口。统一走下面两个封装，别再直接调 subprocess。

CREATE_NO_WINDOW = 0x08000000          # 不分配控制台
DETACHED_PROCESS = 0x00000008          # ⚠ 不要用于跑 bat/脚本：cmd 无控制台时，
                                       # bat 里的外部命令（tasklist/find/timeout）会各弹一个可见窗口。
                                       # 脚本类一律用 CREATE_NO_WINDOW（隐藏控制台可继承）。
CREATE_NEW_PROCESS_GROUP = 0x00000200

_SI = {"ready": False, "obj": None}


def hidden_startupinfo():
    """STARTF_USESHOWWINDOW + SW_HIDE，兜底某些内核自己 AllocConsole 的情况。"""
    if os.name != "nt":
        return None
    if not _SI["ready"]:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        _SI["obj"] = si
        _SI["ready"] = True
    return _SI["obj"]


def run_hidden(cmd, timeout=10, check=False):
    """跑一条命令并取 stdout，全程不出现窗口。异常/超时返回空串（与原逻辑一致）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=check,
                           startupinfo=hidden_startupinfo(),
                           creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        return (r.stdout or b"").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def popen_hidden(cmd, cwd=None, pipe_out=True, detached=False):
    """静默拉起子进程。pipe_out=True 时接管 stdout+stderr（便于读日志），detached 用于自更新脚本。"""
    flags = 0
    if os.name == "nt":
        flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) if detached else CREATE_NO_WINDOW
    kw = {"cwd": cwd, "startupinfo": hidden_startupinfo(),
          "creationflags": flags, "close_fds": True}
    if pipe_out:
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.STDOUT
    else:
        kw["stdout"] = subprocess.DEVNULL
        kw["stderr"] = subprocess.DEVNULL
        kw["stdin"] = subprocess.DEVNULL
    return subprocess.Popen(cmd, **kw)

# ---------------------------------------------------------------- 预置数据

KERNELS = {
    "SRBMiner-MULTI": {
        "exe": "SRBMiner-MULTI.exe",
        "api_port": 21550,
        "api_type": "srbminer",
        "download": "https://www.srbminer.com/download.html",
        "label": "SRBMiner-MULTI",
        "key": "srbminer",
        "dev_fee": "1%",
        "note": "多卡通用，Pearl 官方推荐内核",
    },
    "BzMiner": {
        "exe": "bzminer.exe",
        "api_port": 4014,
        "api_type": "bzminer",
        "download": "https://www.bzminer.com/",
        "label": "BzMiner",
        "key": "bzminer",
        "dev_fee": "2%",   # 官方 README 对 pearl 算法公示 2%
        "note": "低功耗优先，单卡省电；pearl 开发者费 2%（比 SRBMiner 高）",
    },
    "WildRig": {
        "exe": "wildrig.exe",
        "api_port": 17799,
        "api_type": "wildrig",
        "download": "https://github.com/andru-kun/wildrig-multi/releases",
        "label": "WildRig",
        "key": "wildrig",
        "dev_fee": "0%~2%",
        "note": "支持 AMD / NVIDIA / Intel；pearlhash 对 RTX 30/40/50 系持续优化"
                "（0.51.x），连 pearlhash.xyz 池 0 开发费，其他矿池 2%",
    },
}

# ---------------------------------------------------------------- 矿池与节点
# 节点 region 分级口径（本产品定义，UI 与筛选规则都以此为准）：
#   cn     中国大陆节点   —— 真·直连，零跨境
#   hmt    中国港澳台节点 —— 同属中国，无需代理，延迟通常 < 100ms
#   abroad 海外节点       —— 需走国际出口，延迟与掉线率明显更高
#
# geo 为实测 IP 归属（2026-10 核对），用来识破「域名写着 cn / us、落地却在别处」：
#   cn.pearl.k1pool.com   域名标「cn」，实际落香港 AWS（18.163.123.95）
#   us.pearl.k1pool.com   域名标「us」，实际落加拿大 OVH（51.161.15.41）
# 这两个坑不写清楚，机主会以为自己在连大陆节点。
REGION_CN, REGION_HMT, REGION_ABROAD = "cn", "hmt", "abroad"

POOL_BRANDS = [
    {
        "id": "kryptex", "name": "Kryptex", "fee": "1%",
        "note": "多币种平台，PRL 节点覆盖最广，支持 TLS（同主机 8048）",
        "nodes": [
            {"label": "中国香港", "region": REGION_HMT, "geo": "香港 · 九龙",
             "host": "prl-hk.kryptex.network", "port": 7048},
            {"label": "新加坡", "region": REGION_ABROAD, "geo": "新加坡",
             "host": "prl-sg.kryptex.network", "port": 7048},
            {"label": "欧洲", "region": REGION_ABROAD, "geo": "德国 · 法尔肯施泰因",
             "host": "prl.kryptex.network", "port": 7048},
            {"label": "北美", "region": REGION_ABROAD, "geo": "美国 · 弗吉尼亚",
             "host": "prl-us.kryptex.network", "port": 7048},
            {"label": "俄罗斯", "region": REGION_ABROAD, "geo": "俄罗斯 · 莫斯科",
             "host": "prl-ru.kryptex.network", "port": 7048},
        ],
    },
    {
        "id": "pearlhash", "name": "PearlHash", "fee": "1%",
        "note": "Pearl 官方专用池；配 WildRig 内核 0 开发费。全球 Anycast 单入口自动就近，无大陆机房",
        "nodes": [
            {"label": "全球（Anycast）", "region": REGION_ABROAD, "geo": "AWS Global Accelerator · 实测 ~111ms",
             "host": "pool.pearlhash.xyz", "port": 9000},
        ],
    },
    {
        "id": "k1pool", "name": "K1Pool", "fee": "0%",
        "note": "PPLNS 零费率；「中国线路」实际落地香港，非大陆机房",
        "nodes": [
            {"label": "中国线路", "region": REGION_HMT, "geo": "香港 · AWS",
             "host": "cn.pearl.k1pool.com", "port": 3360},
            {"label": "欧洲", "region": REGION_ABROAD, "geo": "法国 · 格拉沃利讷",
             "host": "eu.pearl.k1pool.com", "port": 3360},
            {"label": "美国", "region": REGION_ABROAD, "geo": "加拿大 · 博阿努瓦",
             "host": "us.pearl.k1pool.com", "port": 3360},
            {"label": "欧洲 SOLO", "region": REGION_ABROAD, "geo": "法国 · 格拉沃利讷",
             "host": "eu.pearlsolo.k1pool.com", "port": 3362},
        ],
    },
    {
        "id": "f2pool", "name": "F2Pool 飞鱼", "fee": "1%",
        "note": "PPLNS；钱包位填 F2Pool 账户名而非 PRL 地址",
        "nodes": [
            {"label": "全球", "region": REGION_ABROAD, "geo": "美国 · 旧金山",
             "host": "pearl.f2pool.com", "port": 5500},
        ],
    },
    {
        "id": "herominers", "name": "HeroMiners", "fee": "1%",
        "note": "PPLNS，界面与统计做得细",
        "nodes": [
            {"label": "全球", "region": REGION_ABROAD, "geo": "美国 · 旧金山",
             "host": "pearl.herominers.com", "port": 1200},
        ],
    },
    {
        "id": "alphapool", "name": "AlphaPool", "fee": "1%",
        "note": "新加坡单节点，亚太线路",
        "nodes": [
            {"label": "新加坡", "region": REGION_ABROAD, "geo": "新加坡",
             "host": "sg1.alphapool.tech", "port": 5571},
        ],
    },
]

CUSTOM_POOL_ID = "custom"
CUSTOM_POOL = {"id": CUSTOM_POOL_ID, "name": "自定义矿池…", "fee": "—",
               "note": "手动填写 stratum 地址", "nodes": []}

TIER_HINT = {
    "cn": "该矿池有中国大陆节点，已只列直连节点",
    "hmt": "该矿池无大陆节点，已只列中国港澳台节点（无需代理，延迟最低）",
    "none": "该矿池无中国直连节点，以下列出全部节点供选择",
}
REGION_TAG = {REGION_CN: "大陆直连", REGION_HMT: "中国港澳台", REGION_ABROAD: "海外"}


def pool_brand(bid):
    if bid == CUSTOM_POOL_ID:
        return CUSTOM_POOL
    for b in POOL_BRANDS:
        if b["id"] == bid:
            return b
    return None


def node_url(n):
    return "stratum+tcp://%s:%d" % (n["host"], n["port"])


def pool_candidates(bid):
    """按「中国直连优先」筛节点。

    返回 (nodes, tier)：
      tier=cn     池内有大陆节点  -> 只给大陆节点
      tier=hmt    无大陆、有港澳台 -> 只给港澳台节点
      tier=none   都没有          -> 给全部节点（并标注无直连）
    """
    b = pool_brand(bid)
    if not b or not b["nodes"]:
        return [], "none"
    for tier in (REGION_CN, REGION_HMT):
        hit = [n for n in b["nodes"] if n["region"] == tier]
        if hit:
            return hit, tier
    return list(b["nodes"]), "none"


def brand_of_url(url):
    """从已保存的 pool_url 反查品牌，找不到就归到自定义。"""
    u = (url or "").strip()
    for b in POOL_BRANDS:
        for n in b["nodes"]:
            if node_url(n) == u:
                return b, n
    return None, None


def short_addr(a):
    """钱包地址缩略：中间省略，两端保留。"""
    a = (a or "").strip()
    return a if len(a) <= 22 else (a[:10] + "…" + a[-8:])


def pool_display(url):
    """给状态区用的一行可读文案，例如「Kryptex · 中国香港（香港 · 九龙）」。"""
    b, n = brand_of_url(url)
    if b and n:
        return "%s · %s" % (b["name"], n["label"])
    return (url or "未设置")


_POOL_URL_RE = re.compile(r"^stratum\+(tcp|ssl)://[A-Za-z0-9._\-]+:\d{1,5}$")


def valid_pool_url(u):
    """stratum 地址格式校验（中控 set_pool 命令与本地自定义矿池共用同一规则）。"""
    u = (u or "").strip()
    if not _POOL_URL_RE.match(u):
        return False
    try:
        return 0 < int(u.rsplit(":", 1)[1]) <= 65535
    except ValueError:
        return False


def probe_node(host, port, timeout=3.5, tries=2):
    """TCP 握手测延迟，返回最小毫秒数；全失败返回 None（= 当前网络不可达）。"""
    best = None
    for _ in range(tries):
        s = socket.socket()
        s.settimeout(timeout)
        t0 = time.time()
        try:
            s.connect((host, port))
            ms = (time.time() - t0) * 1000.0
            if best is None or ms < best:
                best = ms
        except OSError:
            pass
        finally:
            s.close()
    return None if best is None else int(round(best))


def node_display(n, ms=None):
    """节点下拉的显示文本：名称 + 落地 + 延迟。"""
    bits = [n["label"], n["geo"]]
    if ms is None:
        bits.append("未测速")
    elif ms is False:
        bits.append("不可达")
    else:
        bits.append("%dms" % ms)
    return " · ".join(bits)


# 测速结果缓存 {节点 url: 毫秒 | False(不可达)}，主页与设置页共用，避免重复测。
_NODE_MS = {}

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


def default_pool_url():
    """首次启动的默认矿池：Kryptex 的中国区节点。

    选它的依据是实测（本机 2026-10）：香港 62ms / 新加坡 107ms / 德国 161ms /
    美国 240ms，且 K1Pool、F2Pool、HeroMiners 的矿池端口从大陆网络直接超时。
    """
    nodes, _tier = pool_candidates("kryptex")
    return node_url(nodes[0]) if nodes else ""


DEFAULT_CFG = {
    "kernel": "SRBMiner-MULTI",
    "kernel_dir": _default_kernel_dir(),
    "algo": "PRL (Pearl)",
    "pool_url": default_pool_url(),
    "wallet": "",
    "worker": socket.gethostname().lower()[:20],
    "autostart": False,
    "autorestart": True,
    "gamemode": True,      # 游戏模式：检测到游戏进程自动暂停挖矿
    "games": [],           # 额外自定义游戏进程名（小写 exe 名，如 mygame.exe）
    "ignore_miners": [],   # 已确认忽略的「其他挖矿程序」进程名
    "check_other_miners": True,   # 检测本机其他挖矿程序并提示关闭
    "auto_update": True,   # 发现中控有新版本客户端时静默自更新（挖矿会中断约 10 秒）
    "server": "http://mine.sinyerobot.com:8099",  # 中控地址已内置，留空不上报
    "token": "",           # 中控接入口令（留空则自动生成随机口令）
    "rig_name": socket.gethostname().lower(),
}

UNIT_THS = {"H": 1e-12, "K": 1e-9, "M": 1e-6, "G": 1e-3, "T": 1.0, "": 1e-12}

# PROCESSENTRY32W（64 位布局）。注意 pcPriClassBase 在 WinAPI 里是 LONG（4 字节）：
# 曾误写成 c_ssize_t(8 字节) 使结构体尺寸与系统要求不符，Process32FirstW 一律
# 报 ERROR_BAD_LENGTH 拒绝填充 → 进程枚举永远返回 0 → 游戏模式/冲突检测静默失效。
class _PE32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint), ("cntUsage", ctypes.c_uint),
                ("th32ProcessID", ctypes.c_uint),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_uint), ("cntThreads", ctypes.c_uint),
                ("th32ParentProcessID", ctypes.c_uint),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_uint),
                ("szExeFile", ctypes.c_wchar * 260)]


def list_processes():
    """枚举当前所有进程的 exe 文件名（小写列表）。非 Windows 或失败返回空表。"""
    if os.name != "nt":
        return []
    try:
        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if snap in (0, -1):
            log_write("进程快照创建失败 err=%s" % k32.GetLastError())
            return []
        pe = _PE32W()
        pe.dwSize = ctypes.sizeof(_PE32W)
        names = []
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            names.append(pe.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
        k32.CloseHandle(snap)
        if not names:
            log_write("进程枚举为空 err=%s（若反复出现请检查结构体定义）" % k32.GetLastError())
        return names
    except Exception as e:  # noqa: BLE001
        log_write("进程枚举异常: %r" % e)
        return []


UI_TITLE = "扒皮矿工·矿机端"


def _match_app_title(text):
    """Edge app 窗口标题精确等于 UI_TITLE（无浏览器后缀）才算。

    普通浏览器打开同名页面时窗口标题会带「- 个人 - Microsoft Edge」等
    后缀或「和另外 N 个页面」，不能把用户自己的浏览器窗口当成矿机端。
    """
    return (text or "").strip() == UI_TITLE


def _find_app_window():
    """找已有的矿机端 Edge app 窗口（按标题精确匹配），返回 hwnd 或 0。"""
    if os.name != "nt":
        return 0
    try:
        import ctypes.wintypes as _wt
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
        def cb(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if _match_app_title(buf.value):
                        found.append(hwnd)
                        return False
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else 0
    except Exception:  # noqa: BLE001
        return 0


def _close_app_windows():
    """关闭所有矿机端 UI 窗口（Edge app 是独立进程，不会随 os._exit 退出）。

    升级替换时旧实例 os._exit 只杀 Python，Edge 窗口会残留成显示停格
    页面的「僵尸窗口」；这里逐个发 WM_CLOSE 关掉。返回关闭数量。
    """
    if os.name != "nt":
        return 0
    try:
        import ctypes.wintypes as _wt
        user32 = ctypes.windll.user32
        targets = []

        @ctypes.WINFUNCTYPE(_wt.BOOL, _wt.HWND, _wt.LPARAM)
        def cb(hwnd, _lp):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if _match_app_title(buf.value):
                        targets.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        closed = 0
        for h in targets:
            try:
                # WM_CLOSE：走 Edge 正常关窗路径（比 DestroyWindow 温和，无束权问题）
                if user32.PostMessageW(h, 0x0010, 0, 0):
                    closed += 1
            except Exception:  # noqa: BLE001
                pass
        if closed:
            log_write("已关闭 %d 个矿机端 UI 窗口（升级替换）" % closed)
            time.sleep(1.0)   # 给 Edge 一点退出时间，避免新实例复用到将死的窗口
        return closed
    except Exception:  # noqa: BLE001
        return 0


# 升级替换前的 UI 收尾钩子（run_gui 启动时注册：关托盘图标 + 关 UI 窗口）
_UI_TEARDOWN = None


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
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if snap in (0, -1):
            log_write("进程快照(ex)创建失败 err=%s" % k32.GetLastError())
            return []
        pe = _PE32W()
        pe.dwSize = ctypes.sizeof(_PE32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            pid = int(pe.th32ProcessID)
            name = pe.szExeFile.lower()
            if pid and name:
                out.append({"pid": pid, "ppid": int(pe.th32ParentProcessID),
                            "name": name,
                            "path": _proc_image_path(k32, pid)})
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
        k32.CloseHandle(snap)
    except Exception as e:  # noqa: BLE001
        log_write("进程枚举(ex)异常: %r" % e)
        return []
    return out


def kill_orphan_kernels():
    """清掉孤儿挖矿内核（单实例锁在手时才允许调用）。

    正常路径下内核由引擎管理；但升级替换（os._exit 跳过 atexit）、客户端
    崩溃后被看门狗拉起等场景，上一个实例的内核会变成没人管的孤儿继续占卡。
    能走到这里 = 本机没有其他客户端实例，任何在跑的内核都是孤儿，杀掉后
    由本次启动按配置重新拉起。
    """
    if os.name != "nt":
        return
    names = {k.get("exe", "").lower() for k in KERNELS.values() if k.get("exe")}
    if not names:
        return
    k32 = ctypes.windll.kernel32
    killed = []
    for p in list_processes_ex():
        if p["name"] in names:
            try:
                h = k32.OpenProcess(0x0001, False, p["pid"])   # PROCESS_TERMINATE
                if h:
                    k32.TerminateProcess(h, 1)
                    k32.CloseHandle(h)
                    killed.append("%s(pid=%d)" % (p["name"], p["pid"]))
            except Exception:  # noqa: BLE001
                pass
    if killed:
        log_write("清理孤儿内核：%s（将由本次启动重新拉起）" % ", ".join(killed))
        time.sleep(1.0)


def detect_conflicts(own_pids=(), own_dirs=(), procs=None):
    """找本机其他挖矿程序与其他客户端实例。

    返回 [{pid, name, label, path, kind}]，kind: miner（第三方矿工）/ dup（重复实例）。
    自己拉起的矿工进程（PID 或所在内核目录）会被排除。
    """
    procs = procs if procs is not None else list_processes_ex()
    own = set(int(p) for p in own_pids if p)
    dirs = [os.path.normcase(os.path.abspath(d)) for d in (own_dirs or []) if d]
    me = os.getpid()
    # PyInstaller onefile 双进程：引导父进程 → 主进程，同名同 exe。
    # 只排除自身 PID 会把另一半误报成「重复实例」，这里双向排除父子关系。
    my_ppid = 0
    for p in procs:
        try:
            if int(p.get("pid") or 0) == me:
                my_ppid = int(p.get("ppid") or 0)
                break
        except (TypeError, ValueError):
            pass
    family = {me, my_ppid} | set(p for p in own if p)
    out = []
    for p in procs:
        try:
            pid = int(p.get("pid") or 0)
            ppid = int(p.get("ppid") or 0)
        except (TypeError, ValueError):
            continue
        name = (p.get("name") or "").lower()
        if not pid or pid in family:
            continue
        path = p.get("path") or ""
        np = os.path.normcase(os.path.abspath(path)) if path else ""
        if np and any(np == d or np.startswith(d + os.sep) for d in dirs):
            continue      # 自己内核目录里的进程 = 我们自己的矿工
        label = MINER_PROCESSES.get(name)
        if label:
            out.append({"pid": pid, "name": name, "label": label,
                        "path": path, "kind": "miner"})
        elif name in _SELF_NAMES and ppid != me:
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
    elif kernel == "WildRig":
        # wildrig 语法：--algo/--url/--user；worker 名跟在钱包后用点分隔；
        # API 无需 --api-enable，--api-port 直接指定端口（17799 避免与默认 9999 冲突）。
        args = ["--algo", algo, "--url", pool_url, "--user", "%s.%s" % (w, worker),
                "--pass", "x", "--api-port", str(k["api_port"])]
    else:
        # BzMiner：-w 是钱包地址，矿机名必须用 --worker（旧代码写成 -r 会解析错），
        # HTTP API 没有 --http_* 命令行开关，只能走 --set 键值。
        args = ["-a", algo, "-p", pool_url, "-w", w, "--worker", worker,
                "--set", "http_enabled=true", "--set", "http_port=%d" % k["api_port"]]
    return exe, args


# ---------------------------------------------------------------- 采集/解析

def query_gpus():
    cmd = ["nvidia-smi", "--query-gpu=index,name,temperature.gpu,utilization.gpu,"
           "memory.used,memory.total,fan.speed,power.draw",
           "--format=csv,noheader,nounits"]
    out = run_hidden(cmd, timeout=10)
    if not out:
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
    # round 到 9 位：KH/s 级别的值约 1e-8 TH/s，round(x,3) 会直接归零
    return round(cands[0][1], 9)


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
            if ("accepted" in kl and "share" in kl) or kl == "accepted" \
                    or (kl == "good" and "share" in pl):   # wildrig: shares.good
                m["accepted"] += int(v)
            elif "rejected" in kl or (kl == "bad" and "share" in pl):
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


_rate_cache = {"rate": 0.0, "cny": 0.0, "wallet": None, "src": "", "ts": 0.0}


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


# ---------------------------------------------------------------- 客户端自更新
# 中控 /api/client_latest 给出最新 exe 的 sha256；本机 exe 指纹不一致就静默替换重启。
# 替换交给一个 cmd 脚本：用「旧 exe 能否改名」判断进程真正退出，比查 PID 稳。

UPDATE_BAT = "_update_client.bat"
_UPDATE_LOCK_FILE = os.path.join(BASE, ".update.lock")
_UPDATE_THREAD_LOCK = threading.Lock()


def _silent_remove(p):
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def exe_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch_client_latest(server, timeout=12):
    """问中控要最新客户端信息；中控不可达或安装包未就绪时返回 None。"""
    if not server:
        return None
    try:
        d = http_json(server.rstrip("/") + "/api/client_latest", timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    if not (isinstance(d, dict) and d.get("ok") and d.get("ready") and d.get("sha256")):
        return None
    return d


def client_update_status(server):
    """返回 (need_update, local_sha, latest)。源码运行 / 读不到自身指纹时不触发更新。"""
    latest = fetch_client_latest(server)
    if not EXE_PATH or not os.path.isfile(EXE_PATH):
        return False, "", latest
    try:
        local = exe_sha256(EXE_PATH)
    except OSError:
        return False, "", latest
    if not latest:
        return False, local, None
    return latest["sha256"] != local, local, latest


def _update_lock_acquire():
    """升级互斥：进程内锁 + 跨进程标记文件（O_EXCL）。

    中控命令升级与自动检查可能同时触发 apply_client_update，两个替换脚本
    并发 move 会把 exe/bak/.new 状态搞乱，新 exe 启动时报
    Failed to load Python DLL（SINYEBIM/dutyangbim 均实发）。返回 True=拿到锁。
    """
    if not _UPDATE_THREAD_LOCK.acquire(False):
        return False
    try:
        # 清理超过 300 秒的陈旧锁（上次升级中断残留）
        try:
            if os.path.exists(_UPDATE_LOCK_FILE) and \
                    time.time() - os.path.getmtime(_UPDATE_LOCK_FILE) > 300:
                os.remove(_UPDATE_LOCK_FILE)
        except OSError:
            pass
        try:
            fd = os.open(_UPDATE_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except OSError:
            _UPDATE_THREAD_LOCK.release()
            return False
    except Exception:  # noqa: BLE001
        return False


def _update_lock_release():
    try:
        os.remove(_UPDATE_LOCK_FILE)
    except OSError:
        pass
    try:
        _UPDATE_THREAD_LOCK.release()
    except Exception:  # noqa: BLE001
        pass


def _updater_bat(target, old_pid=0):
    """生成替换脚本（纯 ASCII）：旧 exe 改名成功 = 进程已退出 = 文件已解锁。

    注意：Windows 允许对「正在运行」的 exe 改名，move 成功不代表旧进程已死。
    必须等旧 PID 真正消失后再 start 新版，否则新实例会撞上旧进程还持有的
    单实例互斥锁（Local\\BaPiMinerClient），被当成重复实例直接退出，
    表现为「升级成功但程序没再打开」。
    """
    name = os.path.basename(target)
    # 睡眠用 ping 而不是 timeout：bat 的 stdin 被重定向（DEVNULL）时 timeout
    # 行为不可靠（实测卡死过一次，导致升级后新 exe 永远没被拉起）。
    # 两个等待循环都设上限（60 秒），到点照样继续，绝不无限卡死。
    lines = [
        "@echo off",
        'cd /d "%~dp0"',
        "set /a n=0",
        ":wait",
        'move /y "%s" "%s.bak" >nul 2>&1' % (name, name),
        "if errorlevel 1 (",
        "  ping -n 2 127.0.0.1 >nul",
        "  set /a n+=1",
        "  if %n% LSS 60 goto wait",
        ")",
        'move /y "%s.new" "%s" >nul 2>&1' % (name, name),
        "if errorlevel 1 (",
        '  move /y "%s.bak" "%s" >nul 2>&1' % (name, name),
        '  start "" "%s" --update-failed' % name,
        '  del "%~f0"',
        "  exit /b 1",
        ")",
    ]
    if old_pid:
        # 等旧进程真正退出（最多约 60 秒），再拉起新版；到点强行走，
        # 万一旧进程还活着，新实例最多被单实例锁拦一下，好过永远卡死
        lines += [
            "set /a m=0",
            ":waitpid",
            'tasklist /FI "PID eq %d" 2>nul | find /I "BaPiMiner" >nul' % old_pid,
            "if not errorlevel 1 (",
            "  ping -n 2 127.0.0.1 >nul",
            "  set /a m+=1",
            "  if %m% LSS 60 goto waitpid",
            ")",
        ]
    lines += [
        'start "" "%s"' % name,
        "set /a k=0",
        ":chknew",
        "ping -n 3 127.0.0.1 >nul",
        'tasklist /FI "IMAGENAME eq %s" 2>nul | find /I "BaPiMiner" >nul' % name,
        "if not errorlevel 1 goto upok",
        "set /a k+=1",
        "if %k% LSS 6 goto chknew",
        # 新版本约 18 秒内没能常驻（如 Failed to load Python DLL / 被安全软件拦截）
        # → 自动回滚旧版再拉起，保证矿机不掉线
        'move /y "%s" "%s.bad" >nul 2>&1' % (name, name),
        'move /y "%s.bak" "%s" >nul 2>&1' % (name, name),
        'start "" "%s"' % name,
        ":upok",
        'del "%~f0"',
    ]
    p = os.path.join(BASE, UPDATE_BAT)
    with open(p, "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    return p


def apply_client_update(server, latest, emit=None, before_exit=None):
    """下载 -> 校验 -> 落地 -> 拉起替换脚本。成功返回 (True, 说明)，此时调用方应尽快退出。"""
    def log(m):
        if emit:
            try:
                emit(event="log", msg=m)
            except Exception:  # noqa: BLE001
                pass

    if not _update_lock_acquire():
        return False, "已有升级任务在执行，跳过本次重复触发"
    if not EXE_PATH:
        _update_lock_release()
        return False, "当前为源码运行模式，不执行自更新"
    if not latest or not latest.get("sha256"):
        _update_lock_release()
        return False, "没有可用的新版本信息"
    target = EXE_PATH
    # part 文件名带 PID：避免两个升级流程并发写同一临时文件互相毁内容
    part = "%s.new.part.%d" % (target, os.getpid())
    url = server.rstrip("/") + (latest.get("url") or "/download/BaPiMiner.exe")
    try:
        n = http_download(url, part, timeout=900)
    except Exception as e:  # noqa: BLE001
        _silent_remove(part)
        _update_lock_release()
        return False, "下载失败：%s" % e
    try:
        got = exe_sha256(part)
    except OSError as e:
        _silent_remove(part)
        _update_lock_release()
        return False, "读取下载文件失败：%s" % e
    if got != latest["sha256"]:
        _silent_remove(part)
        _update_lock_release()
        return False, "校验不一致（收到 %d 字节，期望 %s…）" % (n, latest["sha256"][:12])
    try:
        os.replace(part, target + ".new.exe")
    except OSError as e:
        _silent_remove(part)
        _update_lock_release()
        return False, "落地新版本失败：%s" % e
    log("🔄 新版本 %s 校验通过，正在替换并重启…" % (latest.get("version") or ""))
    if before_exit:
        try:
            before_exit()
        except Exception:  # noqa: BLE001
            pass
    try:
        # 自替换（v1.11.2.5 起）：不再用 bat 在磁盘上倒腾文件——那个机制的
        # 「start 一个刚被换入的 exe」环节反复触发 Failed to load Python DLL。
        # 现在先直接启动新 exe（文件是刚校验过的完整体，启动即证明可用），
        # 由它在新进程里等旧进程退出后把自己改名到位再拉起正式版。
        popen_hidden([target + ".new.exe", "--self-replace", target],
                     cwd=BASE, pipe_out=False, detached=False)
    except Exception as e:  # noqa: BLE001
        _update_lock_release()
        return False, "启动新版本失败：%s" % e
    # UI 窗口/托盘是独立进程资源，os._exit 带不走，立即收掉（新实例会重开）
    try:
        if _UI_TEARDOWN:
            _UI_TEARDOWN()
    except Exception:  # noqa: BLE001
        pass
    # 成功路径刻意不释放锁：替换由新进程接管，锁文件由新进程启动时清理，
    # 防止等待期间又触发一次升级。
    return True, "正在重启到 %s" % (latest.get("version") or "新版本")


def start_update_watcher(get_cfg, emit=None, stop_miner=None):
    """后台看门狗：启动 40 秒查一次版本，之后每 6 小时一次；有新版就静默替换重启。"""
    def loop():
        time.sleep(40)
        while True:
            try:
                cfg = get_cfg() or {}
                if cfg.get("auto_update", True) and cfg.get("server"):
                    need, _local, latest = client_update_status(cfg["server"])
                    if need:
                        if emit:
                            emit(event="log", msg="发现新版本客户端 %s，开始静默升级…"
                                 % (latest.get("version") or ""))
                        ok, note = apply_client_update(cfg["server"], latest,
                                                       emit=emit, before_exit=stop_miner)
                        if emit:
                            emit(event="log", msg=("✅ " if ok else "⚠ 升级跳过：") + note)
                        if ok:
                            if emit:
                                try:
                                    emit(event="log", msg="✅ 升级完成，即将重启到新版本")
                                except Exception:  # noqa: BLE001
                                    pass
                            log_write("自动升级 %s 完成，3 秒后退出交由替换脚本重启"
                                      % (latest.get("version") or ""))
                            time.sleep(3)
                            os._exit(0)   # 替换脚本已在等本进程退出
            except Exception:  # noqa: BLE001
                pass
            time.sleep(6 * 3600)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


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
        elif "502" in msg or "Bad Gateway" in msg or "timed out" in msg \
                or "TimeoutError" in msg or "URLError" in msg or "getaddrinfo failed" in msg:
            msg = "中控无法连接（NAS 可能离线/重启中，或网络波动），恢复后会自动重试"
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


def refresh_rates(server="", token="", rig=""):
    """向中控拉收益口径（全网均价 USDT/TH/s/天 + 实时汇率 + 本机矿池钱包）。10 分钟缓存。

    与看板完全同源：vminers.com PRL 实测榜加权均价 × 实时 USDT/CNY 汇率。
    """
    if time.time() - _rate_cache["ts"] < 600:
        return
    if not server or not token:
        return
    try:
        url = (server.rstrip("/") + "/api/income_meta?rig="
               + urllib.parse.quote(rig or socket.gethostname().lower()))
        d = http_json(url, timeout=12, headers={"X-Rig-Token": token})
        if d.get("ok"):
            _rate_cache["rate"] = float(d.get("rate_avg_usdt") or 0)
            _rate_cache["cny"] = float(d.get("cny_rate") or 0)
            _rate_cache["wallet"] = d.get("wallet") or None
            _rate_cache["src"] = (d.get("source") or "").strip()
            _rate_cache["ts"] = time.time()
    except Exception:  # noqa: BLE001
        _rate_cache["ts"] = time.time() - 540   # 1 分钟后重试


def income_rates(server="", token="", rig=""):
    refresh_rates(server, token, rig)
    return _rate_cache.get("rate") or 0.0, _rate_cache.get("cny") or 0.0


def estimate_income(model, ths, server="", token="", rig=""):
    """日收益估算 = 实测总算力 × 全网均价(USDT/TH/s/天) × 实时汇率。

    与显卡型号无关（同币种每 TH/s 收益基本一致），型号仅用于展示参考。
    """
    rate, cny = income_rates(server, token, rig)
    if rate and cny:
        return round(ths * rate * cny, 2)
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
        self._cfg_ver = 0            # 配置版本号：中控改配置时 +1，主循环据此重建启动命令
        self.persist = None          # 配置落盘回调（GUI 注入；测试环境可为 None）
        self._threads = []
        self.starts = 0
        self.stats = {"ths": 0.0, "accepted": 0, "rejected": 0, "uptime": None,
                      "income": None, "income_rate": 0.0, "income_cny": 0.0, "wallet": None,
                      "gpus": [], "miner_alive": False, "error": None}

    def emit(self, **kw):
        self.q.put(kw)

    def _persist_cfg(self):
        """把运行中的配置变更落盘（本地 GUI 与下次启动都读这份；未注入回调则跳过）。"""
        cb = getattr(self, "persist", None)
        if not cb:
            return
        try:
            cb()
        except Exception as e:  # noqa: BLE001
            log_write("配置落盘失败: %s" % e)

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
        """轮询中控命令队列（30 秒一轮），支持远程游戏模式开关 / 停止启动矿工 /
        更换矿池(set_pool) / 切换内核(set_kernel) / 内核与客户端自更新。"""
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
                    arg = c.get("arg")
                    if isinstance(arg, str):       # 中控库里存的是 JSON 字符串
                        try:
                            arg = json.loads(arg)
                        except ValueError:
                            arg = {}
                    ok, note = self._handle_cmd(c.get("cmd") or "", arg or {})
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

    def _handle_cmd(self, cmd, arg=None):
        """执行中控命令，返回 (ok, 备注)。arg 为命令自带参数（JSON 对象）。"""
        arg = arg or {}
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
        if cmd == "update_client":
            srv = self.cfg.get("server") or ""
            need, _local, latest = client_update_status(srv)
            if not latest:
                return False, "中控上没有可用的客户端安装包"
            if not need:
                return True, "客户端已是最新（v%s）" % CLIENT_VERSION
            ok, note = apply_client_update(srv, latest, emit=self.emit,
                                           before_exit=self._kill)
            if ok:
                # 先让调用方把回执发出去，再退出，替换脚本会拉起新版本
                threading.Thread(
                    target=lambda: (time.sleep(3), os._exit(0)), daemon=True).start()
            return ok, note
        if cmd == "set_pool":
            # 更换矿池：改配置 → 落盘 → 杀矿工，引擎主循环按新配置重启
            url = str(arg.get("pool_url") or "").strip()
            if not valid_pool_url(url):
                return False, "矿池地址格式应为 stratum+tcp://主机:端口"
            if url == (self.cfg.get("pool_url") or "").strip():
                return True, "矿池未变化（%s）" % pool_display(url)
            old = pool_display(self.cfg.get("pool_url") or "")
            self.cfg["pool_url"] = url
            self._persist_cfg()
            self._cfg_ver += 1
            self._kill()
            self.emit(event="log", msg="⛏ 中控切换矿池：%s → %s，矿工重启中" % (
                old, pool_display(url)))
            self.emit(event="cfg_sync", pool_url=url)
            return True, "矿池已切换为 %s，矿工重启中（约 1 分钟恢复）" % pool_display(url)
        if cmd == "set_kernel":
            # 切换内核：先确保内核已安装（没有则从中控下载），失败就不动配置
            key = str(arg.get("kernel") or "").strip()
            if key not in KERNELS:
                return False, "未知内核: %s（可用：%s）" % (key, "、".join(KERNELS))
            if key == self.cfg.get("kernel"):
                return True, "已在使用内核 %s" % KERNELS[key]["label"]
            err, new_v = ensure_kernel(key, self.cfg.get("kernel_dir") or "",
                                       self.cfg.get("server"), self.cfg.get("token"),
                                       emit=self.emit, rig_name=self.cfg.get("rig_name") or "")
            if err:
                return False, "内核 %s 不可用：%s" % (KERNELS[key]["label"], err)
            old = (KERNELS.get(self.cfg.get("kernel")) or {}).get("label") \
                or self.cfg.get("kernel") or "未知"
            self.cfg["kernel"] = key
            self._persist_cfg()
            self._cfg_ver += 1
            self._kill()
            self.emit(event="log", msg="🔧 中控切换内核：%s → %s（v%s），矿工重启中" % (
                old, KERNELS[key]["label"], new_v or "?"))
            self.emit(event="cfg_sync", kernel=key)
            return True, "内核已切换为 %s，矿工重启中（约 1 分钟恢复）" % KERNELS[key]["label"]
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
                run_hidden(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=10)
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

    def _start_log_reader(self):
        """内核日志泵线程：阻塞式 readline 只发生在这个 daemon 线程里。

        曾经在引擎主循环里直接 readline()——内核输出安静（稳定挖矿时统计表
        间隔拉长）会把主循环无限阻塞：不采集、不上报，中控显示离线而内核
        仍在挖（sinyebim 事故）。泵线程阻塞无害，内核退出时 EOF 自行结束。
        """
        proc = self.proc

        def pump():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip()[:200]
                    if line:
                        self.emit(event="log", msg=line)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=pump, daemon=True, name="kernel-log").start()

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
        cmd_ver = self._cfg_ver    # 启动命令对应的配置版本；中控改配置后会不等
        restarts = 0
        fast_fails = 0        # 连续「启动即退出」次数，用于熔断，避免无意义地无限重启

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
                        rc = self.proc.returncode
                        alive = time.time() - (getattr(self, "_proc_t0", 0) or time.time())
                        # 启动几十秒就退，算「起不来」；能跑满 1 分钟才算正常重启
                        fast_fails = (fast_fails + 1) if alive < 60 else 0
                        restarts += 1
                        self.emit(event="log", msg="矿工进程退出(rc=%s)，%.0f 秒后自动拉起（第 %d 次）"
                                  % (rc, 10, restarts))
                        self._kill()
                        if fast_fails >= 6:
                            self.state = "error"
                            self.emit(event="error", msg=(
                                "矿工内核连续 %d 次启动后立即退出（最近退出码 %s），已暂停自动重启。\n"
                                "常见原因：显卡驱动与内核版本不兼容、内核目录被安全软件拦截、"
                                "或内核缺少运行库。请检查后在左上角重新点「开始挖矿」。")
                                % (fast_fails, rc))
                            return
                        if self.stop_flag.wait(10):
                            break
                    # 中控改过配置（换矿池/换内核）：按新配置重建启动命令再拉起
                    if self._cfg_ver != cmd_ver:
                        try:
                            exe, args = build_cmd(self.cfg["kernel"], self.cfg["kernel_dir"],
                                                  self.cfg["algo"], self.cfg["pool_url"],
                                                  self.cfg["wallet"], self.cfg["worker"])
                        except ValueError as e:
                            self.state = "error"
                            self.emit(event="error", msg=str(e))
                            return
                        k = KERNELS[self.cfg["kernel"]]
                        api_port, api_type = k["api_port"], k["api_type"]
                        self.kernel_ver = kernel_local_version(
                            self.cfg["kernel"], self.cfg.get("kernel_dir") or "")
                        cmd_ver = self._cfg_ver
                        self.emit(event="log", msg="配置已变更，按新配置启动：%s %s"
                                  % (exe, " ".join(args)))
                    self.emit(event="log", msg="启动: %s %s" % (exe, " ".join(args)))
                    # 静默启动：不分配控制台、窗口隐藏，避免每台矿机桌面挂个黑框 CMD
                    self.proc = popen_hidden([exe] + args,
                                             cwd=os.path.dirname(exe), pipe_out=True)
                    self._start_log_reader()
                    self._proc_t0 = time.time()
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
                    "income": estimate_income(model, mi.get("ths") or 0.0,
                                              self.cfg.get("server"), self.cfg.get("token"),
                                              self.cfg.get("rig_name") or ""),
                    "income_rate": _rate_cache.get("rate") or 0.0,
                    "income_cny": _rate_cache.get("cny") or 0.0,
                    "wallet": _rate_cache.get("wallet"),
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
                                      "client_version": CLIENT_VERSION,
                                      "hostname": socket.gethostname(),
                                      "ts": time.time(),
                                      "game_mode": bool(hold),
                                      "game_proc": self.game_proc if hold else None,
                                      "kernel": self.cfg.get("kernel"),
                                      "kernel_ver": self.kernel_ver or kernel_local_version(
                                          self.cfg.get("kernel"), self.cfg.get("kernel_dir") or ""),
                                      "pool_url": self.cfg.get("pool_url") or "",
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

                # 内核日志已由 _start_log_reader 的泵线程转发，主循环不再阻塞读取
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


WATCHDOG_TASK = "BaPiMinerWatchdog"


def install_watchdog():
    """注册 Windows 计划任务：每 3 分钟检查客户端进程，意外退出就自动拉起。

    矿机场景里客户端一旦死亡就没人上报（内核是孤儿继续挖，中控失联，
    只能人工发现）。看门狗把「意外死亡」的最长失联时间压到 3 分钟。
    以当前用户注册（交互会话），失败只写日志不抛异常。
    任务本体跑 wscript 静默包装器：直接跑 bat 会每 3 分钟在桌面闪一次
    cmd 黑框（计划任务启动的是交互式 cmd.exe）。
    """
    if os.name != "nt" or not EXE_PATH:
        return
    try:
        bat = os.path.join(BASE, "_watchdog.bat")
        with open(bat, "w", encoding="ascii", newline="\r\n") as f:
            # 全量 tasklist 模糊匹配：升级窗口期进程名是 BaPiMiner.new.exe，
            # 精确匹配会把它当「死了」而误拉旧版，打断自替换
            f.write("\r\n".join([
                "@echo off",
                "tasklist /FO CSV 2>nul | find /I \"BaPiMiner\" >nul",
                "if errorlevel 1 start \"\" \"%s\"" % EXE_PATH,
            ]) + "\r\n")
        vbs = os.path.join(BASE, "_watchdog_run.vbs")
        with open(vbs, "w", encoding="ascii", newline="\r\n") as f:
            # Run(…, 0, False)：隐藏窗口运行 bat，桌面不再闪 cmd 黑框
            f.write('CreateObject("Wscript.Shell").Run """%s""", 0, False\r\n' % bat)
        subprocess.run(
            ["schtasks", "/Create", "/TN", WATCHDOG_TASK,
             "/TR", 'wscript.exe //B "%s"' % vbs, "/SC", "MINUTE", "/MO", "3", "/F"],
            capture_output=True, timeout=20,
            creationflags=CREATE_NO_WINDOW)
        log_write("看门狗计划任务已注册（每 3 分钟自检，静默运行）")
    except Exception as e:  # noqa: BLE001
        log_write("看门狗注册失败: %s" % e)


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
    """Web 壳：引擎/看门狗/自更新/矿池规则全部保留，UI 由本地 HTTP + Edge app 窗口承载。

    传输设计（pywebview 的 pythonnet 编组在本机实测不可靠，弃用其 js_api）：
      Python → JS：出站队列，JS 每 400ms fetch /<token>/api/poll?since=N 增量拉取
      JS → Python：POST /<token>/api/action {op, args}
    窗口：msedge --app=（Win11 自带 Chromium 壳）。关窗 ≠ 退出：挖矿继续，托盘可重开。
    """
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    # -- 单实例（重复实例会各拉一个矿工抢同一张卡，算力不升反降）
    if not acquire_single_instance():
        log_write("启动被拦：已有一个实例在运行（单实例互斥锁被占）")
        # 把已有实例的窗口调到前台，而不是弹「已在运行」错误框——
        # 用户双击多次的预期就是「调出界面」，不是报错
        hwnd = _find_app_window()
        if hwnd:
            try:
                u32 = ctypes.windll.user32
                u32.ShowWindow(hwnd, 9)          # SW_RESTORE
                u32.SetForegroundWindow(hwnd)
                return 1
            except Exception:  # noqa: BLE001
                pass
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "本机已经有一个 扒皮矿工·矿机端在运行。\n\n"
               "同时跑多个实例会互相抢显卡，算力不会翻倍，反而会明显下降。\n"
               "请切换到已打开的那个窗口（或先关掉它再启动本程序）。",
            "扒皮矿工已在运行", 0x00000030)
        return 1

    cfg = {}
    if os.path.isfile(CONFIG_FILE):
        try:
            cfg = json.load(open(CONFIG_FILE, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
    for k, v in DEFAULT_CFG.items():
        cfg.setdefault(k, v)
    if not cfg.get("server"):
        cfg["server"] = DEFAULT_CFG["server"]
    if cfg.get("kernel_dir") and not any(
            os.path.isfile(os.path.join(cfg["kernel_dir"], k["exe"]))
            for k in KERNELS.values()):
        cfg["kernel_dir"] = DEFAULT_CFG["kernel_dir"]
    if not cfg.get("token"):
        cfg["token"] = "rig-" + os.urandom(8).hex()
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    q = queue.Queue()
    engine = MiningEngine(cfg, q)

    # ================================================================
    # 状态仓：原 Tk 变量 → 纯 Python 状态 + 出站队列
    # ================================================================
    S = {
        "kernel": cfg["kernel"],
        "brand": None,               # 当前矿池品牌 id
        "nodes": [], "shown": [],    # 品牌下的候选节点与展示文本
        "fields": {                  # 输入框实时回传（等价原 tk.StringVar）
            "wallet": cfg["wallet"], "games": ", ".join(cfg.get("games") or []),
            "server": cfg["server"], "token": cfg["token"],
            "rig_name": cfg["rig_name"], "worker": cfg["worker"],
            "kernel_dir": cfg["kernel_dir"], "pool_url": cfg["pool_url"],
        },
        "toggles": {
            "autostart": bool(cfg.get("autostart")),
            "autostart_mining": bool(cfg.get("autostart_mining")),
            "gamemode": bool(cfg.get("gamemode")),
            "autorestart": bool(cfg.get("autorestart")),
            "check_other_miners": bool(cfg.get("check_other_miners", True)),
            "auto_update": bool(cfg.get("auto_update", True)),
        },
    }
    latest_meta = {}            # kernel_key -> 中控版本条目
    conflict_items = []
    log_buf = []                # 最近日志（页面重开时回放）
    quitting = [False]
    close_hits = [0.0]
    # ---- 出站队列（Python → JS 轮询）----
    _ob_lock = threading.Lock()
    _outbox = []
    _ob_ver = [0]
    _ui_token = os.urandom(12).hex()

    def push(obj):
        with _ob_lock:
            _outbox.append(obj)
            _ob_ver[0] += 1
            if len(_outbox) > 600:          # 客户端掉线太久就截断，前端会自愈重拉
                del _outbox[:len(_outbox) - 600]

    def poll_outbox(since):
        with _ob_lock:
            ver = _ob_ver[0]
            n = len(_outbox)
            # since 是与 _ob_ver 同语义的累计游标。cap 截断后 len(_outbox) < ver，
            # 必须换算成列表尾部偏移；曾用 _outbox[since:] 直接当索引用——
            # ver 超过 cap 后切片越界返回空列表，UI 永远收不到新推送（数据全 0）。
            if since < 0 or since > ver or ver - since > n:
                return {"ver": ver, "reset": True,
                        "items": list(_outbox[-300:])}
            return {"ver": ver, "reset": False,
                    "items": _outbox[n - (ver - since):]}

    # ---------------- 日志 ----------------
    def tag_of(msg):
        if msg.startswith(("✓", "已恢复", "游戏模式已开启")):
            return "ok"
        if msg.startswith(("错误", "失败", "✕")) or "失败:" in msg or "失败: " in msg:
            return "err"
        if msg.startswith(("🎮",)):
            return "gm"
        if msg.startswith(("⬇", "提示", "首次", "⟳")):
            return "warn"
        return "dim"

    def append_log(msg):
        entry = {"t": "log", "ts": time.strftime("%H:%M:%S"),
                 "msg": msg, "tag": tag_of(msg)}
        log_buf.append(entry)
        if len(log_buf) > 400:
            del log_buf[:len(log_buf) - 400]
        push(entry)

    def clear_log():
        del log_buf[:]
        push({"t": "log_clear"})

    # ---------------- 通用 ----------------
    def is_mining():
        return bool(engine.proc and engine.proc.poll() is None)

    def persist_cfg():
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as e:
            append_log("配置保存失败: %s" % e)

    engine.persist = persist_cfg      # 中控远程改配置时由引擎线程落盘

    def parse_games(txt):
        return [x.strip().lower() for x in re.split(r"[,，;；\s]+", txt or "") if x.strip()]

    def set_running(on):
        push({"t": "runbtn", "on": on})
        push({"t": "pill",
              "text": "运行中" if on else "已停止",
              "kind": "run" if on else "stop"})

    # ---------------- 挖矿配置卡（内核 chips） ----------------
    def kchips_payload():
        k = KERNELS.get(S["kernel"]) or {}
        ok, ver, _exe = kernel_installed(S["kernel"], S["fields"]["kernel_dir"])
        return {"t": "kchips",
                "items": {key: {"label": kk["label"]} for key, kk in KERNELS.items()},
                "active": S["kernel"],
                "hint": "%s · 开发者费 %s · %s" % (
                    k.get("label") or S["kernel"], k.get("dev_fee") or "—",
                    ("已装 v%s" % ver) if ok else "未安装，挖矿前会自动下载"),
                "hint_kind": "" if ok else "amb"}

    def refresh_kchips():
        push(kchips_payload())

    def pick_kernel(key):
        if key not in KERNELS or S["kernel"] == key:
            return
        S["kernel"] = key
        cfg["kernel"] = key
        persist_cfg()
        refresh_kchips()
        refresh_kernels()
        append_log("内核已切换为 %s%s" % (
            KERNELS[key]["label"],
            "，停止再开始挖矿后生效" if is_mining() else ""))

    # ---------------- 矿池品牌 / 节点（中国直连优先） ----------------
    brand_choices = [("%s  %s" % (b["name"], b["fee"]), b["id"]) for b in POOL_BRANDS]
    brand_choices.append((CUSTOM_POOL["name"], CUSTOM_POOL_ID))

    def brand_id_of_url(url):
        b, _n = brand_of_url(url)
        return b["id"] if b else CUSTOM_POOL_ID

    def shown_text(n):
        u = node_url(n)
        return node_display(n, _NODE_MS[u] if u in _NODE_MS else None)

    def pool_payload(hint=None, hint_kind="mut", speed_disabled=False, speed_label="测速"):
        bid = S["brand"]
        custom = bid == CUSTOM_POOL_ID
        return {"t": "pool",
                "brands": [{"id": b_, "name": lbl} for lbl, b_ in brand_choices],
                "brand": bid,
                "nodes": [{"url": node_url(n), "label": shown_text(n)} for n in S["nodes"]],
                "node": S["fields"]["pool_url"],
                "custom_visible": custom,
                "custom_value": S["fields"]["pool_url"],
                "hint": hint or "", "hint_kind": hint_kind,
                "speed_disabled": speed_disabled or custom,
                "speed_label": speed_label}

    def refresh_node_list(keep_url=None):
        bid = S["brand"]
        if bid == CUSTOM_POOL_ID:
            S["nodes"], S["shown"] = [], []
            push(pool_payload(hint="自定义：填写 stratum+tcp://主机:端口"))
            return
        nodes, tier = pool_candidates(bid)
        S["nodes"] = nodes
        S["shown"] = [shown_text(n) for n in nodes]
        target = keep_url if any(node_url(n) == keep_url for n in nodes) else \
            (node_url(nodes[0]) if nodes else "")
        if target:
            S["fields"]["pool_url"] = target
        b = pool_brand(bid)
        hidden = len(b["nodes"]) - len(nodes) if b else 0
        hint = TIER_HINT[tier] + ("；已隐藏 %d 个海外节点" % hidden if hidden else "")
        push(pool_payload(hint=hint,
                          hint_kind="grn" if tier in (REGION_CN, REGION_HMT) else "amb"))

    def api_set_brand(bid):
        if bid not in [c[1] for c in brand_choices]:
            return
        S["brand"] = bid
        refresh_node_list()
        if bid != CUSTOM_POOL_ID and S["nodes"]:
            append_log("矿池切换为 %s，节点 %s（保存后重新开始挖矿生效）" % (
                [lbl for lbl, b in brand_choices if b == bid][0],
                shown_text(S["nodes"][0])))

    def api_pick_node(url):
        if S["brand"] == CUSTOM_POOL_ID:
            return
        for n in S["nodes"]:
            if node_url(n) == url:
                S["fields"]["pool_url"] = url
                push(pool_payload())
                return

    def do_speedtest():
        nodes = list(S["nodes"])
        if not nodes or S["brand"] == CUSTOM_POOL_ID:
            return
        push(pool_payload(speed_disabled=True, speed_label="测速中…"))

        def work():
            res = {node_url(n): probe_node(n["host"], n["port"]) for n in nodes}
            _NODE_MS.update(res)
            q.put({"event": "_speedtest_apply"})
        threading.Thread(target=work, daemon=True).start()

    def speedtest_apply():
        refresh_node_list(S["fields"]["pool_url"])
        ok = sum(1 for n in S["nodes"] if _NODE_MS.get(node_url(n)))
        append_log("节点测速完成：%d/%d 可达" % (ok, len(S["nodes"])))

    # ---------------- 收益 ----------------
    def income_payload(rate=None, cny=None, wallet=None):
        wl = wallet if wallet is not None else _rate_cache.get("wallet")
        meta = "等待中控同步收益率…"
        if rate and cny:
            meta = "总算力 × 全网均价 %.4f USDT/TH/s/天 × 汇率 %.2f（vminers PRL 实测榜）" % (
                rate, cny)
        disp, disp2, bar, note = "—", "—", 0, "绑定钱包后自动同步矿池余额"
        if wl:
            coin = (wl.get("coin") or "prl").upper()
            disp2 = "%s %s" % (
                ("%.4f" % wl["total"]) if wl.get("total") is not None else "—", coin)
            bar = wl.get("pct") or 0
            note = "已到起付线 %.2f %s 的 %.0f%%%s" % (
                wl.get("threshold") or 0, coin, bar,
                "，达到后矿池自动打款" if bar < 100 else "，已可打款")
        return {"t": "income", "disp": disp, "meta": meta,
                "disp2": disp2, "bar": bar, "note": note}

    def update_income_ui(rate=None, cny=None, wallet=None):
        push(income_payload(rate, cny, wallet))

    def refresh_income_async():
        if quitting[0]:
            return
        sv, tv, rv = (S["fields"]["server"].strip(), S["fields"]["token"].strip(),
                      S["fields"]["rig_name"].strip())
        if sv and tv:
            def work():
                refresh_rates(sv, tv, rv)
                q.put({"event": "income_meta"})
            threading.Thread(target=work, daemon=True).start()
        t = threading.Timer(300, refresh_income_async)
        t.daemon = True
        t.start()

    # ---------------- 系统设置：内核管理 ----------------
    def kernel_rows_payload():
        rows = []
        for key, k in KERNELS.items():
            ok, ver, _exe = kernel_installed(key, S["fields"]["kernel_dir"])
            meta = latest_meta.get(key) or {}
            lv = str(meta.get("version") or "")
            if ok:
                st, dot_ok = "已安装 v%s" % (ver or "未知"), True
            else:
                st, dot_ok = "未安装", False
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
                bits.append("点「检测最新版」查看可用版本")
            if not ok:
                btn_text, btn_kind = "安装", "soft"
            elif lv and ver and ver != lv:
                btn_text, btn_kind = "更新到 v%s" % lv, "soft"
            else:
                btn_text, btn_kind = "重装", "ghost"
            rows.append({"key": key, "label": k["label"], "note": k.get("note") or "",
                         "ok": dot_ok, "st": st,
                         "info": "  ·  ".join(bits), "btn_text": btn_text,
                         "btn_kind": btn_kind, "sel": S["kernel"] == key})
        return rows

    def refresh_kernels():
        push({"t": "kernel_rows", "rows": kernel_rows_payload()})

    def select_kernel(key):
        if key not in KERNELS:
            return
        S["kernel"] = key
        cfg["kernel"] = key
        refresh_kchips()
        refresh_kernels()

    def do_kernel_action(key, force=False):
        if key not in KERNELS:
            return
        select_kernel(key)
        k = KERNELS[key]
        ok, ver, _exe = kernel_installed(key, S["fields"]["kernel_dir"])
        target = str((latest_meta.get(key) or {}).get("version") or "")
        if ok and ver and target and ver == target and not force:
            push({"t": "confirm",
                  "msg": "%s 已是中控最新版 v%s。\n\n仍要重新下载覆盖吗？" % (k["label"], ver),
                  "action": "kernel_action", "arg": key})
            return
        append_log("⬇ 正在从中控获取 %s ..." % k["label"])

        def work():
            running = bool(engine.proc and engine.proc.poll() is None)
            if running:
                engine._kill()
                time.sleep(1.5)
                q.put({"event": "log", "msg": "已暂停矿工，准备替换内核文件"})
            err, nv = ensure_kernel(key, S["fields"]["kernel_dir"].strip(),
                                    S["fields"]["server"].strip(),
                                    S["fields"]["token"].strip(),
                                    emit=lambda **kw: q.put(kw),
                                    rig_name=S["fields"]["rig_name"].strip() or "",
                                    force=True)
            if err:
                q.put({"event": "log", "msg": "✕ 内核操作失败：%s" % err})
            else:
                engine.kernel_ver = nv
                q.put({"event": "log", "msg": "✓ %s 就绪（v%s）%s" % (
                    k["label"], nv or "未知",
                    "，矿工将用新内核自动重启" if running else "")})
            q.put({"event": "refresh_kernels"})
        threading.Thread(target=work, daemon=True).start()

    def refresh_latest():
        sv, tv = S["fields"]["server"].strip(), S["fields"]["token"].strip()

        def work():
            q.put({"event": "log", "msg": "⟳ 正在向中控查询最新内核版本..."})
            cat = fetch_kernel_catalog(sv, tv)
            if not cat:
                q.put({"event": "log",
                       "msg": "✕ 未取到版本信息（中控离线，或中控还没检查过内核）"})
                return
            q.put({"event": "latest_kernels", "catalog": cat})
        threading.Thread(target=work, daemon=True).start()

    # ---------------- 冲突提示条 ----------------
    def on_kill_conflicts():
        items = list(conflict_items)
        if not items:
            return
        res = kill_pids([c["pid"] for c in items])
        okn = sum(1 for v in res.values() if v)
        append_log("✓ 已关闭 %d 个冲突进程（共 %d 个）" % (okn, len(items)))
        conflict_items.clear()
        push({"t": "conflict", "on": False, "text": ""})

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
        push({"t": "conflict", "on": False, "text": ""})
        append_log("已忽略冲突提醒：%s" % "、".join(names))

    # ---------------- 配置读写 ----------------
    def current_cfg():
        c = dict(cfg)
        f, t = S["fields"], S["toggles"]
        c.update({
            "kernel": S["kernel"], "kernel_dir": f["kernel_dir"].strip(),
            "algo": "PRL (Pearl)",
            "pool_url": f["pool_url"].strip(),
            "wallet": f["wallet"].strip(), "worker": f["worker"].strip() or "rig",
            "autostart": t["autostart"], "autostart_mining": t["autostart_mining"],
            "autorestart": t["autorestart"], "gamemode": t["gamemode"],
            "check_other_miners": t["check_other_miners"], "auto_update": t["auto_update"],
            "games": parse_games(f["games"]),
            "server": f["server"].strip(), "token": f["token"].strip(),
            "rig_name": f["rig_name"].strip() or socket.gethostname().lower(),
        })
        return c

    def save_cfg(quiet=False):
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
        push({"t": "rig", "wallet": short_addr(cfg["wallet"]) or "未设置"})
        if not quiet:
            append_log("✓ 设置已保存")
            push({"t": "toast", "kind": "",
                  "msg": "设置已保存。修改矿池/钱包后请重新开始挖矿生效。"})

    # ---------------- 启停 ----------------
    def on_start():
        try:
            save_cfg(quiet=True)
            build_cmd(cfg["kernel"], cfg["kernel_dir"], cfg["algo"],
                      cfg["pool_url"], cfg["wallet"], cfg["worker"])  # 预检
        except ValueError as e:
            push({"t": "toast", "kind": "warn", "msg": "还差一步：%s" % e})
            return
        if not engine.start(cfg):
            append_log("挖矿引擎已在运行，忽略重复启动（重复实例会抢显卡、拉低算力）")
        set_running(True)

    def on_stop():
        engine.stop()
        set_running(False)

    def on_pill():
        live = any(t.is_alive() for t in engine._threads) and not engine.stop_flag.is_set()
        on_stop() if live else on_start()

    # ---------------- 开关 / 输入（JS → Python） ----------------
    def set_toggle(key, val):
        if key not in S["toggles"]:
            return
        S["toggles"][key] = bool(val)
        if key == "gamemode":
            engine.cfg["gamemode"] = bool(val)
            append_log("🎮 游戏模式已%s" % (
                "开启：检测到游戏进程时自动停挖让卡" if val else "关闭：始终挖矿"))
        elif key == "check_other_miners":
            engine.cfg["check_other_miners"] = bool(val)
        elif key == "auto_update":
            engine.cfg["auto_update"] = bool(val)

    def set_field(key, val):
        if key not in S["fields"]:
            return
        S["fields"][key] = str(val)
        if key == "games":
            engine.cfg["games"] = parse_games(val)
        elif key == "kernel_dir":
            refresh_kernels()
            refresh_kchips()

    # ---------------- 退出 / 托盘 ----------------
    tray = TrayIcon(None, engine, q)
    tray_ok = [False]

    def real_quit():
        if quitting[0]:
            return
        quitting[0] = True
        push({"t": "pill", "text": "正在退出…", "kind": "warn"})
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

    # ---------------- 事件泵（q → 出站推送） ----------------
    def handle_event(ev):
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
        elif kind == "cfg_sync":
            # 中控远程改了配置（换矿池/换内核）：同步本地 cfg/S/fields，
            # 避免本地界面仍显示旧值、下次保存把新配置覆盖回旧值
            changed = []
            if ev.get("kernel") and ev["kernel"] in KERNELS \
                    and ev["kernel"] != cfg.get("kernel"):
                cfg["kernel"] = ev["kernel"]
                S["kernel"] = ev["kernel"]
                changed.append("内核 %s" % KERNELS[ev["kernel"]]["label"])
                refresh_kchips()
            if ev.get("pool_url") and ev["pool_url"] != cfg.get("pool_url"):
                cfg["pool_url"] = ev["pool_url"]
                S["fields"]["pool_url"] = ev["pool_url"]
                changed.append("矿池 %s" % pool_display(ev["pool_url"]))
                bid = brand_id_of_url(ev["pool_url"])
                if bid != S["brand"]:
                    S["brand"] = bid
                    refresh_node_list(ev["pool_url"])
            if changed:
                persist_cfg()
                append_log("✓ 已同步中控下发的配置：%s" % "、".join(changed))
        elif kind == "_speedtest_apply":
            speedtest_apply()
        elif kind == "income_meta":
            update_income_ui(_rate_cache.get("rate"), _rate_cache.get("cny"))
        elif kind == "conflicts":
            conflict_items[:] = ev.get("items") or []
            if conflict_items:
                names = "、".join("%s（%s）" % (c["label"], c["name"])
                                  for c in conflict_items[:4])
                more = " 等 %d 个程序" % len(conflict_items) if len(conflict_items) > 4 else ""
                dup = any(c["kind"] == "dup" for c in conflict_items)
                txt = ("本机检测到%s%s 正在占用显卡，这会让算力明显低于正常水平。"
                       "是否关闭？" % (names, more)
                       + ("（其中包含重复运行的 BaPiMiner 实例，建议只保留一个）" if dup else ""))
                push({"t": "conflict", "on": True, "text": txt})
            else:
                push({"t": "conflict", "on": False, "text": ""})
        elif kind == "stats":
            gpus = ev.get("gpus") or []
            inc = ev.get("income")
            up = ev.get("uptime") or 0
            push({"t": "metrics",
                  "ths": "%.1f" % (ev.get("ths") or 0),
                  "acc": "%d / %d" % (ev.get("accepted") or 0, ev.get("rejected") or 0),
                  "gpu": ("%.0f°C / %.0f%%" % (
                      max((g.get("temp") or 0) for g in gpus),
                      max((g.get("fan") or 0) for g in gpus))) if gpus else "—",
                  "income": ("¥%.0f" % inc) if inc else "—",
                  "gpu_text": "   ·   ".join(
                      "%s   %.1f TH/s   %.0f°C   %.0fW" % (
                          (g.get("model") or "GPU").replace("NVIDIA GeForce ", ""),
                          g.get("ths") or 0, g.get("temp") or 0, g.get("power") or 0)
                      for g in gpus) if gpus else "未检测到 NVIDIA GPU",
                  "uptime": "%d:%02d:%02d" % (up // 3600, up % 3600 // 60, up % 60) if up else "—"})
            update_income_ui(ev.get("income_rate"), ev.get("income_cny"),
                             ev.get("wallet"))
            if ev.get("gamemode"):
                gp = ev.get("game_proc")
                push({"t": "pill", "kind": "warn",
                      "text": "让卡中" + ("（%s）" % gp if gp else "")})
            elif not ev.get("miner_alive") and engine.state == "running":
                push({"t": "pill", "kind": "err", "text": "矿工未响应"})
            elif ev.get("miner_alive") and engine.state == "running":
                # 恢复分支：某次 API 轮询超时置红后必须拉回来，否则永远红着
                push({"t": "pill", "kind": "run", "text": "运行中"})
        elif kind == "gamemode":
            S["toggles"]["gamemode"] = bool(ev.get("enabled"))
            push({"t": "tg", "k": "gamemode", "v": bool(ev.get("enabled"))})
        elif kind == "error":
            append_log("错误: " + ev["msg"])
            push({"t": "pill", "kind": "err", "text": "出错"})
            set_running(False)
        elif kind == "stopped":
            set_running(False)
        elif kind == "tray_show":
            open_window()
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
            os._exit(0)   # HTTP 服务线程是 daemon，直接收摊

    def pump_loop():
        while not quitting[0]:
            try:
                ev = q.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                handle_event(ev)
            except Exception as e:  # noqa: BLE001
                log_write("事件处理异常: %r / %s" % (ev, e))

    # ---------------- HTTP：静态页 + poll / action / boot ----------------
    web_dir = res_path("web")

    class UiHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=web_dir, **kw)

        def log_message(self, *a):  # 静默访问日志
            pass

    def _json_reply(h, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h.send_response(code)
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        h.wfile.write(body)

    def _is_ui(h):
        # 路径带随机 token：本机其他进程/网页无法猜测（防 CSRF/误触）
        return h.path.startswith("/" + _ui_token + "/")

    def ui_handler(h):
        p, _, qs = h.path[len(_ui_token) + 1:].partition("?")
        try:
            if p == "/api/boot" and h.command == "GET":
                _json_reply(h, boot_payload())
                return True
            if p == "/api/poll" and h.command == "GET":
                since = 0
                for kv in qs.split("&"):
                    if kv.startswith("since="):
                        try:
                            since = int(kv[6:])
                        except ValueError:
                            since = 0
                _json_reply(h, poll_outbox(since))
                return True
            if p == "/api/action" and h.command == "POST":
                n = int(h.headers.get("Content-Length") or 0)
                req = json.loads(h.rfile.read(n).decode("utf-8") or "{}")
                dispatch_action(str(req.get("op") or ""), req.get("args") or {})
                _json_reply(h, {"ok": True})
                return True
        except (BrokenPipeError, ConnectionResetError):
            return True
        except Exception as e:  # noqa: BLE001
            try:
                _json_reply(h, {"ok": False, "err": str(e)}, 500)
            except Exception:  # noqa: BLE001
                pass
            return True
        return False

    _orig_send_head = UiHandler.send_head

    def send_head(self):
        if _is_ui(self) and ui_handler(self):
            return None
        if _is_ui(self):
            self.path = self.path[len(_ui_token) + 1:] or "/index.html"
        return _orig_send_head(self)

    UiHandler.send_head = send_head
    UiHandler.do_POST = lambda h: ui_handler(h) or h.send_error(404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), UiHandler)
    httpd.daemon_threads = True
    ui_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ui_url = "http://127.0.0.1:%d/%s/index.html" % (ui_port, _ui_token)

    # ---------------- 动作分发（JS → Python） ----------------
    def dispatch_action(op, args):
        if op == "toggle_mining":
            on_pill()
        elif op == "set_toggle":
            set_toggle(args.get("key"), args.get("val"))
        elif op == "set_field":
            set_field(args.get("key"), args.get("val"))
        elif op == "pick_kernel":
            pick_kernel(args.get("key"))
        elif op == "select_kernel":
            select_kernel(args.get("key"))
        elif op == "set_brand":
            api_set_brand(args.get("bid"))
        elif op == "pick_node":
            api_pick_node(args.get("url"))
        elif op == "speedtest":
            do_speedtest()
        elif op == "save":
            save_cfg()
        elif op == "clear_log":
            clear_log()
        elif op == "conflict_kill":
            on_kill_conflicts()
        elif op == "conflict_ignore":
            on_ignore_conflicts()
        elif op == "kernel_action":
            do_kernel_action(args.get("key"), bool(args.get("force")))
        elif op == "refresh_latest":
            refresh_latest()
        elif op == "open_dashboard":
            import webbrowser
            webbrowser.open((S["fields"]["server"].strip()
                             or "http://mine.sinyerobot.com:8099") + "/")
        elif op == "open_kernel_dir":
            d = S["fields"]["kernel_dir"].strip()
            if d and os.path.isdir(d):
                os.startfile(d)  # noqa: S606
            else:
                push({"t": "toast", "kind": "warn", "msg": "目录还不存在：%s" % d})
        elif op == "open_download":
            import webbrowser
            k = KERNELS.get(S["kernel"])
            if k:
                webbrowser.open(k["download"])
        elif op == "error":
            append_log("前端错误: %s" % args.get("msg"))
        elif op == "quit_now":
            real_quit()
        # 未知 op 静默忽略

    # ---------------- 开机全量快照（页面加载即拉） ----------------
    def boot_payload():
        bid = brand_id_of_url(S["fields"]["pool_url"])
        S["brand"] = bid
        nodes, tier = ([], None)
        if bid != CUSTOM_POOL_ID:
            nodes, tier = pool_candidates(bid)
        S["nodes"] = nodes
        S["shown"] = [shown_text(n) for n in nodes]
        b = pool_brand(bid)
        hidden = len(b["nodes"]) - len(nodes) if b else 0
        if bid == CUSTOM_POOL_ID:
            hint, hkind = "自定义：填写 stratum+tcp://主机:端口", "mut"
        else:
            hint = TIER_HINT[tier] + ("；已隐藏 %d 个海外节点" % hidden if hidden else "")
            hkind = "grn" if tier in (REGION_CN, REGION_HMT) else "amb"
        running = is_mining()
        return {
            "version": CLIENT_VERSION,
            "page": os.environ.get("MINRIGS_PAGE") or "mine",
            "fields": dict(S["fields"]),
            "toggles": dict(S["toggles"]),
            "running": running,
            "pill": {"text": "运行中", "kind": "run"} if running
                     else {"text": "已停止", "kind": "stop"},
            "preset_games": list(GAME_PROCESSES.values()),
            "logs": list(log_buf),
            "kchips": kchips_payload(),
            "pool": {"brands": [{"id": b_, "name": lbl} for lbl, b_ in brand_choices],
                     "brand": bid,
                     "nodes": [{"url": node_url(n), "label": shown_text(n)}
                               for n in nodes],
                     "node": S["fields"]["pool_url"],
                     "custom_visible": bid == CUSTOM_POOL_ID,
                     "custom_value": S["fields"]["pool_url"],
                     "hint": hint, "hint_kind": hkind,
                     "speed_disabled": False, "speed_label": "测速"},
            "kernel_rows": kernel_rows_payload(),
            "income": income_payload(),
            "rig": {"wallet": short_addr(S["fields"]["wallet"]) or "未设置"},
            "conflict": {"on": False, "text": ""},
            "poll_base": _ob_ver[0],
        }

    # ---------------- Edge app 窗口 ----------------
    def open_window():
        # 已有矿机端窗口（托盘「打开主界面」连点/重复触发）→ 恢复并前置，不再开新的
        hwnd = _find_app_window()
        if hwnd:
            try:
                u32 = ctypes.windll.user32
                u32.ShowWindow(hwnd, 9)          # SW_RESTORE：最小化时还原
                u32.SetForegroundWindow(hwnd)
                return True
            except Exception:  # noqa: BLE001
                pass
        for cand in (
                os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                             r"Microsoft\Edge\Application\msedge.exe"),
                os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                             r"Microsoft\Edge\Application\msedge.exe")):
            if os.path.isfile(cand):
                try:
                    # 注意：不能用 popen_hidden——SW_HIDE 会让 Edge 连窗口一起藏掉
                    subprocess.Popen(
                        [cand,
                         "--app=" + ui_url,
                         "--window-size=1240,960",
                         "--user-data-dir=" + os.path.join(BASE, "ui-profile"),
                         "--no-first-run", "--no-default-browser-check"],
                        cwd=BASE, close_fds=True)
                    return True
                except Exception as e:  # noqa: BLE001
                    log_write("Edge app 启动失败: %s" % e)
                    break
        import webbrowser
        webbrowser.open(ui_url)     # 找不到 Edge：普通浏览器打开（功能等价）
        return True

    # ---------------- 引导序列 ----------------
    append_log("扒皮矿工已就绪 —— 填好钱包地址、选好矿池，点右上「开始挖矿」")
    refresh_kernels()
    refresh_kchips()
    _ok, _ver, _exe = kernel_installed(S["kernel"], S["fields"]["kernel_dir"])
    if _ok:
        append_log("内核 %s 已就绪%s" % (
            KERNELS[S["kernel"]]["label"], ("（v%s）" % _ver) if _ver else ""))
    else:
        append_log("首次使用：内核还没装，去「系统设置」点该内核的「安装」，"
                   "程序会自动从中控下载，不需要手动准备文件")

    threading.Thread(target=pump_loop, daemon=True).start()
    install_watchdog()   # 每 3 分钟自检客户端进程，意外退出自动拉起

    def boot_seq():
        time.sleep(2.0)
        refresh_income_async()
        if S["toggles"]["autostart_mining"] and cfg.get("wallet"):
            on_start()
        else:
            refresh_latest()
    threading.Thread(target=boot_seq, daemon=True).start()

    atexit.register(lambda: engine.shutdown(timeout=4))   # 进程异常退出也清内核
    tray_ok[0] = tray.start()
    # 升级替换收尾钩子：托盘图标 + UI 窗口都是独立资源，os._exit 带不走
    global _UI_TEARDOWN
    def _ui_teardown():
        try:
            tray.stop()
        except Exception:  # noqa: BLE001
            pass
        _close_app_windows()
    _UI_TEARDOWN = _ui_teardown
    if tray_ok[0]:
        append_log("托盘已就绪：关闭窗口挖矿继续，托盘「打开主界面」随时调出，"
                   "托盘右键「退出」才会真正退出")
    else:
        append_log("系统托盘不可用：请保持本窗口开启（关闭窗口不影响挖矿，但无法看到界面）")

    # 启动时先清孤儿 UI 窗口：能走到这里 = 单实例锁在我们手里，
    # 桌面上任何矿机端窗口都是已死实例的残留（显示停格页面的僵尸窗口），
    # 不清掉的话 open_window 会错误复用它，出现新旧两个窗口并存。
    _close_app_windows()
    # 同理清孤儿内核：升级 os._exit 跳过 atexit / 崩溃后被看门狗拉起时，
    # 上个实例的内核会变成没人管的孤儿继续占卡，杀掉后按配置重新拉起。
    kill_orphan_kernels()
    open_window()   # 启动即打开 UI 窗口

    # 自更新残留自愈：上次升级若中途断掉（替换脚本被杀/断电），会留下
    # .bak（旧版本副本）和 _update_client.bat（脚本本体）。新 exe 已在运行，可安全清掉。
    try:
        _bak = os.path.join(BASE, "BaPiMiner.exe.bak")
        if os.path.isfile(_bak):
            os.remove(_bak)
            log_write("清理升级残留：BaPiMiner.exe.bak")
    except OSError:
        pass
    try:
        _bat = os.path.join(BASE, UPDATE_BAT)
        if os.path.isfile(_bat):
            os.remove(_bat)
            log_write("清理升级残留：%s" % UPDATE_BAT)
    except OSError:
        pass
    try:
        if os.path.isfile(_UPDATE_LOCK_FILE):
            os.remove(_UPDATE_LOCK_FILE)
            log_write("清理升级残留：.update.lock")
    except OSError:
        pass
    try:
        _new = os.path.join(BASE, "BaPiMiner.exe.new.exe")
        if os.path.isfile(_new):
            os.remove(_new)
            log_write("清理升级残留：BaPiMiner.exe.new.exe")
    except OSError:
        pass

    log_write("GUI 就绪 v%s（Web UI @ 127.0.0.1:%d）" % (CLIENT_VERSION, ui_port))
    log_write("UI 调试地址（仅本机）: %s" % ui_url)

    # 客户端自更新看门狗：40 秒后首次检查，之后每 6 小时一次。
    # 主线程每 5 秒把纯 dict 快照刷进 watcher_cfg，后台线程只读它。
    watcher_cfg = {}

    def _sync_watcher_cfg():
        if quitting[0]:
            return
        try:
            d = current_cfg()
        except Exception:  # noqa: BLE001
            d = None
        if d:
            watcher_cfg.clear()
            watcher_cfg.update(d)
        t = threading.Timer(5, _sync_watcher_cfg)
        t.daemon = True
        t.start()

    _sync_watcher_cfg()
    start_update_watcher(watcher_cfg.get,
                         emit=lambda **kw: q.put(kw),
                         stop_miner=lambda: engine._kill())

    # 常驻：等待退出信号（quit_done 里 os._exit；外部 kill 走 atexit 兜底）
    while not quitting[0]:
        time.sleep(0.5)
    log_write("GUI 主循环结束（进程即将退出）")
    httpd.shutdown()
    try:
        tray.stop()
        engine.shutdown(timeout=6)
    except Exception as e:  # noqa: BLE001
        log_write("退出兜底失败: %s" % e)


# ---------------------------------------------------------------- 自检

def self_replace_main(old):
    """--self-replace <旧exe路径>：新版本进程自行完成最后替换。

    自己能运行到这一步 = 新文件完整且杀软已放行，此后只是「改名运行中的
    自己」（Windows 允许）+ 拉起正式路径，不再存在「启动刚被换入的 exe」
    这个 DLL 弹窗源头。等不到旧进程退出（60 秒）就放弃，避免误伤。
    """
    exe = sys.executable
    log_write("自替换开始：old=%s new=%s" % (old, exe))
    k32 = ctypes.windll.kernel32
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            h = k32.CreateMutexW(None, False, ctypes.c_wchar_p("Local\\BaPiMinerClient"))
            free = k32.GetLastError() != 183      # ERROR_ALREADY_EXISTS
            k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            free = True
        if free:
            break
        time.sleep(0.5)
    else:
        log_write("自替换放弃：旧进程 60 秒内未退出")
        _update_lock_release()
        return 1
    try:
        if os.path.isfile(old):
            os.replace(old, old + ".bak")         # 回滚兜底
    except OSError as e:
        log_write("备份旧版失败（继续）: %s" % e)
    try:
        os.replace(exe, old)                      # 运行中的 exe 允许改名
    except OSError as e:
        log_write("自替换改名失败: %s" % e)
        _update_lock_release()
        return 1
    try:
        popen_hidden([old], cwd=os.path.dirname(old) or BASE, pipe_out=False)
    except Exception as e:  # noqa: BLE001
        log_write("自替换拉起新版失败: %s（看门狗将兜底）" % e)
        _update_lock_release()
        return 1
    log_write("自替换完成，已拉起 %s" % old)
    return 0


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

    # 4) 收益估算（公式：总算力 × 全网均价 × 汇率）
    _saved = dict(_rate_cache)
    _rate_cache.update({"rate": 0.0331, "cny": 6.6629, "ts": time.time()})
    inc = estimate_income("NVIDIA GeForce RTX 5080 Laptop GPU", 137.0)
    ok_income = inc is not None and 25 <= inc <= 35
    print("[income] 137TH/s × 0.0331 × 6.6629 ->", ("¥%s/天" % inc) if inc else "收益率暂不可用",
          "| PASS" if ok_income else "| FAIL")
    _rate_cache.clear()
    _rate_cache.update(_saved)

    # 5) 游戏模式：进程枚举 + 名单匹配 + 命令状态机
    nproc = len(list_processes())
    print("[gamemode] 进程枚举 OK（当前 %d 个进程）" % nproc)
    assert nproc > 20, "实时进程枚举返回 %d 个（应为数百），PROCESSENTRY32W 结构体定义有误" % nproc
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
    if "--self-replace" in sys.argv:
        i = sys.argv.index("--self-replace")
        old = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        sys.exit(self_replace_main(old) if old else 1)
    if "--tkcheck" in sys.argv or "--webcheck" in sys.argv:
        ok = os.path.isfile(res_path(os.path.join("web", "index.html"))) or \
            os.path.isfile(os.path.join(BASE, "web", "index.html"))
        print("webcheck", "OK web/index.html 就绪" if ok else "FAIL 缺少 web/index.html")
        sys.exit(0 if ok else 1)
    try:
        run_gui()
    except SystemExit:
        raise
    except Exception:
        # --noconsole 打包下 stderr 是黑洞，GUI 崩了必须留痕到 client.log
        import traceback
        log_write("✕ GUI 异常退出:\n" + traceback.format_exc())
