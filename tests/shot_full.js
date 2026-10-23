/* Full-page screenshot: node shot_full.js <cdpPort> <outPng> */
const CDP = process.argv[2] || '9338';
const OUT = process.argv[3];
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function main() {
  const targets = await (await fetch(`http://127.0.0.1:${CDP}/json`)).json();
  const t = targets.find(x => x.type === 'page' && x.url.includes(process.env.URLMATCH || 'nas.sinyerobot.com'));
  if (!t) { console.log('NO TAB'); return; }
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let mid = 0; const pending = new Map();
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.rej(new Error(m.error.message)) : p.res(m.result);
    }
  };
  const cmd = (method, params) => new Promise((res, rej) => {
    const id = ++mid; pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  await cmd('Runtime.evaluate', { expression: `localStorage.setItem('bapiminer_admin_token','${process.env.TOK}'); location.href='#rigs';` });
  await sleep(4500);
  const shot = await cmd('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  require('fs').writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
  console.log('saved ' + OUT);
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('FAIL ' + e.message); process.exit(1); });
