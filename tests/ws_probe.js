/* 最小复现：连 CDP WebSocket 并打印错误详情 */
const CDP = process.argv[2] || '9333';
(async () => {
  const targets = await (await fetch(`http://127.0.0.1:${CDP}/json`)).json();
  const t = targets.find(x => x.type === 'page');
  console.log('target:', t && t.url, t && t.webSocketDebuggerUrl);
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  ws.onopen = () => { console.log('WS OPEN'); ws.close(); process.exit(0); };
  ws.onerror = (e) => {
    console.log('WS ERROR type=', e && e.type, 'ctor=', e && e.constructor && e.constructor.name);
    console.log('  message=', e && e.message, '| error=', e && e.error, '| keys=', e && Object.keys(e));
    process.exit(2);
  };
  ws.onclose = (e) => { console.log('WS CLOSE code=', e.code, 'reason=', e.reason); process.exit(3); };
  setTimeout(() => { console.log('TIMEOUT'); process.exit(4); }, 8000);
})();
