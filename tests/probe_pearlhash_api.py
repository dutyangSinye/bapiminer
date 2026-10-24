# -*- coding: utf-8 -*-
"""诊断 NAS -> pool.pearlhash.xyz 连通性（首页 + 详细错误）。"""
import os
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
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
out = run("curl -sv --max-time 12 -A 'bapiminer/1.0' "
          "'https://pool.pearlhash.xyz/api/v1/miner/balance/"
          "prl1plthdf0l9mmpnvr3zlyqqk5jd38hzz0yue678kwmw2z6kg6l7u8nqfv7y38' 2>&1 | head -25")
print(out)
cli.close()
