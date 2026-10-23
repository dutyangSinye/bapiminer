/* Screenshot minrigs dashboard: set admin token, reload, capture. Usage: node shot_minrigs.js <port> <outPng> */
const CDP = process.argv[2] || '9335';
const OUT = process.argv[3];
const TOKEN = process.env.TOK || 'adm_REPLACE_ME';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const targets = await (await fetch(`http://127.0.0.1:${CDP}/json`)).json();
  const t = targets.find(x => x.type === 'page' && x.url.includes(process.env.URLMATCH || 'nas.sinyerobot.com'));
  if (!t) { console.log('NO TAB', JSON.stringify(targets.map(x => x.url))); return; }
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
  await cmd('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1200, deviceScaleFactor: 1, mobile: false });
  await cmd('Runtime.evaluate', { expression: `localStorage.setItem('bapiminer_admin_token','${TOKEN}'); location.reload();` });
  await sleep(6000);
  const state = await cmd('Runtime.evaluate', { expression: `document.getElementById('authCard').style.display + '|' + document.getElementById('main').style.display + '|' + (document.body.innerText.includes('游戏模式') ? 'GM_UI_VISIBLE' : 'GM_UI_MISSING') + '|' + (document.body.innerText.includes('游戏模式·开启') ? 'GM_BTN_OK' : 'GM_BTN_MISSING')`, returnByValue: true });
  console.log('state:', state.result.value);
  if (process.env.SCROLL) {
    await cmd('Runtime.evaluate', { expression: `(function(){var e=document.getElementById('${process.env.SCROLL}'); if(e) e.scrollIntoView({block:'center'});})()` });
    await sleep(600);
  }
  const shot = await cmd('Page.captureScreenshot', { format: 'png' });
  require('fs').writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
  console.log('saved ' + OUT);
  ws.close();
}
main().then(() => process.exit(0)).catch(e => { console.error('FAIL ' + e.message); process.exit(1); });
