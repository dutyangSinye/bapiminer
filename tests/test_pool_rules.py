# -*- coding: utf-8 -*-
"""矿池/内核选择规则的离线验证：不启动 GUI，只验证数据与筛选逻辑。"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "client", "miner_client.py")

spec = importlib.util.spec_from_file_location("mc", SRC)
mc = importlib.util.module_from_spec(spec)
sys.modules["mc"] = mc
spec.loader.exec_module(mc)

ok_n = 0
fail = []


def check(name, cond, detail=""):
    global ok_n
    if cond:
        ok_n += 1
        print("PASS  %s%s" % (name, ("  " + detail) if detail else ""))
    else:
        fail.append(name)
        print("FAIL  %s  %s" % (name, detail))


# 1. 有港澳台节点的池，只返回该节点
for bid, want_label in [("kryptex", "中国香港"), ("k1pool", "中国线路")]:
    nodes, tier = mc.pool_candidates(bid)
    check("%s 只给中国区节点" % bid,
          tier == mc.REGION_HMT and len(nodes) == 1 and nodes[0]["label"] == want_label,
          "tier=%s 节点=%s" % (tier, [n["label"] for n in nodes]))
    b = mc.pool_brand(bid)
    check("%s 隐藏了海外节点" % bid, len(b["nodes"]) > len(nodes),
          "总数 %d -> 显示 %d" % (len(b["nodes"]), len(nodes)))

# 2. 无中国节点的池，仍要列出来
for bid in ("f2pool", "herominers", "alphapool", "pearlhash"):
    nodes, tier = mc.pool_candidates(bid)
    check("%s 无直连也保留" % bid, tier == mc.REGION_NONE if hasattr(mc, "REGION_NONE") else tier == "none",
          "tier=%s 节点=%s" % (tier, [n["label"] for n in nodes]))
    check("%s 给出全部节点" % bid, len(nodes) == len(mc.pool_brand(bid)["nodes"]))

# 3. 每个品牌都至少有一个可选项
for b in mc.POOL_BRANDS:
    nodes, _t = mc.pool_candidates(b["id"])
    check("品牌 %s 有可选节点" % b["id"], len(nodes) >= 1)

# 4. url 往返一致
for b in mc.POOL_BRANDS:
    for n in b["nodes"]:
        u = mc.node_url(n)
        rb, rn = mc.brand_of_url(u)
        check("URL 反查 %s" % u, rb is not None and rn["host"] == n["host"])

# 5. 旧配置里的历史地址仍能识别（迁移兼容）
legacy = "stratum+tcp://prl-hk.kryptex.network:7048"
rb, rn = mc.brand_of_url(legacy)
check("旧配置地址可识别", rb is not None and rb["id"] == "kryptex", legacy)
check("未知地址归自定义", mc.brand_of_url("stratum+tcp://x.example.com:1234")[0] is None)

# 6. 默认矿池应是中国区节点
dflt = mc.default_pool_url()
dn = [n for b in mc.POOL_BRANDS for n in b["nodes"] if mc.node_url(n) == dflt]
check("默认矿池是中国区节点", bool(dn) and dn[0]["region"] in (mc.REGION_CN, mc.REGION_HMT), dflt)

# 7. 没有任何节点的 IP 落在中国大陆 -> 说明"直连"只能按港澳台口径实现
regions = {(n["region"]) for b in mc.POOL_BRANDS for n in b["nodes"]}
check("数据里不存在大陆节点（口径说明）", mc.REGION_CN not in regions,
      "现有 region 集合=%s" % sorted(regions))

# 8. BzMiner 命令行：必须用 --worker 与 --set http_port
#    build_cmd 会校验 exe 是否存在，这里造一个临时内核目录
import tempfile
_kdir = tempfile.mkdtemp(prefix="kcheck_")
for _f in ("bzminer.exe", "SRBMiner-MULTI.exe"):
    open(os.path.join(_kdir, _f), "wb").close()

exe, args = mc.build_cmd("BzMiner", _kdir, "PRL (Pearl)",
                         "stratum+tcp://prl-hk.kryptex.network:7048",
                         "prl1testwallet", "rig01")
check("BzMiner 用 --worker", "--worker" in args and "-r" not in args, " ".join(args))
check("BzMiner 未使用旧的 --http_port", "--http_port" not in args)
check("BzMiner http_port 走 --set", "--set" in args and any(a.startswith("http_port=") for a in args))
check("BzMiner 算法名为 pearlhash", "pearlhash" in args)

exe2, args2 = mc.build_cmd("SRBMiner-MULTI", _kdir, "PRL (Pearl)",
                           "stratum+tcp://prl-hk.kryptex.network:7048",
                           "prl1testwallet", "rig01")
check("SRBMiner 钱包带 worker 后缀",
      any(a == "prl1testwallet.rig01" for a in args2), " ".join(args2))

# 10. WildRig 命令行：--algo/--url/--user，API 端口走 --api-port
open(os.path.join(_kdir, "wildrig.exe"), "wb").close()
exe3, args3 = mc.build_cmd("WildRig", _kdir, "PRL (Pearl)",
                           "stratum+tcp://pool.pearlhash.xyz:9000",
                           "prl1testwallet", "rig01")
check("WildRig 用 --user 钱包带 worker",
      any(a == "prl1testwallet.rig01" for a in args3)
      and args3[args3.index("--user") + 1] == "prl1testwallet.rig01", " ".join(args3))
check("WildRig 用 --url 而非 --pool/-p", "--url" in args3 and "-p" not in args3)
check("WildRig API 端口 17799",
      "--api-port" in args3 and args3[args3.index("--api-port") + 1] == "17799")
check("WildRig 算法名为 pearlhash", "pearlhash" in args3)

# 11. PearlHash 品牌数据：官方池 0 开发费要写进 note，Anycast 单节点
ph = mc.pool_brand("pearlhash")
check("PearlHash 品牌存在", ph is not None and ph["name"] == "PearlHash")
check("PearlHash 提示 WildRig 0 开发费", "WildRig" in ph["note"] and "0" in ph["note"])
check("PearlHash 节点是官方 anycast 入口",
      ph["nodes"][0]["host"] == "pool.pearlhash.xyz" and ph["nodes"][0]["port"] == 9000)

# 12. wildrig API 解析：shares.good 计入 accepted
fake = {"hashrate": {"total": ["10.5 KH/s", "10.8 KH/s", "11.0 KH/s"]},
        "shares": {"good": 123, "bad": 2, "total": 125},
        "uptime": 3600, "algo": "pearlhash",
        "pool": "stratum+tcp://pool.pearlhash.xyz:9000"}
mm = mc.parse_miner("wildrig", fake)
check("wildrig shares.good 计入 accepted", mm["accepted"] == 123, str(mm["accepted"]))
check("wildrig shares.bad 计入 rejected", mm["rejected"] == 2, str(mm["rejected"]))
check("wildrig hashrate 取最稳窗口", mm["ths"] > 0, str(mm["ths"]))

# 9. 每个内核都标了开发者费
for key, k in mc.KERNELS.items():
    check("内核 %s 有开发者费" % key, bool(k.get("dev_fee")), k.get("dev_fee") or "空")

print("\n%d passed, %d failed" % (ok_n, len(fail)))
if fail:
    print("失败项：" + "、".join(fail))
sys.exit(1 if fail else 0)
