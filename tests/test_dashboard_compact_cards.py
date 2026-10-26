# -*- coding: utf-8 -*-
"""看板卡片精简/展开行为的静态断言（不依赖浏览器，CI 可跑）。

覆盖：
  A. 精简模式：管理员默认开启、个人默认关闭；偏好按角色分开存
  B. 卡片结构：主信息（算力/收益/钱包）常驻；明细区（指标/曲线/GPU/次要按钮）可折叠
  C. 折叠状态下明细区 hidden；展开按钮文案切换
  D. 非精简模式下「重启/重置/⚙」仍在头部，不丢失操作入口
  E. 退出登录时清空展开状态，避免换账号串味
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


def main():
    src = open(HTML, encoding="utf-8").read()

    print("=== A. 精简模式开关与默认值 ===")
    check("存在 fCompact 复选框", 'id="fCompact"' in src)
    check("存在 setCompact()", "function setCompact(" in src)
    check("存在 applyCompactPref()", "function applyCompactPref(" in src)
    check("偏好按角色分开存（admin/user）",
          "bapiminer_compact_' + (me && me.is_admin ? 'admin' : 'user')" in src)
    check("管理员默认精简", "COMPACT_CARDS = v === null ? !!(me && me.is_admin) : v === '1'" in src)
    check("load() 里应用偏好", "renderMe(); applyCompactPref();" in src)

    print("\n=== B. 卡片结构：主信息常驻 + 明细可折叠 ===")
    check("存在 rig-sum 摘要区", 'class="rig-sum"' in src)
    check("存在 rig-detail 明细区", 'class="rig-detail"' in src)
    check("存在 rig-fold 展开按钮", 'data-rig-fold=' in src)
    check("展开按钮绑定 toggleRigCard", "onclick=\"toggleRigCard(" in src)
    check("算力在摘要区（主信息常驻）",
          re.search(r'rig-sum.*?class="big"', src, re.S) is not None)
    check("收益徽标在摘要区", "sum-tags\">${incomeChip}${walletChip}" in src)
    check("明细区含指标四宫格", "rig-detail" in src and 'class="metrics"' in src)
    check("次要操作有唯一入口（更多菜单，不在明细区重复）",
          'class="rig-menu"' in src and 'class="rig-ops"' not in src)

    print("\n=== C. 折叠状态与文案 ===")
    check("精简态给卡片加 mini 类", "${compact ? 'mini' : ''}" in src)
    check("默认收起条件正确", "${compact && !expanded ? 'hidden' : ''}" in src)
    check("rigCardExpanded 逻辑", "return !COMPACT_CARDS || RIG_EXPANDED.has(name);" in src)
    check("展开按钮文案切换", "'▲ 收起' : '▼ 详情'" in src)
    check("toggleRigCard 切换 hidden", "box.hidden = !rigCardExpanded(name)" in src)
    check("CSS 隐藏明细区", ".rig-detail[hidden]{display:none}" in src)

    print("\n=== D. 操作入口不丢失 ===")
    check("头部操作组 rig-acts", 'class="rig-acts"' in src)
    check("次要操作收进「更多」菜单", 'class="rig-menu"' in src and 'class="rig-menu-item' in src)
    check("菜单含重启/重置/配置", "restart(" in src and "resetToken(" in src and "openCfg(" in src)
    check("菜单项都有说明文案", src.count('<b>重启矿机</b>') >= 1
          and '<b>重置客户端密钥</b>' in src and '<b>配置矿池 / 内核</b>' in src)
    check("头部恒定含「更多」按钮（两种模式一致）",
          'class="btn mini ico" onclick="toggleRigMenu(' in src)
    check("菜单可关闭（Esc + 点外部）",
          "function closeRigMenu(" in src and "e.key === 'Escape'" in src)
    check("菜单项点击不抢焦点", "closeRigMenu(false)" in src)

    print("\n=== E. 换账号不串味 ===")
    check("退出登录清空展开状态", "RIG_EXPANDED.clear();" in src)
    check("clearRenderedData 清空展开状态", "try { RIG_EXPANDED.clear(); } catch (e) { }" in src)

    print("\n=== F. CSS 支撑 ===")
    check(".rig-sum 样式", ".rig-sum{" in src)
    check(".rig.mini 样式", ".rig.mini" in src)

    print("\n=== G. 卡片头信息分层（设计修复） ===")
    check("主身份行 rig-hd 存在", 'class="rig-hd"' in src)
    check("元信息行 rig-meta 存在", 'class="rig-meta"' in src)
    check("元信息行与主身份行分离（归属不再塞进 .rig-hd）",
          re.search(r'class="rig-hd".*?class="rig-meta"', src, re.S) is not None
          and 'class="rig-hd">[^\n]*ownerTag' not in src)
    check("矿机名独占可伸缩空间（flex:1 + min-width:0）",
          ".rig-hd .nm{" in src and "flex:1 1 auto;min-width:0" in src)
    check("操作组固定宽（不挤压名字）", ".rig-acts{" in src and "flex:none" in src)
    check("归属徽标用头像+用户名形态", "tag own" in src and 'class="av"' in src and 'class="un"' in src)
    check("未绑定用虚线空槽（中性态，非告警色）", "tag own free" in src and "border-style:dashed" in src)
    check("未绑定不再用橙色告警块", "background:#fff4e5" not in src)
    check("未绑定不再用 🔗 emoji（语义错位）", "🔗" not in src)
    check("归属不再用 👤 emoji（与矿机名同构）", "👤" not in src)
    check("机器标识用等宽次级样式", "tag mid" in src and ".tag.mid{" in src)
    check("元信息行空态折叠", ".rig-meta:empty{display:none}" in src)
    check("展开菜单的卡片抬升层级（不被相邻卡片压住）", ".rig.menu-open{" in src)

    print("\n" + "=" * 60)
    print("PASS %d / FAIL %d" % (PASS, FAIL))
    if FAIL == 0:
        print("全部通过")
    else:
        print("存在失败用例")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
