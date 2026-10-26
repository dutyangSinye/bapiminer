#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""根路径分流验证：官网首页 vs 群控台 vs App 内嵌 WebView。

背景：
  根路径 "/" 现在是官网首页，群控台移到 "/group"。
  但已装的 uniapp APK 里 <web-view> 硬编码指向根路径 —— 必须保证
  App 流量访问根路径仍拿到群控台（老 App 不重打包也能用）。

覆盖：
  A 根路径分流：普通浏览器 → 首页；App UA → 群控台
  B 群控台别名路径
  C 首页链接指向正确（不能指回 "/" 自己）
  D 首页里的 App 兜底跳转脚本存在且逻辑正确
  E 群控台的绝对 API 路径（/group 下 fetch('/api/...') 必须正确）
  F 首页静态资源在 /group 下不受影响
"""
import os
import re
import sys
import urllib.request

BASE = os.environ.get("PREVIEW_BASE", "http://127.0.0.1:18946").rstrip("/")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "server", "static")

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


def get(path, ua=None):
    """返回 (status, body_text)。走 ProxyHandler({}) 绕开本机系统代理。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(BASE + path, headers={"User-Agent": ua} if ua else {})
    try:
        r = op.open(req, timeout=20)
        return r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# uniapp / HTML5+ App 内嵌 WebView 的典型 UA 特征
APP_UA = ("Mozilla/5.0 (Linux; Android 13; V2049A Build/TP1A) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Version/4.0 Chrome/89.0.4389.116 Mobile Safari/537.36 "
          "Html5plus/3.7.2 uniapp MinRigs")


print("=== A 根路径分流 ===")
c, body = get("/")
check("普通浏览器访问 / 得到官网首页", c == 200 and "扒皮矿工 BaPi Miner" in body,
      "code=%s" % c)
check("首页不是群控登录页", "扒皮矿工 · 群控" not in body and "🔒 登录群控" not in body, "")
check("首页含「群控登录」入口", "群控登录" in body, "")

c, body = get("/", ua=APP_UA)
check("App UA 访问 / 得到群控台", c == 200 and "扒皮矿工 · 群控" in body,
      "code=%s" % c)
check("App UA 拿到的是 index.html（群控）", "扒皮矿工 · 群控" in body, "")

for ua_name, ua in [("html5plus", "Html5plus/3.7.2"),
                     ("dcloud", "Mozilla/5.0 dcloud/3.0"),
                     ("uniapp", "Mozilla/5.0 uniapp/1.0"),
                     ("minrigs", "Mozilla/5.0 MinRigs/1.13")]:
    c, b = get("/", ua=ua)
    check("UA 含 %s 仍返回群控台" % ua_name, c == 200 and "扒皮矿工 · 群控" in b, "code=%s" % c)

c, b = get("/", ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/605.1")
check("普通 iPhone Safari 访问 / 得到首页（不被误判为 App）",
      c == 200 and "扒皮矿工 BaPi Miner" in b, "code=%s" % c)

print()
print("=== B 群控台别名路径 ===")
for p in ["/group", "/group/", "/group.html", "/dash", "/index.html"]:
    c, b = get(p)
    check("%s 返回群控台" % p, c == 200 and "扒皮矿工 · 群控" in b, "code=%s" % c)

c, b = get("/home")
check("/home 仍可访问（兼容旧链接）", c == 200 and "扒皮矿工 BaPi Miner" in b, "code=%s" % c)

print()
print("=== C 首页链接指向正确 ===")
home = open(os.path.join(STATIC, "home.html"), encoding="utf-8").read()
group_links = re.findall(r'href="(/group)"', home)
bad_self = re.findall(r'href="/"[^>]*>\s*(?:群控登录|进入群控台)', home)
check("首页「群控登录/进入群控台」共 %d 处，全部指向 /group" % len(group_links),
      len(group_links) >= 3, "count=%d" % len(group_links))
check("没有链接指回 /（会绕回首页自己）", len(bad_self) == 0, str(bad_self))

print()
print("=== D App 兜底跳转脚本 ===")
check("home.html 含 plusready 监听", "plusready" in home, "")
check("home.html 含 plus 对象轮询兜底", "typeof plus" in home, "")
check("跳转目标是 /group", "location.replace('/group')" in home, "")
check("已在 /group 时不重复跳转（防循环）",
      "location.pathname.indexOf('/group')" in home, "")

print()
print("=== E /group 下 API 绝对路径可用 ===")
# 注意：生产 /api/fleet 需要 token，未带 token 时返回 401 属正常 —— 这里只断言
# 「路径可达且返回合法状态码」，不要求 200（预览台 mock 不校验 token 才给 200）。
for api in ["/api/downloads", "/api/fleet"]:
    c, b = get(api)
    check("%s 在任意路径下均可达（绝对路径）" % api, c in (200, 401), "code=%s" % c)

print()
print("=== F 静态资源 ===")
for p in ["/bapiminer.svg", "/img/shot-dashboard.jpg", "/img/shot-client.jpg",
          "/img/shot-mobile.jpg"]:
    c, b = get(p)
    check("%s 可访问" % p, c == 200, "code=%s" % c)

print()
print("=" * 56)
print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
if FAIL:
    print("\n失败项：")
    for f in FAILURES:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
