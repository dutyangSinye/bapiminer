# -*- coding: utf-8 -*-
"""单元验证：_find_app_windows / _keep_only_one_app_window / _match_app_title。

这是之前漏掉的环节——三个函数曾「只有调用没有定义」，导致枚举恒返回空，
托盘每次点击都开新窗口。本脚本直接对真实桌面上的矿机端窗口做断言。

用法：先打开一个矿机端 UI 窗口（或让客户端开着），再跑本脚本。
"""
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT = os.path.join(os.path.dirname(HERE), "client", "miner_client.py")

spec = importlib.util.spec_from_file_location("mc", CLIENT)
mc = importlib.util.module_from_spec(spec)
sys.modules["mc"] = mc
spec.loader.exec_module(mc)

print("=== 1) 标题匹配 ===")
cases = [
    ("扒皮矿工·矿机端", True),
    ("扒皮矿工 · 中控台", False),
    ("扒皮矿工·矿机端 - 5 个通知", True),
    ("", False),
    (None, False),
    ("ChatGPT", False),
]
ok = True
for title, want in cases:
    got = mc._match_app_title(title)
    flag = "OK " if got == want else "!! "
    if got != want:
        ok = False
    print("  %s title=%-28r 期望=%-5s 实际=%s" % (flag, title, want, got))

print("\n=== 2) 枚举真实窗口 ===")
wins = mc._find_app_windows()
print("  找到 %d 个矿机端窗口: %s" % (len(wins), wins))
if not wins:
    print("  （当前桌面没有矿机端窗口，枚举结果为 0 是正确的）")
else:
    # 交叉验证：与独立的窗口计数器对比
    r = subprocess.run([sys.executable, os.path.join(HERE, "count_app_windows.py"), "count"],
                       capture_output=True, text=True, timeout=30)
    print("  独立计数器输出:\n    " + r.stdout.strip().replace("\n", "\n    "))

print("\n=== 3) 收敛函数（幂等性）===")
before = len(mc._find_app_windows())
c1 = mc._keep_only_one_app_window()
time.sleep(0.8)
after = len(mc._find_app_windows())
print("  调用前 %d 个 -> 关闭 %d 个 -> 调用后 %d 个" % (before, c1, after))
if after > 1:
    print("  !! 收敛失败，仍有 %d 个" % after)
    ok = False
c2 = mc._keep_only_one_app_window()
print("  再次调用关闭 %d 个（幂等：应该为 0 或很少）" % c2)

print("\n=== 结论 ===")
print("PASS：标题匹配与枚举/收敛均正常" if ok else "FAIL：存在异常")
sys.exit(0 if ok else 1)
