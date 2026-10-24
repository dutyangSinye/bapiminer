/* wallet group view walk: navigate, check #fGroup, screenshot, collect console errors.
   usage: node walk_wallet_group.js <cdpPort> <outPng> <url> */
const CDP = process.argv[2] || '9351';
const OUT = process.argv[3];
const URL = process.argv[4];
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  let targets = null;
  for (let i = 0; i < 30; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${CDP}/json`)).json();
      if (targets.some(t => t.type === 'page')) break;
    } catch (e) { /* port not up yet */ }
    await sleep(500);
  }
  const t = (targets || []).find(x => x.type === 'page');
  if (!t) { console.log('NO TAB'); process.exit(1); }
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = rej;
    setTimeout(() => rej(new Error('ws open timeout')), 10000);
  });
  let mid = 0;
  const pending = new Map();
  const consoleErrs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error')
      consoleErrs.push(m.params.entry.text);
    if (m.method === 'Runtime.exceptionThrown')
      consoleErrs.push(JSON.stringify(m.params.exceptionDetails).slice(0, 300));
  };
  const send = (method, params) => new Promise(res => {
    const id = ++mid;
    pending.set(id, res);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });

  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send('Network.setCacheDisabled', { cacheDisabled: true });
  await send('Page.navigate', { url: URL });
  await sleep(9000);  // fleet poll + render

  const ev = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    return r.result && r.result.result ? r.result.result.value : null;
  };

  const before = await ev(`document.querySelectorAll('.rig-card,[class*=rig]').length`);
  await ev(`const g=document.getElementById('fGroup'); if(g && !g.checked){g.checked=true;render();}`);
  await sleep(1200);
  const after = await ev(`({
    groups: document.querySelectorAll('.wgroup-hd').length,
    masks: [...document.querySelectorAll('.wgroup-hd .wm')].map(e => e.textContent),
    tags: document.querySelectorAll('.wgroup-hd .tag').length,
    cards: document.querySelectorAll('.rig-hd').length,
    title: document.title
  })`);
  console.log('pre-check rig-ish nodes:', before);
  console.log('AFTER GROUP:', JSON.stringify(after, null, 1));

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (shot.result && shot.result.data) {
    require('fs').writeFileSync(OUT, Buffer.from(shot.result.data, 'base64'));
    console.log('screenshot saved:', OUT);
  }
  console.log('console errors:', consoleErrs.length ? consoleErrs.slice(0, 5) : 'none');
  process.exit(0);
}
main().catch(e => { console.log('WALK FAIL:', e.message); process.exit(1); });
