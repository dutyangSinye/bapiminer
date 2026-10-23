/* Probe mobile overflow: connect CDP, evaluate scrollWidth and list elements wider than viewport. */
const CDP = process.argv[2] || '9336';
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
  await sleep(9000); // 等数据加载
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
    return JSON.stringify({ vw, sw, innerW: window.innerWidth, dpr: window.devicePixelRatio, bad: bad.slice(0, 25) }, null, 1);
  })()`;
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
  console.log(r.result.value);
  ws.close();
}
main().catch(e => { console.error(e); process.exit(1); });
