#!/bin/sh
export PW='YYyy..83'
echo '--- 鉴权正例 ---'
curl -s --noproxy '*' -H 'X-Admin-Token: ${ADMIN_TOKEN:-adm_REPLACE_ME}' http://127.0.0.1:8099/api/fleet | head -c 120
echo
echo '--- 鉴权反例(应 401) ---'
curl -s --noproxy '*' -o /dev/null -w 'no-token=%{http_code}\n' http://127.0.0.1:8099/api/fleet
curl -s --noproxy '*' -o /dev/null -w 'bad-token=%{http_code}\n' -H 'X-Admin-Token: wrong' http://127.0.0.1:8099/api/fleet
echo '--- 自愈复测 ---'
echo "$PW" | sudo -S -p '' docker restart minrigs >/dev/null
sleep 6
curl -s --noproxy '*' http://127.0.0.1:8099/api/health
echo
echo "$PW" | sudo -S -p '' docker ps --filter name=minrigs --format '{{.Status}}'
