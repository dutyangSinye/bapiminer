#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""内核分类与版本比较的契约测试。

背景（真实故障）：
  用户「我下发给租赁4090 更新内核的命令了 但是没升级啊」。
  排查发现：
    · 群控目录有三个内核 key：srbminer / bzminer / wildrig
    · 但前端 batchKernel() 里用内联判断 /bz/i.test(kernel) ? 'bzminer' : 'srbminer'
      归类 —— WildRig 机器全部被误归成 srbminer
    · 于是「⬆ 升级 N 台」把 WildRig 机器算进 SRBMiner 的升级名单，
      目标版本又取 srbminer 的 3.8.0 去比 WildRig 的 0.51.7 → 永远「可更新」
    · 下发后客户端更新的是它自己当前的内核（WildRig），用户观感就是「没升级」

  同类漏网点共 3 处：batchKernel 归类、stale 版本裸比较、版本 v 前缀不归一。

本测试锁死：
  A kernelShort 三内核归类正确
  B kernelVerNum 版本归一化（v 前缀 / 空白）
  C 所有内核版本比较点都必须经过 kernelVerNum，不得裸比较
  D 所有内核归类点都必须经过 kernelShort，不得内联 /bz/ 判断
  E 前端不存在残留的内联 /bz/ 分类表达式
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "server", "static", "index.html")

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name + (("  " + str(detail)) if detail else ""))
    else:
        FAIL += 1
        FAILURES.append(name + "  " + str(detail))
        print("  FAIL  " + name + "  " + str(detail))


src = open(IDX, encoding="utf-8").read()
# 剥掉注释，避免注释里的示例代码被当成真实逻辑
code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
code = re.sub(r"//[^\n]*", "", code)

print("=== A kernelShort 三内核归类 ===")
m = re.search(r"function kernelShort\(kn\)\s*\{(.*?)\n\}", code, re.S)
check("存在 kernelShort 函数", m is not None)
if m:
    body = m.group(1)
    check("wildrig 分支存在", "wildrig" in body, "")
    check("bzminer 分支存在", "bzminer" in body, "")
    check("srbminer 是兜底分支", body.rstrip().endswith("return 'srbminer';"),
          body.strip()[-40:])

print()
print("=== B kernelVerNum 版本归一化 ===")
m2 = re.search(r"function kernelVerNum\(v\)\s*\{(.*?)\n\}", code, re.S)
check("存在 kernelVerNum 函数", m2 is not None)
if m2:
    b = m2.group(1)
    check("去 v 前缀", "/^v/i" in b, "")
    check("提取数字部分", re.search(r"\[\\d\.\]\+|\\d", b) is not None, "")

print()
print("=== C 版本比较必须走 kernelVerNum ===")
# 找出所有 kernel_ver 与目录版本的比较点。
# 注意：要排除「已经包在 kernelVerNum() 里」的写法 ——
# kernelVerNum(a) !== kernelVerNum(b) 里的 === 会被裸比较正则误捕。
cmp_pat = re.compile(r"kernel_ver[^;\n]{0,80}?(===|!==)[^;\n]{0,60}?(k\.version|item\.version)")
raw = []
for m in cmp_pat.finditer(code):
    seg = m.group(0)
    # 该比较语句左侧若是 kernelVerNum(...) 开头，说明已归一化 → 跳过
    if "kernelVerNum(" in seg:
        continue
    raw.append(seg)
check("不存在裸比较 kernel_ver 与目录版本", len(raw) == 0, str(raw))

# 断言所有比较点都用了 kernelVerNum
ver_cmps = re.findall(r"kernelVerNum\([^)]*\)", code)
check("kernelVerNum 至少被调用 3 处（徽标/stale/batchKernel）", len(ver_cmps) >= 3,
      "count=%d" % len(ver_cmps))

print()
print("=== D 内核归类必须走 kernelShort ===")
shorts = re.findall(r"kernelShort\([^)]*\)", code)
check("kernelShort 至少被调用 4 处", len(shorts) >= 4, "count=%d" % len(shorts))

print()
print("=== E 不得残留内联 /bz/ 分类表达式 ===")
inline_bz = re.findall(r"/bz/i\.test\([^)]*\)\s*\?[^:]*:\s*['\"]srbminer['\"]", code)
check("无内联 /bz/ ? bzminer : srbminer 分类", len(inline_bz) == 0, str(inline_bz))

# batchKernel 里必须用 kernelShort
m3 = re.search(r"async function batchKernel\(name\)\s*\{(.*?)\n\}\nasync function", code, re.S)
if not m3:
    m3 = re.search(r"async function batchKernel\(name\)\s*\{(.*?)\nasync function", code, re.S)
check("找到 batchKernel 函数体", m3 is not None)
if m3:
    bb = m3.group(1)
    check("batchKernel 用 kernelShort 归类", "kernelShort(" in bb, "")
    check("batchKernel 用 kernelVerNum 比版本", "kernelVerNum(" in bb, "")
    check("batchKernel 不再内联判断 /bz/", "/bz/i.test" not in bb, "")

print()
print("=== F 内核下载 key 映射（客户端） ===")
cli = open(os.path.join(ROOT, "client", "miner_client.py"), encoding="utf-8").read()
for key in ("srbminer", "bzminer", "wildrig"):
    check("客户端 KERNELS 含 %s 的 key 映射" % key,
          re.search(r'"key"\s*:\s*"%s"' % key, cli) is not None, "")
check("客户端不再有 bzminer 猜测兜底",
      'else "bzminer"' not in re.sub(r"#.*", "", cli), "")
check("客户端 key 必须显式映射（缺失即报错）",
      re.search(r'key\s*=\s*k\.get\("key"\)\s*\n\s*if not key:', cli) is not None, "")

print()
print("=" * 56)
print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
if FAIL:
    print("\n失败项：")
    for f in FAILURES:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
