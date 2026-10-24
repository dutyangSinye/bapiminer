// 整页截图（captureBeyondViewport）留证移动端最终效果
const PORT = parseInt(process.argv[2] || "9346", 10);
const URL = process.argv[3];
const TOKEN = process.argv[4] || "";
const OUT = process.argv[5];
function httpGet(path) {
  return new Promise((res, rej) => {
    require("http").get({ host: "127.0.0.1", port: PORT, path }, r => {
      let b = ""; r.on("data", c => (b += c)); r.on("end", () => res(JSON.parse(b)));
    }).on("error", rej);
  });
}
async function main() {
  const list = await httpGet("/json");
  const t = list.find(x => x.type === "page");
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { res, rej } = pending.get(m.id); pending.delete(m.id);
      m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
    }
  };
  const cmd = (method, params) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  await cmd("Runtime.enable"); await cmd("Page.enable");
  if (TOKEN) await cmd("Page.addScriptToEvaluateOnNewDocument", {
    source: `try{localStorage.setItem('bapiminer_admin_token', ${JSON.stringify(TOKEN)})}catch(e){}`
  });
  await cmd("Emulation.setDeviceMetricsOverride",
    { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await cmd("Page.navigate", { url: URL });
  await new Promise(r => setTimeout(r, 9000));
  const metrics = await cmd("Page.getLayoutMetrics");
  const h = Math.min(Math.ceil(metrics.cssContentSize.height), 8000);
  const shot = await cmd("Page.captureScreenshot",
    { format: "png", captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: 390, height: h, scale: 1 } });
  require("fs").writeFileSync(OUT, Buffer.from(shot.data, "base64"));
  console.log("full-page screenshot saved, height =", h);
  ws.close();
}
main().catch(e => { console.error("FAIL", String(e && e.message || e)); process.exit(1); });
