# -*- coding: utf-8 -*-
"""诊断 NAS -> pool.pearlhash.xyz 连通性（首页 + 详细错误）。"""
import os
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=30):
    si, so, se = cli.exec_command(cmd, timeout=timeout)
    return (so.read().decode("utf-8", "replace")
            + se.read().decode("utf-8", "replace")).strip()


print("== DNS ==")
print(run("getent hosts pool.pearlhash.xyz || echo NO_DNS"))
print("\n== 首页 ==")
print(run("curl -s -o /dev/null -w 'code=%{http_code} time=%{time_total}s' "
          "--max-time 12 -A 'bapiminer/1.0' https://pool.pearlhash.xyz/"))
print("\n== API verbose（取前 25 行）==")
# 钱包地址不进仓库：export MINRIGS_TEST_WALLET=prl1... 后再跑
_wallet = os.environ.get("MINRIGS_TEST_WALLET", "")
if not _wallet:
    raise SystemExit("请先设置 MINRIGS_TEST_WALLET")
out = run("curl -sv --max-time 12 -A 'bapiminer/1.0' "
          "'https://pool.pearlhash.xyz/api/v1/miner/balance/%s' 2>&1 | head -25" % _wallet)
print(out)
cli.close()
