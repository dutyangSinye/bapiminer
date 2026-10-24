// 移动端补充走查：刷新按钮 / 头部 / 截图
const PORT = parseInt(process.argv[2] || "9340", 10);
const URL = process.argv[3];
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
      const out = {};
      const btn = document.querySelector('#btnRefresh');
      if (btn) {
        const cs = getComputedStyle(btn), b = btn.getBoundingClientRect();
        out.btn = { text: btn.textContent.trim().slice(0, 12), w: Math.round(b.width),
                    h: Math.round(b.height), pad: cs.padding, fs: cs.fontSize,
                    ownRow: (() => { const hd = btn.closest('.hd') || btn.parentElement;
                      const sib = [...hd.children].filter(e => e !== btn);
                      const br = b; return sib.every(s => { const sb = s.getBoundingClientRect();
                        return Math.abs(sb.y - br.y) > 4 || sb.x > br.x + br.width || sb.x + sb.width < br.x; });
                    })() };
      }
      const hd = document.querySelector('.hd');
      if (hd) { const cs = getComputedStyle(hd); out.hd = { pad: cs.padding, dir: cs.flexDirection }; }
      const kwm = document.querySelector('#kwm');
      if (kwm) { const b = kwm.getBoundingClientRect();
        out.kwm = { text: kwm.textContent.trim().slice(0, 40), h: Math.round(b.height),
                    lines: Math.round(b.height / parseFloat(getComputedStyle(kwm).lineHeight) * 10) / 10 }; }
      out.docW = document.documentElement.scrollWidth;
      return JSON.stringify(out);
    })()`, returnByValue: true
  });
  console.log(r.result.value);
  const shot = await cmd("Page.captureScreenshot", { format: "png" });
  require("fs").writeFileSync(process.argv[5] || ".wb_mobile_shot.png", Buffer.from(shot.data, "base64"));
  console.log("screenshot saved");
  ws.close();
}
main().catch(e => { console.error("FAIL", String(e && e.message || e)); process.exit(1); });
