# -*- coding: utf-8 -*-
"""在 NAS 容器内直连 Kryptex 余额 API，验证可达性与返回结构。"""
import os
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)

W = os.environ.get("MINRIGS_TEST_WALLET", "")
script = r'''
import json, urllib.request, urllib.error, time
W = "%s"
for coin in ("prl", "pearlhash", "prl-pearlhash"):
    url = "https://pool.kryptex.com/%%s/api/v1/miner/balance/%%s" %% (coin, W)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bapiminer/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
        print("%%s -> HTTP %%s (%%.1fs) %%s" %% (coin, r.status, time.time() - t0, body[:220]))
    except urllib.error.HTTPError as e:
        print("%%s -> HTTPError %%s (%%.1fs) %%s" %% (coin, e.code, time.time() - t0, e.read()[:160]))
    except Exception as e:
        print("%%s -> ERROR (%%.1fs) %%s: %%s" %% (coin, time.time() - t0, e.__class__.__name__, e))
''' % W

si, so, se = cli.exec_command("docker exec -i minrigs python3 -", timeout=120)
si.write(script)
si.flush()
si.channel.shutdown_write()
print(so.read().decode())
err = se.read().decode()
if err:
    print("ERR:", err[:500])
cli.close()
