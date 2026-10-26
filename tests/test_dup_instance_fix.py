# -*- coding: utf-8 -*-
"""重复实例误报的根因回归测试。

背景（本文件的由来）：
    用户反馈「关闭了进程重新打开，还是报 BaPiMiner 重复实例」。
    根因不在重复实例本身，而在进程枚举用的 _PE32W 结构体字段偏移错位：
    th32ParentProcessID 实际读到了 cntThreads（线程数）。
    于是 detect_conflicts 里「用父 PID 排除 PyInstaller onefile 双进程」
    彻底失效，客户端把自己的引导进程报成「另一个重复实例」。

本文件锁死三件事：
    A. 结构体偏移必须是 MSVC 真实布局
    B. onefile 双进程不得被判为「重复实例」
    C. 真正的重复实例仍然必须被报出来（不能把功能改瞎）
"""
import ctypes
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "client"))
import miner_client as mc  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s%s" % (name, ("  — " + detail) if detail else ""))


print("=" * 74)
print("A. _PE32W 结构体字段偏移（必须与 MSVC 真实布局一致）")
print("=" * 74)

EXPECT = {
    "dwSize": 0,
    "cntUsage": 4,
    "th32ProcessID": 8,
    "th32DefaultHeapID": 16,
    "th32ModuleID": 24,
    "cntThreads": 28,
    "th32ParentProcessID": 32,
    "pcPriClassBase": 36,
    "dwFlags": 40,
    "szExeFile": 44,
}
for fname, want in EXPECT.items():
    got = getattr(mc._PE32W, fname).offset
    check("%s 偏移 == %d" % (fname, want), got == want, "实际 %d" % got)

check("结构体总长 == 568", ctypes.sizeof(mc._PE32W) == 568,
      "实际 %d" % ctypes.sizeof(mc._PE32W))
check("没有残留的 _pack_ = 4 紧凑对齐",
      not hasattr(mc._PE32W, "_pack_") or mc._PE32W._pack_ in (None, 8),
      "_pack_=%r" % getattr(mc._PE32W, "_pack_", None))

print()
print("=" * 74)
print("B. 枚举正确性：ppid 必须是真实父 PID，不能是线程数")
print("=" * 74)

procs = mc.list_processes_ex()
check("枚举到进程 (>100)", len(procs) > 100, "实际 %d" % len(procs))
check("每个进程都有 ppid 字段", all("ppid" in p for p in procs))

# 关键判据：读出的 ppid 必须能解析成一个「看起来像 PID」的祖先，
# 而线程数通常不会与任何真实进程的 pid 相符。
by_pid = {int(p["pid"]): int(p.get("ppid") or 0) for p in procs}
own_thread_counts = {int(p.get("ppid") or 0) for p in procs}
# 取一批 ppid 落在「真实进程 pid 集合」里的比例
resolvable = sum(1 for pid, ppid in by_pid.items()
                  if ppid == 0 or ppid in by_pid)
ratio = resolvable / max(1, len(by_pid))
check("ppid 指向真实存在的进程（比例 > 90%%）", ratio > 0.90,
      "实际 %.1f%%" % (ratio * 100))

print()
print("=" * 74)
print("C. detect_conflicts：自己视角不得把自己报成重复实例")
print("=" * 74)

# 模拟「我是进程表里的某个 bapiminer 进程」
self_like = None
for p in procs:
    if (p.get("name") or "").lower() == "bapiminer.exe":
        self_like = p
        break

if self_like:
    pid_self = int(self_like["pid"])
    ppid_self = int(self_like.get("ppid") or 0)
    print("   模拟对象：pid=%d ppid=%d path=%s"
          % (pid_self, ppid_self, self_like.get("path")))

    orig_getpid = os.getpid
    os.getpid = lambda: pid_self
    try:
        got = mc.detect_conflicts()
    finally:
        os.getpid = orig_getpid
    dups = [c for c in got if c["kind"] == "dup"]
    check("自己视角不产生任何 dup", len(dups) == 0,
          "误报 %d 条: %s" % (len(dups), [(c["pid"], c["name"]) for c in dups]))
else:
    print("   （当前无 bapiminer 进程在跑，跳过该断言）")

print()
print("=" * 74)
print("D. detect_conflicts：真重复实例仍必须被报出（不能把功能改瞎）")
print("=" * 74)

# 构造一份假进程表：me=1000（我），另一个同 exe 路径的独立实例 pid=2000
fake = [
    {"pid": 1000, "ppid": 900, "name": "bapiminer.exe", "path": r"D:\mine\BaPiMiner.exe"},
    {"pid": 2000, "ppid": 900, "name": "bapiminer.exe", "path": r"D:\mine\BaPiMiner.exe"},
    {"pid": 900, "ppid": 0, "name": "explorer.exe", "path": r"C:\Windows\explorer.exe"},
]
orig_getpid = os.getpid
os.getpid = lambda: 1000
try:
    got = mc.detect_conflicts(procs=fake)
finally:
    os.getpid = orig_getpid
