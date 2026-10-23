#!/bin/bash
set -e
export SSHPASS='YYyy..83'
NAS=13301116664@124.92.66.20
SSHOPT="ssh -p 10000 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"

echo "=== 1. stop container ==="
docker stop minrigs >/dev/null 2>&1 || true
docker ps -a --filter name=minrigs --format '{{.Names}} | {{.Status}}'

echo "=== 2. clear fresh db ==="
rm -f /opt/minrigs/data/fleet.db /opt/minrigs/data/fleet.db-wal /opt/minrigs/data/fleet.db-shm
ls -la /opt/minrigs/data/

echo "=== 3. rsync from NAS ==="
sshpass -e rsync -a --info=stats2 \
  --exclude 'fleet.db-wal' --exclude 'fleet.db-shm' --exclude 'minrigs.db' \
  -e "$SSHOPT" \
  $NAS:/data_7HKERMAF/minrigs/data/ /opt/minrigs/data/ 2>&1 | grep -Ev '^$' | tail -5

echo "=== 4. result ==="
ls -la /opt/minrigs/data/
echo "--- downloads ---"; ls -la /opt/minrigs/data/downloads/
echo "--- kernels ---"; ls -la /opt/minrigs/data/kernels/

echo "=== 5. db sanity ==="
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('/opt/minrigs/data/fleet.db')
print('integrity:', c.execute('pragma integrity_check').fetchone()[0])
for t in ('rigs','samples','alerts'):
    try:
        print(t, c.execute('select count(*) from '+t).fetchone()[0])
    except Exception as e:
        print(t, 'ERR', e)
print('rigs:', c.execute('select name, token, last_seen from rigs').fetchall())
PY

echo "=== 6. start container ==="
docker start minrigs >/dev/null
sleep 4
docker ps --filter name=minrigs --format '{{.Names}} | {{.Status}}'
echo "--- logs ---"
docker logs --tail 12 minrigs 2>&1
