# -*- coding: utf-8 -*-
"""从 NAS 拉 wildrig-0.51.7.zip 到本地 .wb_wildrig_test/ 并解压。"""
import os, paramiko, zipfile

DEST = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\.wb_wildrig_test"
os.makedirs(DEST, exist_ok=True)
zp = os.path.join(DEST, "wildrig-0.51.7.zip")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("116.169.217.29", 22, "root", os.environ.get("MINRIGS_SSH_PASS", ""), timeout=25,
            look_for_keys=False, allow_agent=False)
sftp = cli.open_sftp()
sftp.get("/opt/minrigs/data/kernels/wildrig-0.51.7.zip", zp)
print("downloaded:", os.path.getsize(zp), "bytes")
sftp.close()
cli.close()

with zipfile.ZipFile(zp) as z:
    names = z.namelist()
    print("entries:", len(names))
    for n in names[:20]:
        print("  ", n)
    z.extractall(DEST)
print("extracted to", DEST)
