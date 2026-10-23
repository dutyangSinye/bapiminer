#!/bin/sh
set -e
export PW='YYyy..83'
cd /data_7HKERMAF/minrigs
echo "$PW" | sudo -S -p '' docker build -t minrigs-server:1.0 . 2>&1 | tail -4
echo "$PW" | sudo -S -p '' docker rm -f minrigs 2>/dev/null || true
echo "$PW" | sudo -S -p '' docker run -d --name minrigs --restart always \
  -p 8099:8099 \
  -v /data_7HKERMAF/minrigs/data:/app/data \
  -v /data_7HKERMAF/minrigs/config.json:/app/config.json \
  minrigs-server:1.0
sleep 5
echo '--- logs ---'
echo "$PW" | sudo -S -p '' docker logs minrigs 2>&1 | tail -6
echo '--- health ---'
curl -s --noproxy '*' http://127.0.0.1:8099/api/health
echo
echo '--- ps ---'
echo "$PW" | sudo -S -p '' docker ps --filter name=minrigs --format '{{.Names}} {{.Status}} {{.Ports}}'
