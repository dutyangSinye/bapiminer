/* 探查卡片内 sparkline 的真实 DOM 结构 */
const CDP = process.argv[2] || '9334';
const URL = process.argv[3];
const TOKEN = process.argv[4] || '';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const targets = await (await fetch(`http://127.0.0.1:${CDP}/json`)).json();
  const t = targets.find(x => x.type === 'page');
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let mid = 0;
  const pending = new Map();
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.rej(new Error(m.error.message)) : p.res(m.result);
    }
  };
  const cmd = (method, params) => new Promise((res, rej) => {
    const id = ++mid;
    pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  await cmd('Runtime.enable');
  await cmd('Page.enable');
  if (TOKEN) {
    await cmd('Page.addScriptToEvaluateOnNewDocument', {
      source: `try{localStorage.setItem('bapiminer_admin_token', ${JSON.stringify(TOKEN)})}catch(e){}`
    });
  }
  await cmd('Page.navigate', { url: URL });
  await sleep(8000);
  const r = await cmd('Runtime.evaluate', {
    expression: `(() => {
      const cards = document.querySelectorAll('.rig');
      const c0 = cards[0];
      const svgs = c0 ? c0.querySelectorAll('svg') : [];
      const srcHasClass = [...document.scripts].some(s => (s.text || '').indexOf('class="spark"') >= 0);
      return JSON.stringify({
        href: location.href,
        sparkSel: document.querySelectorAll('.rig .spark').length,
        svgTotal: document.querySelectorAll('.rig svg').length,
        svg0Cls: svgs[0] ? svgs[0].getAttribute('class') : null,
        srcHasClass: srcHasClass,
        sparkZone: c0 ? (() => {
          const el = [...c0.children].find(x => x.tagName === 'svg' || (x.tagName === 'DIV' && x.style.height === '46px'));
          return el ? el.outerHTML.slice(0, 120) : '(none)';
        })() : null
      });
    })()`, returnByValue: true
  });
  console.log(r.result.value);
  ws.close();
  process.exit(0);
})().catch(e => { console.error('FAIL ' + e.message); process.exit(1); });
