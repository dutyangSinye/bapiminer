/* 探查 CDP 标签页当前状态：location/title/HTML 片段 */
const CDP = process.argv[2] || '9333';
const URL = process.argv[3];
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
  await cmd('Page.navigate', { url: URL });
  await sleep(6000);
  const r = await cmd('Runtime.evaluate', {
    expression: `JSON.stringify({
      href: location.href, title: document.title,
      bodyLen: document.body ? document.body.innerText.length : -1,
      html: document.body ? document.body.innerHTML.slice(0, 400) : '(no body)'
    })`, returnByValue: true
  });
  console.log(r.result.value);
  ws.close();
  process.exit(0);
})().catch(e => { console.error('FAIL ' + e.message); process.exit(1); });
