#!/bin/bash
export SSHPASS='YYyy..83'
NAS=13301116664@124.92.66.20
SSHOPT="-p 10000 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"

echo "=== remote dirs ==="
sshpass -e ssh $SSHOPT $NAS "ls -la /data_7HKERMAF/minrigs/ ; echo '--- data ---'; ls -la /data_7HKERMAF/minrigs/data/ ; echo '--- downloads ---'; ls -la /data_7HKERMAF/minrigs/data/downloads/ ; echo '--- kernels ---'; ls -la /data_7HKERMAF/minrigs/data/kernels/"

echo "=== rsync available? ==="
sshpass -e ssh $SSHOPT $NAS "which rsync tar || echo NO_RSYNC"
