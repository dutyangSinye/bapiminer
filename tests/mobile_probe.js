// 手机视口（390x844）探查 KPI 网格为何退化成单列
const PORT = parseInt(process.argv[2] || "9335", 10);
const URL = process.argv[3] || "http://bapimine.click:8099/";
const TOKEN = process.argv[4] || "";

function httpGet(path) {
  return new Promise((res, rej) => {
    require("http").get({ host: "127.0.0.1", port: PORT, path }, r => {
      let b = "";
      r.on("data", c => (b += c));
      r.on("end", () => res(JSON.parse(b)));
    }).on("error", rej);
  });
}

async function main() {
  const list = await httpGet("/json");
  const t = list.find(x => x.type === "page");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  const cmd = (method, params) => new Promise((res, rej) => {
    const mid = ++id;
    pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  await cmd("Runtime.enable");
  await cmd("Page.enable");
  if (TOKEN) {
    await cmd("Page.addScriptToEvaluateOnNewDocument", {
      source: `try{localStorage.setItem('bapiminer_admin_token', ${JSON.stringify(TOKEN)})}catch(e){}`
    });
  }
  await cmd("Emulation.setDeviceMetricsOverride",
    { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await cmd("Page.navigate", { url: URL });
  await new Promise(r => setTimeout(r, 9000));
  const r = await cmd("Runtime.evaluate", {
    expression: `(() => {
      const out = { vp: null, kpis: [] };
      const m = document.querySelector('meta[name=viewport]');
      out.vp = m ? m.content : '(none)';
      document.querySelectorAll('.kpis').forEach((g, gi) => {
        const cs = getComputedStyle(g);
        const cards = [...g.querySelectorAll('.kpi')].slice(0, 4).map(c => {
          const b = c.getBoundingClientRect();
          const mm = c.querySelector('.m');
          const mcs = mm ? getComputedStyle(mm) : null;
          return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width),
                   mText: mm ? mm.textContent.slice(0, 24) : '',
                   mWS: mcs ? mcs.whiteSpace : '',
                   mScroll: mm ? mm.scrollWidth : 0, mClient: mm ? mm.clientWidth : 0 };
        });
        out.kpis.push({ gi, cols: cs.gridTemplateColumns, cards });
      });
      out.winW = window.innerWidth;
      return JSON.stringify(out);
    })()`, returnByValue: true
  });
  console.log(r.result.value);
  ws.close();
}

main().catch(e => { console.error("FAIL", String(e && e.message || e)); process.exit(1); });
