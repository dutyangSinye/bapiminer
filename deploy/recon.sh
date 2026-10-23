#!/bin/sh
export PW='YYyy..83'
echo "$PW" | sudo -S -p '' docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null | head -20
echo '--- docker root / mirrors ---'
echo "$PW" | sudo -S -p '' docker info 2>/dev/null | grep -E 'Docker Root Dir|Registry Mirrors' -A2 | head -6
echo '--- disks ---'
df -h | grep -vE 'tmpfs|udev|overlay|shm' | tail -8
