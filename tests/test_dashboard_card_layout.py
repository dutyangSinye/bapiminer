# -*- coding: utf-8 -*-
"""矿机卡片布局回归测试：锁死「高度一致 + 换行不错位」的契约。

背景（用户反馈「这卡片做的太丑了，高度 换行 做的都有问题」）：
  卡片原来把「大数字+单位+GPU型号」和「收益/钱包」放在同一个 flex 容器里，
  用 flex-wrap 换行。GPU 型号一长（如 RTX 5080 Laptop GPU）就把收益整组挤到下一行，
  于是同一排里 —— 型号短的卡片两组同行、型号长的卡片收益换行，
  卡片高度不同、内部各区块基线全部错位，看起来参差不齐。
  另外 .rig-tags 写死 min-height:50px 预留两行，但徽标通常只有一行，
  每张卡顶部都空出约 30px 死白。

本测试覆盖：
  A. 摘要区改成两栏 grid（不再 flex-wrap 换行）
  B. GPU 型号从算力块拆出，独立成 sub 行，不再影响右栏
  C. 徽标行不再写死 min-height:50px 死白
  D. 冲突徽标只在明细区出现一次（不再上下重复）
  E. 单卡时不再重复渲染 GPU 芯片行（与上方四宫格冗余）
  F. 曲线区有标题说明
  G. 折叠按钮归位到头部操作组（不占摘要区栅格列）
  H. 响应式：窄屏下摘要区仍不塌陷
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HTML = os.path.join(ROOT, "server", "static", "index.html")

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        print("  FAIL  %s %s" % (name, extra))


def css_block(src, selector):
    """取某个选择器的 CSS 规则文本（含续行）。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", src)
    return m.group(1) if m else ""


def css_all(src, selector):
    """取某个选择器的所有 CSS 规则文本（拼接）。

    注意：css_block 只匹配第一条规则。像 `.metric .k` / `.metric .v`
    这类带后代选择器的规则，必须用本函数才能全部取到。
    """
    pat = re.escape(selector) + r"(?![\w-])[^{]*\{([^}]*)\}"
    out = []
    for m in re.finditer(pat, src):
        out.append(m.group(0))
    return "\n".join(out)


def main():
    src = open(HTML, encoding="utf-8").read()

    print("=== A. 摘要区改为两栏 grid（消除 flex-wrap 换行错位） ===")
    sum_css = css_block(src, ".rig-sum")
    check("rig-sum 使用 grid 布局", "display:grid" in sum_css, sum_css[:80])
    check("rig-sum 不再使用 flex-wrap（关键：换行是错位根源）",
          "flex-wrap" not in sum_css, sum_css[:120])
    check("rig-sum 有两列定义", "grid-template-columns" in sum_css)
    check("左列自适应（minmax(0,1fr)）", "minmax(0,1fr)" in sum_css)
    check("存在左栏容器 sum-l", 'class="sum-l"' in src)

    print("\n=== B. GPU 型号拆出到独立 sub 行 ===")
    check("存在 sum-sub 样式", ".rig-sum .sum-sub" in src)
    check("模板渲染 sum-sub", 'class="sum-sub"' in src)
    # 算力块里不应再拼接型号
    big_line = re.search(r'<div class="big">.*?</div>', src, re.S)
    check("算力块内只含数值与单位", big_line is not None and "s.model" not in big_line.group(0),
          (big_line.group(0)[:120] if big_line else "not found"))
    check("型号走 sum-sub 而非 big",
          re.search(r'sum-sub.*?s\.model', src, re.S) is not None)
    check("sum-sub 可截断（长型号不撑破卡片）",
          "text-overflow:ellipsis" in css_block(src, ".rig-sum .sum-sub"))

    print("\n=== C. 徽标行不再预留死白 ===")
    tags_css = css_block(src, ".rig-tags")
    check("rig-tags 不再写死 min-height:50px",
          "min-height:50px" not in tags_css, tags_css[:120])
    check("rig-tags 允许换行（内容自适应）", "flex-wrap:wrap" in tags_css)
    check("mini 模式也不写死 min-height",
          "min-height" not in css_block(src, ".rig.mini .rig-tags"))

    print("\n=== D. 冲突徽标只出现一次 ===")
    # 模板里的 rig-meta 块：定位「元信息行只在有内容时渲染」这段 IIFE
    meta_iife = re.search(
        r"元信息行只在有内容时渲染.*?\}\)\(\)\}", src, re.S)
    meta_txt = meta_iife.group(0) if meta_iife else ""
    check("rig-meta 模板块存在", bool(meta_txt))
    # 剥掉注释后再判断：注释里会提到「其他矿工」字样，不能算作渲染
    meta_code = re.sub(r"//[^\n]*", "", meta_txt)
    check("rig-meta 不再重复渲染冲突徽标", "其他矿工" not in meta_code,
          meta_code[:200])
    check("rig-meta 代码块里没有 conflicts 引用", "conflicts" not in meta_code,
          meta_code[:200])
    check("冲突徽标仍在明细区承载", "conflictTag" in src)
    check("conflictTag 仍被 rig-tags 引用",
          re.search(r'rig-tags.*?conflictTag', src, re.S) is not None)

    print("\n=== E. 单卡不再重复渲染 GPU 芯片行 ===")
    check("gpuChips 条件为多卡才渲染",
          re.search(r'const gpuChips = gpus\.length > 1', src) is not None)
    check("单卡时 gpu-row 不出现（与四宫格冗余）",
          "gpus.length > 1" in src)
    check("多卡仍保留逐卡明细", "gpu-chip" in src)

    print("\n=== F. 曲线区有标题说明 ===")
    check("存在 spark-cap 样式", ".spark-cap" in src)
    check("曲线区渲染标题", 'class="spark-cap"' in src)
    check("标题文案为「算力走势」", "算力走势" in src)

    print("\n=== G. 折叠按钮归位到头部操作组 ===")
    hd_block = re.search(r'<div class="rig-hd">.*?</div>\s*</div>', src, re.S)
    hd_txt = hd_block.group(0) if hd_block else ""
    check("折叠按钮在 rig-hd 内", "rig-fold" in hd_txt, hd_txt[:150])
    sum_block = re.search(r'<div class="rig-sum">.*?</div>\s*</div>', src, re.S)
    sum_txt = sum_block.group(0) if sum_block else ""
    check("折叠按钮不在摘要区内", "rig-fold" not in sum_txt, sum_txt[:150])
    check("rig-fold 样式改为 flex（操作组是 flex 容器）",
          "flex:none" in css_block(src, ".rig-fold"))

    print("\n=== H. 数字与徽标不撑破窄卡 ===")
    metric_all = css_all(src, ".metric")
    check("metric 有 min-width:0（栅格项可收缩）", "min-width:0" in metric_all)
    check("metric 值可截断", "text-overflow:ellipsis" in metric_all)
    check("指标标签 nowrap", "white-space:nowrap" in metric_all)
    big_css = css_block(src, ".big")
    check("big 数字块 nowrap", "flex-wrap:nowrap" in big_css)

    print("\n=== I. 收益徽标不撑破右栏 ===")
    income_css = css_block(src, ".rig-sum .sum-tags .income")
    check("收益徽标 nowrap", "white-space:nowrap" in income_css)
    check("收益徽标可截断", "text-overflow:ellipsis" in income_css)
    check("收益徽标有 max-width 约束", "max-width" in income_css)

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (PASS, FAIL))
    if FAIL:
        print("存在失败断言")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