dups = [c for c in got if c["kind"] == "dup"]
check("独立实例（无亲缘关系）被报为 dup", len(dups) == 1 and dups[0]["pid"] == 2000,
      "实际 %s" % [(c["pid"], c["kind"]) for c in got])

# 构造：onefile 双进程 —— 引导进程 2000 的父是主进程 1000
fake2 = [
    {"pid": 1000, "ppid": 900, "name": "bapiminer.exe", "path": r"D:\mine\BaPiMiner.exe"},
    {"pid": 2000, "ppid": 1000, "name": "bapiminer.exe", "path": r"D:\mine\BaPiMiner.exe"},
    {"pid": 900, "ppid": 0, "name": "explorer.exe", "path": r"C:\Windows\explorer.exe"},
]
orig_getpid = os.getpid
os.getpid = lambda: 1000
try:
    got = mc.detect_conflicts(procs=fake2)
finally:
    os.getpid = orig_getpid
dups = [c for c in got if c["kind"] == "dup"]
check("onefile 引导进程（父是我）不算重复实例", len(dups) == 0,
      "误报 %s" % [(c["pid"], c["kind"]) for c in got])

# 构造：我是子进程，引导进程是我的父（ppid 正确时的常规形态）
fake3 = [
    {"pid": 1000, "ppid": 2000, "name": "bapiminer.exe", "path": r"D:\mine\BaPiMiner.exe"},
    {"pid": 2000, "ppid": 900, "name": "bapiminer.exe", "path": r"D:\mine\BaPiMiner.exe"},
    {"pid": 900, "ppid": 0, "name": "explorer.exe", "path": r"C:\Windows\explorer.exe"},
]
orig_getpid = os.getpid
os.getpid = lambda: 1000
try:
    got = mc.detect_conflicts(procs=fake3)
finally:
    os.getpid = orig_getpid
dups = [c for c in got if c["kind"] == "dup"]
check("引导进程是我的父 → 不算重复实例", len(dups) == 0,
      "误报 %s" % [(c["pid"], c["kind"]) for c in got])

# 构造：外部第三方矿工必须仍被报出
fake4 = [
    {"pid": 1000, "ppid": 900, "name": "bapiminer.exe", "path": r"D:\mine\BaPiMiner.exe"},
    {"pid": 3000, "ppid": 900, "name": "xmrig.exe", "path": r"D:\other\xmrig.exe"},
    {"pid": 900, "ppid": 0, "name": "explorer.exe", "path": r"C:\Windows\explorer.exe"},
]
orig_getpid = os.getpid
os.getpid = lambda: 1000
try:
    got = mc.detect_conflicts(procs=fake4)
finally:
    os.getpid = orig_getpid
miners = [c for c in got if c["kind"] == "miner"]
check("第三方矿工（xmrig）仍被报出", len(miners) == 1 and miners[0]["pid"] == 3000,
      "实际 %s" % [(c["pid"], c["kind"]) for c in got])

print()
print("=" * 74)
print("E. 端到端：真启动一个副本，双进程不得被判为重复实例")
print("=" * 74)

EXE = os.path.join(ROOT, "client", "dist", "BaPiMiner.exe")
if not os.path.isfile(EXE):
    print("   （跳过：%s 不存在）" % EXE)
else:
    proc = subprocess.Popen([EXE], cwd=os.path.dirname(EXE),
                            creationflags=0x08000000)
    try:
        seen_dual = False
        for _ in range(20):
            time.sleep(0.3)
            s = mc.list_processes_ex()
            by_pid = {int(p["pid"]): p for p in s}
            # 新副本的进程树：spawn 进程(引导) ← 主进程
            # 「我」= 主进程，它必须把自己的引导进程排除掉。
            # 注意：不能拿列表里任意一个 bapiminer 当「我」——
            # 那个长期运行的 47480 是**独立实例**，报它是对的。
            boot = by_pid.get(proc.pid)
            main = None
            for p in s:
                if int(p.get("ppid") or 0) == proc.pid:
                    main = p
                    break
            if not (boot and main):
                continue
            seen_dual = True
            print("   观测到双进程：引导 pid=%d(ppid=%d) 主进程 pid=%d(ppid=%d)"
                  % (int(boot["pid"]), int(boot.get("ppid") or 0),
                     int(main["pid"]), int(main.get("ppid") or 0)))
            me_pid = int(main["pid"])
            orig_getpid = os.getpid
            os.getpid = lambda: me_pid
            try:
                got = mc.detect_conflicts(procs=s)
            finally:
                os.getpid = orig_getpid
            dups = [c for c in got if c["kind"] == "dup"]
            check("onefile 双进程：引导进程不被误报为重复实例",
                  all(c["pid"] != int(boot["pid"]) for c in dups),
                  "引导 pid=%d 被误报；全部 dup=%s"
                  % (int(boot["pid"]), [(c["pid"], c["name"]) for c in dups]))
            break
        check("确实观测到 onefile 双进程（验证前提成立）", seen_dual,
              "未捕获到双进程窗口")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

print()
print("=" * 74)
print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
print("=" * 74)
sys.exit(1 if FAIL else 0)
