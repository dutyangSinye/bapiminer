/* 中控看板走查：验证「今日实际收入」真实渲染 + 卡片各分区高度一致 + 截图 + 控制台错误收集。
   用法: node walk_dashboard.js <cdpPort> <outPng> <url> [adminToken] */
const CDP = process.argv[2] || '9333';
const OUT = process.argv[3];
const URL = process.argv[4];
const TOKEN = process.argv[5] || '';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function main() {
  let targets = null;
  for (let i = 0; i < 30; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${CDP}/json`)).json();
      if (targets.some(t => t.type === 'page')) break;
    } catch (e) { /* 端口未起 */ }
    await sleep(500);
  }
  const t = (targets || []).find(x => x.type === 'page');
  if (!t) { console.log('NO TAB'); process.exit(1); }
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.onopen = (e) => { console.error('[ws] open'); res(e); };
    ws.onerror = (e) => {
      console.error('[ws] error type=' + typeof e + ' ctor=' +
        (e && e.constructor ? e.constructor.name : '?'));
      rej(e || new Error('ws error without event'));
    };
    setTimeout(() => rej(new Error('ws open timeout')), 10000);
  });
  let mid = 0;
  const pending = new Map();
  const errors = [];
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.rej(new Error(m.error.message)) : p.res(m.result);
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      errors.push('EXCEPTION: ' + (d.exception && d.exception.description || d.text));
    }
    if (m.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(m.params.type)) {
      errors.push(m.params.type.toUpperCase() + ': ' +
        m.params.args.map(a => a.value || a.description || '').join(' '));
    }
  };
  const cmd = (method, params) => new Promise((res, rej) => {
    const id = ++mid;
    pending.set(id, { res, rej });
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
  await cmd('Runtime.enable');
  await cmd('Page.enable');
  await cmd('Network.enable');
  await cmd('Network.setCacheDisabled', { cacheDisabled: true });  // 避免拿到缓存的旧 HTML
  console.error('[step] Runtime.enable ok');
  // 令牌在页面脚本前写入 localStorage，省掉登录卡
  if (TOKEN) {
    await cmd('Page.addScriptToEvaluateOnNewDocument', {
      source: `try{localStorage.setItem('bapiminer_admin_token', ${JSON.stringify(TOKEN)})}catch(e){}`
    });
    console.error('[step] addScript ok');
  }
  await cmd('Emulation.setDeviceMetricsOverride',
    { width: 1440, height: 2600, deviceScaleFactor: 1, mobile: false });
  console.error('[step] metrics ok');
  await cmd('Page.navigate', { url: URL });
  console.error('[step] navigate ok');

  // 轮询等「今日实际收入」渲染出真实值（钱包循环 5 分钟一刷，首刷后即有值）
  let state = null;
  for (let i = 0; i < 40; i++) {
    await sleep(1000);
    const r = await cmd('Runtime.evaluate', {
      expression: `(() => {
        const g = id => document.getElementById(id);
        const H = el => el ? Math.round(el.getBoundingClientRect().height) : 0;
        return JSON.stringify({
          kt: g('kt') ? g('kt').innerText.trim() : null,
          ktm: g('ktm') ? g('ktm').innerText.trim() : null,
          authHidden: g('authCard') ? g('authCard').style.display === 'none' : null,
          mainShown: g('main') ? g('main').style.display !== 'none' : null,
          k1: g('k1') ? g('k1').innerText.trim() : '',
          k2: g('k2') ? g('k2').innerText.trim() : '',
          kw: g('kw') ? g('kw').innerText.trim() : '',
          k5: g('k5') ? g('k5').innerText.trim() : '',
          dailyChips: document.querySelectorAll('#dailyList .inc').length,
          walletChips: document.querySelectorAll('.rig-ft .income').length,
          rigCards: document.querySelectorAll('.rig').length,
          bodyLen: document.body.innerText.length,
          cards: Array.from(document.querySelectorAll('.rig')).map(el => {
            const nm = el.querySelector('.nm');
            const hd = el.querySelector('.rig-hd');
            const tags = el.querySelector('.rig-tags');
            const big = el.querySelector('.big');
            const chips = Array.from(el.querySelectorAll('.rig-ft .income')).map(c => c.innerText.trim());
            return {
              name: nm ? nm.innerText.trim() : '?',
              cardH: H(el),
              hdH: H(hd),
              tagsH: H(tags),
              tags: Array.from(el.querySelectorAll('.rig-tags .tag')).map(x => x.innerText.trim()),
              bigH: H(big),
              bigText: big ? big.innerText.replace(/\\s+/g, ' ').trim() : '',
              metricsH: H(el.querySelector('.metrics')),
              sparkH: H(el.querySelector('.spark')),
              gpuRowH: H(el.querySelector('.gpu-row')),
              gpuN: el.querySelectorAll('.gpu-chip').length,
              ftH: H(el.querySelector('.rig-ft')),
              walletChip: chips.some(c => c.indexOf('\\uD83C\\uDFE6') >= 0),
              chips: chips
            };
          })
        });
      })()`, returnByValue: true
    });
    state = JSON.parse(r.result.value);
    if (state.kt && state.kt !== '—' && state.bodyLen > 3000) break;
  }
  console.log('state: ' + JSON.stringify({ ...state, cards: undefined }));
  (state.cards || []).forEach(c => console.log(
    'card ' + c.name + ' | cardH=' + c.cardH + ' hdH=' + c.hdH + ' tagsH=' + c.tagsH +
    ' bigH=' + c.bigH + ' metricsH=' + c.metricsH + ' sparkH=' + c.sparkH +
    ' gpuRowH=' + c.gpuRowH + '(n=' + c.gpuN + ') ftH=' + c.ftH +
    ' walletChip=' + c.walletChip));
  (state.cards || []).forEach(c => console.log(
    '  tags[' + c.name + ']: ' + c.tags.join(' / ') + ' | big: ' + c.bigText +
    ' | chips: ' + c.chips.join(' / ')));

  const bad = [];
  if (state.authHidden === false) bad.push('登录卡仍显示（令牌未生效）');
  if (!state.mainShown) bad.push('主面板未显示');
  if (!state.kt || state.kt === '—') bad.push('今日实际收入无值: ' + state.kt);
  else if (!/PRL/.test(state.kt)) bad.push('今日实际收入非币数口径: ' + state.kt);
  if (!state.ktm || state.ktm.length < 4) bad.push('今日实际收入副值为空');
  if (!state.rigCards) bad.push('矿机卡片未渲染');
  if (!state.dailyChips) bad.push('按天明细未渲染');
  // ---- 卡片分区高度一致性（≥2 张卡才可比较）----
  const cs = state.cards || [];
  if (cs.length >= 2) {
    for (const key of ['cardH', 'hdH', 'tagsH', 'bigH', 'metricsH', 'sparkH', 'ftH']) {
      const hs = cs.map(c => c[key]);
      if (new Set(hs).size > 1) {
        bad.push('分区高度不一致 ' + key + ': ' + cs.map(c => c.name + '=' + c[key]).join(', '));
      }
    }
  }
  console.log('assertions: ' + (bad.length ? 'FAIL -> ' + bad.join('; ') : 'ALL PASS'));
  console.log('console errors/warnings: ' + errors.length);
  errors.slice(0, 10).forEach(e => console.log('  ' + String(e).slice(0, 300)));

  const shot = await cmd('Page.captureScreenshot', { format: 'png' });
  require('fs').writeFileSync(OUT, Buffer.from(shot.data, 'base64'));
  console.log('saved ' + OUT);
  ws.close();
  process.exit(bad.length || errors.length ? 1 : 0);
}
main().catch(e => {
  console.error('FAIL type=' + typeof e + ' value=' + String(e) +
    ' stack=' + (e && e.stack ? String(e.stack).slice(0, 400) : '(no stack)'));
  process.exit(1);
});
