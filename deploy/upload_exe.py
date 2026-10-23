# -*- coding: utf-8 -*-
"""上传新 exe 到新服务器下载区（覆盖旧版，SHA 由服务端自动重算）"""
import os
import sys
import paramiko

SRC = r"C:\Users\Admin\WorkBuddy\2026-10-19-18-33-08\minrigs\client\dist\BaPiMiner.exe"
DST = "/opt/minrigs/data/downloads/BaPiMiner.exe"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["SSH_HOST"], port=int(os.environ.get("SSH_PORT", "22")),
            username=os.environ["SSH_USER"], password=os.environ["SSH_PASS"],
            timeout=25, banner_timeout=30, look_for_keys=False, allow_agent=False)
sftp = cli.open_sftp()
sftp.put(SRC, DST)
st = sftp.stat(DST)
print("uploaded %s -> %s (%d bytes, local=%d)" %
      (SRC, DST, st.st_size, os.path.getsize(SRC)))
assert st.st_size == os.path.getsize(SRC), "SIZE MISMATCH"
sftp.close()
cli.close()
print("OK")
