/**
 * 端到端：GPU 页面手填价 → 写入中控报价池 → 排名重算 → 刷新后仍在。
 */
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const http = require('http');

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const BASE = process.argv[2] || 'http://127.0.0.1:18099';
const OUT = 'C:/Users/Admin/WorkBuddy/2026-10-19-18-33-08/minrigs/tests/shots';
const PORT = 9337;
if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true });
const sleep = ms => new Promise(r => setTimeout(r, ms));

function getJSON(url) {
  return new Promise((resolve, reject) => {
    http.get(url, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    }).on('error', reject);
  });
}

(async () => {
  const proc = spawn(EDGE, [
    '--headless=new', '--disable-gpu', '--hide-scrollbars',
    '--remote-debugging-port=' + PORT,
    '--user-data-dir=' + path.join(OUT, '_edge_profile3'),
    '--no-first-run', '--no-default-browser-check', 'about:blank'
  ], { stdio: 'ignore' });

  let target = null;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    try {
      const list = await getJSON(`http://127.0.0.1:${PORT}/json/list`);
      const page = list.find(t => t.type === 'page');
      if (page && page.webSocketDebuggerUrl) { target = page; break; }
    } catch (e) { }
  }
  if (!target) { console.error('CDP 未就绪'); proc.kill(); process.exit(1); }

  const WebSocket = require('ws');
  const ws = new WebSocket(target.webSocketDebuggerUrl, { perMessageDeflate: false, maxPayload: 128 * 1024 * 1024 });
  let id = 0;
  const pending = new Map();
  const errors = [];
  ws.on('message', raw => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
    if (m.method === 'Runtime.exceptionThrown') errors.push('EXC ' + m.params.exceptionDetails.text);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      errors.push('console.error: ' + m.params.args.map(a => a.value || a.description || '').join(' '));
    }
  });
  await new Promise(r => ws.on('open', r));
  function send(method, params = {}) {
    return new Promise(res => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
  }
  async function evaluate(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result && r.result.exceptionDetails) return 'EXC ' + JSON.stringify(r.result.exceptionDetails);
    return r.result && r.result.result ? r.result.result.value : undefined;
  }
  async function shot(file, w, h) {
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1.5, mobile: false });
    await sleep(1000);
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(OUT, file), Buffer.from(r.result.data, 'base64'));
    console.log('SHOT ' + file);
  }

  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1.5, mobile: false });
  await send('Page.navigate', { url: BASE + '/gpu' });
  await sleep(6000);

  const before = await evaluate(`JSON.stringify({
    cloud: (document.getElementById('cloudText')||{}).textContent,
    rows: document.querySelectorAll('#tbody tr').length,
    k1: (document.getElementById('k1v')||{}).textContent,
    k1m: (document.getElementById('k1m')||{}).textContent
  })`);
  console.log('BEFORE ' + before);

  // 切到「全部算力」筛选 —— 默认「只看有报价」下没有空输入框可填
  await evaluate(`document.querySelector('#filterSeg button[data-f="all"]').click()`);
  await sleep(1200);
  const allMode = await evaluate(`JSON.stringify({
    rows: document.querySelectorAll('#tbody tr').length,
    inputs: document.querySelectorAll('#tbody .mpin').length
  })`);
  console.log('ALL_MODE ' + allMode);

  // 找一个"有报价但价格低"的行，模拟手填价改价
  const filled = await evaluate(`(async () => {
    const rows = [...document.querySelectorAll('#tbody tr')];
    const target = rows.find(r => r.querySelector('.mpin'));
    if (!target) return 'no input';
    const inp = target.querySelector('.mpin');
    const model = inp.dataset.mp;
    inp.value = '999';
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    return model;
  })()`);
  console.log('FILLED ' + filled);
  await sleep(3000);

  const after = await evaluate(`JSON.stringify({
    cloud: (document.getElementById('cloudText')||{}).textContent,
    rows: document.querySelectorAll('#tbody tr').length,
    k1: (document.getElementById('k1v')||{}).textContent,
    k1m: (document.getElementById('k1m')||{}).textContent,
    localStorageManual: (localStorage.getItem('gpu_manual_prices_v1')||'{}')
  })`);
  console.log('AFTER ' + after);

  await evaluate(`document.querySelector('.table-wrap').scrollIntoView({block:'start'})`);
  await sleep(800);
  await shot('gpu_manual_filled.png', 1440, 900);

  // 刷新页面，验证报价池持久化 + 本机手填仍在
  await send('Page.navigate', { url: BASE + '/gpu' });
  await sleep(6000);
  const reloaded = await evaluate(`JSON.stringify({
    cloud: (document.getElementById('cloudText')||{}).textContent,
    k1: (document.getElementById('k1v')||{}).textContent,
    k1m: (document.getElementById('k1m')||{}).textContent,
    rows: document.querySelectorAll('#tbody tr').length
  })`);
  console.log('RELOADED ' + reloaded);

  console.log('JS_ERRORS ' + JSON.stringify(errors));
  ws.close(); proc.kill(); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
