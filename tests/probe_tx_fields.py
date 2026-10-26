# -*- coding: utf-8 -*-
"""拉取钱包最新 3 笔链上交易的完整字段，确认打款来源池的标识字段。"""
import json
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(__import__('os').environ.get("MINRIGS_HOST", ""), 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)

PY = r'''
import json, urllib.request
W = __import__('os').environ.get("MINRIGS_TEST_WALLET", "")
req = urllib.request.Request(
    "https://api.prlscan.com/v1/addresses/%s/txs?limit=5" % W,
    headers={"User-Agent": "bapiminer/1.0"})
d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())
for t in (d.get("items") or [])[:5]:
    print(json.dumps({k: t.get(k) for k in
                      ("time", "received_grains", "type", "type_label",
                       "label", "block_height", "confirmations")},
                     ensure_ascii=False))
'''
si, so, se = cli.exec_command("docker exec -i minrigs python -", timeout=40)
si.write(PY)
si.flush()
si.channel.shutdown_write()
print(so.read().decode("utf-8", "replace"))
err = se.read().decode("utf-8", "replace")
if err.strip():
    print("[ERR]", err)
cli.close()
