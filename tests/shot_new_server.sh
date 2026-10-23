#!/bin/bash
# 用无头 Edge + CDP 截图新服务器看板（含 admin token 登录）
EDGE="/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
[ -x "$EDGE" ] || EDGE="/c/Program Files/Microsoft/Edge/Application/msedge.exe"
TMP="$(cygpath -u "$TEMP")/edge-cdp-minrigs"
OUT="C:/Users/Admin/WorkBuddy/2026-10-19-18-33-08/minrigs/tests/shots/mine_new.png"
NODE="/c/Users/Admin/.workbuddy/binaries/node/versions/22.22.2-6/node.exe"
SCRIPT="/c/Users/Admin/.workbuddy/skills/nas-docker-deploy/scripts/cdp_shot.js"

rm -rf "$TMP"
"$EDGE" --headless=new --disable-gpu --no-proxy-server --no-first-run \
  --remote-debugging-port=9344 --user-data-dir="$TMP" about:blank > /tmp/edge-mine.log 2>&1 &
sleep 6

"$NODE" "$SCRIPT" 9344 "http://mine.sinyerobot.com:8099/" "${ADMIN_TOKEN:-adm_REPLACE_ME}" "$OUT"
RC=$?

"$NODE" -e "fetch('http://127.0.0.1:9344/json/version').then(r=>r.json()).then(v=>{const ws=new WebSocket(v.webSocketDebuggerUrl);ws.onopen=()=>ws.send(JSON.stringify({id:1,method:'Browser.close',params:{}}));ws.onmessage=()=>process.exit(0);setTimeout(()=>process.exit(0),3000)}).catch(()=>process.exit(0))"
echo "SHOT_RC=$RC"
ls -la "C:/Users/Admin/WorkBuddy/2026-10-19-18-33-08/minrigs/tests/shots/" 2>/dev/null
exit $RC
