/* Mobile screenshot via CDP device emulation (bypasses Edge min window width 500).
   Usage: node shot_mobile.js <cdpPort> <outPng> <width> <height> */
const CDP = process.argv[2] || '9336';
const OUT = process.argv[3] || 'mobile.png';
const W = parseInt(process.argv[4] || '390', 10);
const H = parseInt(process.argv[5] || '844', 10);
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  const targets = await (await fetch(`http://127.0.0.1:${CDP}/json`)).json();
  const t = targets.find(x => x.type === 'page' && x.url.includes('nas.sinyerobot.com'));
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
  const send = (method, params) => new Promise((res, rej) => {
    const id = ++mid; pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params }));
  });
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: W, height: H, deviceScaleFactor: 2, mobile: true,
  });
  await send('Page.reload', { ignoreCache: true });
  await sleep(9000);
  const expr = `(() => {
    const vw = document.documentElement.clientWidth;
    const sw = document.documentElement.scrollWidth;
    const bad = [];
    document.querySelectorAll('*').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.right > vw + 2 && r.width > 10) {
        bad.push(el.tagName + '.' + String(el.className).split(' ').slice(0,2).join('.') + ' right=' + Math.round(r.right) + ' w=' + Math.round(r.width));
      }
    });
    return JSON.stringify({ vw, sw, overflow: bad.slice(0, 25) });
  })()`;
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
  console.log('PROBE:', r.result.value);
  // 全页截图
  const sh = await send('Runtime.evaluate', { expression: 'document.body.scrollHeight', returnByValue: true });
  await send('Emulation.setDeviceMetricsOverride', {
    width: W, height: Math.min(sh.result.value + 40, 5000), deviceScaleFactor: 2, mobile: true,
  });
  await sleep(1500);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  require('fs').writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
  console.log('SAVED', OUT);
  ws.close();
}
main().catch(e => { console.error(e); process.exit(1); });
