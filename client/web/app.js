/* 扒皮矿工 · 矿机端 Web UI 逻辑
   传输：本地 HTTP 回环 —— boot 全量 + poll 增量（400ms）+ action POST。
   token 取自路径（/<token>/index.html），由 Python 侧生成防本机误触。 */
"use strict";

var T = (location.pathname.replace(/^\/+/, "").split("/")[0] || "");
var APIBASE = "/" + T;

var S = {
  page: "mine", fields: {}, toggles: {}, kernel: null,
  pool: { brands: [], brand: null, nodes: [], node: "", custom_visible: false },
  running: false, logs: [], pollVer: 0,
  acct: { logged: false, username: "", user_id: 0, is_admin: false, msg: "" },
  // 首次登录强制改密：非空时账号区渲染改密表单
  acctPwdRequired: null
};

var PAGE_META = {
  mine: ["单机挖矿", "算力、收益与运行日志一屏掌握"],
  game: ["游戏模式", "有人玩游戏时自动让卡，退出后自动恢复"],
  fleet: ["群控中心", "接入中控看板，手机也能随时查看"],
  sys: ["系统设置", "挖矿内核、目录与运行选项"]
};

function $(id) { return document.getElementById(id); }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}

var toastTimer = null;
function toast(msg, kind) {
  var t = $("toast");
  t.textContent = msg; t.className = kind || "";
  t.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.style.display = "none"; }, 4200);
}

function post(op, args) {
  fetch(APIBASE + "/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ op: op, args: args || {} })
  }).catch(function (e) { toast("操作失败: " + e, "err"); });
}

/* ---------------- 页面导航 ---------------- */
function showPage(key) {
  S.page = key;
  var nodes = document.querySelectorAll("#nav .nav-item");
  for (var i = 0; i < nodes.length; i++)
    nodes[i].classList.toggle("on", nodes[i].dataset.k === key);
  var pages = document.querySelectorAll(".page");
  for (var j = 0; j < pages.length; j++)
    pages[j].classList.toggle("on", pages[j].id === "page-" + key);
  $("pageTitle").textContent = PAGE_META[key][0];
  $("pageSub").textContent = PAGE_META[key][1];
}

/* ---------------- 状态接收 ---------------- */
function apply(o) {
  switch (o.t) {
    case "log":
      S.logs.push(o);
      if (S.logs.length > 800) S.logs.splice(0, S.logs.length - 800);
      appendLogLine(o);
      break;
    case "log_clear":
      S.logs = []; $("logBox").textContent = ""; break;
    case "metrics":
      $("ths").textContent = o.ths;
      $("mAcc").textContent = o.acc; $("mGpu").textContent = o.gpu;
      $("mInc").textContent = o.income;
      $("gpuText").textContent = o.gpu_text;
      $("rigUptime").textContent = o.uptime;
      break;
    case "pill":
      var p = $("pill");
      p.textContent = o.text; p.className = "pill " + o.kind;
      break;
    case "runbtn":
      S.running = !!o.on;
      var b = $("startBtn");
      b.textContent = o.on ? "停止挖矿" : "开始挖矿";
      b.className = "btn big " + (o.on ? "solid-danger" : "pri");
      break;
    case "rig":
      $("rigWallet").textContent = o.wallet || "未设置";
      break;
    case "income":
      $("incomeBig").innerHTML = esc(o.disp) + "<small> 元/天</small>";
      $("incomeMeta").textContent = o.meta;
      $("walletDisp").textContent = o.disp2;
      $("walletBar").style.width = (o.bar || 0) + "%";
      $("walletNote").textContent = o.note;
      break;
    case "kchips": renderKchips(o); break;
    case "pool": renderPool(o); break;
    case "kernel_rows": renderKerRows(o.rows); break;
    case "conflict":
      $("conflictBar").classList.toggle("on", !!o.on);
      $("conflictTxt").textContent = o.text || "";
      break;
    case "toast": toast(o.msg, o.kind); break;
    case "tg":
      S.toggles[o.k] = o.v; renderToggles(); break;
    case "acct":
      S.acct = o.acct || S.acct;
      // 登录成功 → 改密流程结束，清掉改密表单状态
      if (S.acct && S.acct.logged) S.acctPwdRequired = null;
      renderAcct();
      break;
    case "acct_pwd_required":
      // 首次登录强制改密：切到改密表单
      S.acctPwdRequired = { username: o.username || "" };
      renderAcct();
      break;
    case "display_name":
      // 中控远程改名（D6）：静默同步，不弹窗
      S.fields.display_name = o.display_name || "";
      var dn = $("display_name");
      if (dn && document.activeElement !== dn) dn.value = S.fields.display_name;
      break;
    case "field_reset":
      // 只读字段被写入尝试 → 回滚界面值
      S.fields[o.key] = o.val || "";
      var fr = $(o.key);
      if (fr && document.activeElement !== fr) fr.value = o.val || "";
      break;
    case "confirm":
      if (window.confirm(o.msg) && o.action === "kernel_action")
        post("kernel_action", { key: o.arg, force: true });
      break;
  }
}

window.__push = apply;   // 兼容调试：可在控制台手动喂事件

function appendLogLine(o) {
  var box = $("logBox");
  var div = document.createElement("div");
  div.innerHTML = '<span class="ts">[' + esc(o.ts) + "]</span> <span class='" +
    esc(o.tag) + "'>" + esc(o.msg) + "</span>";
  box.appendChild(div);
  while (box.childNodes.length > 800) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

/* ---------------- 各区块渲染 ---------------- */
function renderKchips(o) {
  S.kernel = o.active;
  var html = "";
  for (var k in o.items)
    html += '<button class="chip' + (k === o.active ? " on" : "") +
      '" data-k="' + esc(k) + '">' + esc(o.items[k].label) + "</button>";
  $("kchips").innerHTML = html;
  var kids = $("kchips").children;
  for (var i = 0; i < kids.length; i++)
    kids[i].onclick = function () { post("pick_kernel", { key: this.dataset.k }); };
  var h = $("khint");
  h.textContent = o.hint; h.className = "hint " + (o.hint_kind || "");
}

function renderPool(o) {
  var bs = $("brandSel");
  bs.innerHTML = "";
  for (var i = 0; i < o.brands.length; i++) {
    var b = o.brands[i];
    var op = document.createElement("option");
    op.value = b.id; op.textContent = b.name;
    if (b.id === o.brand) op.selected = true;
    bs.appendChild(op);
  }
  var ns = $("nodeSel");
  ns.innerHTML = "";
  for (var j = 0; j < o.nodes.length; j++) {
    var n = o.nodes[j];
    var op2 = document.createElement("option");
    op2.value = n.url; op2.textContent = n.label;
    if (n.url === o.node) op2.selected = true;
    ns.appendChild(op2);
  }
  ns.disabled = !o.nodes.length;
  $("btnSpeed").disabled = !!o.speed_disabled;
  $("btnSpeed").textContent = o.speed_label || "测速";
  $("nodeRow").style.display = o.custom_visible ? "none" : "flex";
  var cp = $("customPool");
  cp.style.display = o.custom_visible ? "block" : "none";
  if (o.custom_visible && document.activeElement !== cp) cp.value = o.custom_value || "";
  var h = $("poolHint");
  h.textContent = o.hint || ""; h.className = "hint " + (o.hint_kind || "");
}

function renderKerRows(rows) {
  var html = "";
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    html += '<div class="ker' + (r.sel ? " sel" : "") + (r.ok ? " ok" : "") +
      '" data-k="' + esc(r.key) + '"><div class="ind"></div><div class="inner">' +
      '<div class="r1"><span class="dot">●</span><span class="nm">' + esc(r.label) +
      '</span><span class="st">' + esc(r.st) + '</span><span style="flex:1"></span>' +
      '<button class="btn ' + esc(r.btn_kind) + ' sm" data-act="' + esc(r.key) + '">' +
      esc(r.btn_text) + "</button></div>" +
      '<div class="note">' + esc(r.note) + "</div>" +
      '<div class="info">' + esc(r.info) + "</div></div></div>";
  }
  $("kerRows").innerHTML = html;
  var ks = $("kerRows").children;
  for (var j = 0; j < ks.length; j++) {
    (function (row) {
      var key = row.dataset.k;
      row.onclick = function (e) {
        if (e.target.dataset.act) return;
        post("select_kernel", { key: key });
      };
      var btn = row.querySelector("[data-act]");
      btn.onclick = function (e) {
        e.stopPropagation();
        post("kernel_action", { key: key, force: false });
      };
    })(ks[j]);
  }
}

/* ---------------- 表单绑定 ---------------- */
function bindStatic() {
  var nav = $("nav");
  for (var k in PAGE_META) {
    var d = document.createElement("div");
    d.className = "nav-item" + (k === "mine" ? " on" : "");
    d.dataset.k = k; d.textContent = PAGE_META[k][0];
    d.onclick = function () { showPage(this.dataset.k); };
    nav.appendChild(d);
  }
  showPage(S.page);

  $("startBtn").onclick = function () { post("toggle_mining"); };
  $("btnSave").onclick = function () { post("save"); };
  $("btnClearLog").onclick = function () { post("clear_log"); };
  $("btnSpeed").onclick = function () { post("speedtest"); };
  $("btnLatest").onclick = function () { post("refresh_latest"); };
  $("btnDash").onclick = function () { post("open_dashboard"); };
  $("btnOpenDir").onclick = function () { post("open_kernel_dir"); };
  $("btnKerPage").onclick = function () { post("open_download"); };
  /* 账号区按钮是 renderAcct() 动态生成的，事件在那里绑定，
     这里不能直接取元素（首屏还没渲染，取到 null 会抛错）。 */
  $("btnKillConflict").onclick = function () {
    if (window.confirm($("conflictTxt").textContent + "\n\n确认关闭这些程序？"))
      post("conflict_kill");
  };
  $("btnIgnoreConflict").onclick = function () { post("conflict_ignore"); };

  $("brandSel").onchange = function () { post("set_brand", { bid: this.value }); };
  $("nodeSel").onchange = function () { post("pick_node", { url: this.value }); };

  var debounce = {};
  function bindField(id, key) {
    var el = $(id);
    if (!el) return;
    function send() {
      S.fields[key] = el.value;
      post("set_field", { key: key, val: el.value });
    }
    el.addEventListener("input", function () {
      clearTimeout(debounce[key]);
      debounce[key] = setTimeout(send, 350);
    });
    el.addEventListener("change", send);
  }
  bindField("wallet", "wallet");
  bindField("games", "games");
  bindField("worker", "worker");
  bindField("kernel_dir", "kernel_dir");
  bindField("customPool", "pool_url");
  bindField("display_name", "display_name");
  /* D7：中控地址 / 接入口令 / 机器标识 —— 只读，禁止写入。
     这三项决定矿机连到哪台中控、以什么身份上报，改了就能被指向假中控或冒充他人机器。 */
  ["server", "token", "rig_name"].forEach(function (id) {
    var el = $(id);
    if (!el) return;
    el.setAttribute("readonly", "readonly");
    el.addEventListener("beforeinput", function (e) { e.preventDefault(); });
    el.addEventListener("keydown", function (e) {
      if (e.key !== "Tab") e.preventDefault();
    });
    el.addEventListener("paste", function (e) { e.preventDefault(); });
    el.addEventListener("drop", function (e) { e.preventDefault(); });
  });

  var tgs = document.querySelectorAll(".tg[data-tg]");
  for (var i = 0; i < tgs.length; i++) {
    tgs[i].addEventListener("click", function () {
      var k = this.dataset.tg;
      var v = !S.toggles[k];
      S.toggles[k] = v;
      this.classList.toggle("on", v);
      post("set_toggle", { key: k, val: v });
    });
  }
}

function renderToggles() {
  var tgs = document.querySelectorAll(".tg[data-tg]");
  for (var i = 0; i < tgs.length; i++)
    tgs[i].classList.toggle("on", !!S.toggles[tgs[i].dataset.tg]);
}

/* ---------------- 中控账号区 ---------------- */
function renderAcct() {
  var box = $("acctBox");
  var st = $("acctState");
  if (!box) return;
  var a = S.acct || {};
  if (a.logged) {
    st.textContent = "已登录 · " + (a.username || "") + (a.is_admin ? "（管理员）" : "");
    box.innerHTML =
      '<div class="kv"><span class="k">当前账号</span><span class="v mono">' +
      esc(a.username || "") + '</span></div>' +
      '<div class="kv"><span class="k">本机归属</span><span class="v">' +
      (a.bound ? "已绑定到该账号" : "未绑定") + '</span></div>' +
      '<div style="display:flex;gap:10px;margin-top:14px">' +
      '<button class="btn ghost sm" id="btnAcctLogout">退出登录</button>' +
      (a.bound ? '<button class="btn ghost sm" id="btnAcctUnbind">解绑本机</button>' : '') +
      '</div>' +
      '<div class="hint" style="margin-top:10px">' +
      (a.bound
        ? '本机已绑定到该账号。归属只能由矿机端登录产生：换账号请先解绑，再用新账号登录。'
        : '本机尚未绑定。直接用目标账号登录即可自动绑定；解绑后本机回到未绑定状态。') +
      '</div>';
    var lo = $("btnAcctLogout");
    if (lo) lo.onclick = function () { post("user_logout"); };
    var ub = $("btnAcctUnbind");
    if (ub) ub.onclick = function () {
      if (!window.confirm("解绑后本机将不再归属当前账号，\n回到未绑定状态，可用其他账号登录绑定。\n\n确认解绑？"))
        return;
      post("user_unbind");
    };
  } else if (S.acctPwdRequired) {
    // 首次登录强制改密：只显示改密表单，登录/注册入口暂时隐藏
    st.textContent = "首次登录 · 请设置新密码";
    box.innerHTML =
      '<div class="kv"><span class="k">账号</span><span class="v mono">' +
      esc(S.acctPwdRequired.username || "") + '</span></div>' +
      '<div class="flabel" style="margin-top:12px">当前（初始）密码</div>' +
      '<input class="field" id="pwdOld" type="password" autocomplete="current-password">' +
      '<div class="flabel">新密码（至少 6 位）</div>' +
      '<input class="field" id="pwdNew" type="password" autocomplete="new-password">' +
      '<div class="flabel">确认新密码</div>' +
      '<input class="field" id="pwdNew2" type="password" autocomplete="new-password">' +
      '<div style="display:flex;gap:10px;margin-top:14px">' +
      '<button class="btn pri sm" id="btnPwdSave">设置新密码</button>' +
      '</div>' +
      '<div class="hint" style="margin-top:10px">' +
      '这是首次登录，请设置你的新密码。设置完成后会自动登录并绑定本机。' +
      '</div>';
    var ps = $("btnPwdSave");
    if (ps) ps.onclick = function () {
      var o = $("pwdOld").value, n1 = $("pwdNew").value, n2 = $("pwdNew2").value;
      if (!o) { toast("请输入当前密码", "warn"); return; }
      if (!n1) { toast("请输入新密码", "warn"); return; }
      if (n1.length < 6) { toast("新密码至少 6 位", "warn"); return; }
      if (n1 !== n2) { toast("两次输入的新密码不一致", "warn"); return; }
      post("user_change_password", { old_password: o, new_password: n1 });
    };
  } else {
    st.textContent = "未登录";
    box.innerHTML =
      '<div class="flabel" style="margin-top:0">用户名</div>' +
      '<input class="field" id="acctUser" spellcheck="false" placeholder="中控账号">' +
      '<div class="flabel">密码</div>' +
      '<input class="field" id="acctPwd" type="password" spellcheck="false">' +
      '<div style="display:flex;gap:10px;margin-top:14px">' +
      '<button class="btn pri sm" id="btnAcctLogin">登录</button>' +
      '<button class="btn ghost sm" id="btnAcctRegister">注册</button>' +
      '</div>' +
      '<div class="hint" style="margin-top:10px">' +
      '登录后本机自动绑定到该账号名下；中控看板上只会出现你名下的矿机。' +
      '</div>';
    $("btnAcctLogin").onclick = function () {
      post("user_login", { username: $("acctUser").value.trim(), password: $("acctPwd").value });
    };
    $("btnAcctRegister").onclick = function () {
      post("user_register", { username: $("acctUser").value.trim(), password: $("acctPwd").value });
    };
  }
}

function renderFields() {
  for (var k in S.fields) {
    var el = $(k === "pool_url" ? "customPool" : k);
    if (el && document.activeElement !== el) el.value = S.fields[k];
  }
}

/* ---------------- 启动 / 轮询 ---------------- */
function applyBoot(d) {
  S.pollVer = d.poll_base || 0;
  S.fields = d.fields; S.toggles = d.toggles;
  if (d.acct) {
    S.acct = d.acct;
    if (d.acct.pwd_required) S.acctPwdRequired = d.acct.pwd_required;
    else if (d.acct.logged) S.acctPwdRequired = null;
  }
  $("verTag").textContent = "v" + d.version + " · PRL pearlhash";
  $("presetGames").innerHTML = (d.preset_games || []).map(function (n) {
    return '<span class="pchip">' + esc(n) + "</span>";
  }).join("");
  renderFields(); renderToggles(); renderAcct();
  apply({ t: "runbtn", on: d.running });
  if (d.pill) apply({ t: "pill", text: d.pill.text, kind: d.pill.kind });
  // 实时指标（算力/份额/温度/日收益/运行时长）：快照直接带上，
  // 否则关窗重开后要等引擎下一次 stats 推送（10 秒）才有数字
  if (d.metrics) apply(d.metrics);
  if (d.kchips) renderKchips(d.kchips);
  if (d.pool) renderPool(d.pool);
  if (d.kernel_rows) renderKerRows(d.kernel_rows);
  if (d.income) apply(d.income);
  if (d.rig) apply({ t: "rig", wallet: d.rig.wallet });
  if (d.conflict) apply({ t: "conflict", on: d.conflict.on, text: d.conflict.text });
  (d.logs || []).forEach(function (o) {
    S.logs.push(o); appendLogLine(o);
  });
  showPage(d.page || "mine");
}

function pollLoop() {
  fetch(APIBASE + "/api/poll?since=" + S.pollVer)
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.reset) {                          // 掉线太久 → 全量重拉
        fetch(APIBASE + "/api/boot").then(function (r) { return r.json(); })
          .then(applyBoot).catch(function () {});
      } else {
        S.pollVer = d.ver;
        for (var i = 0; i < d.items.length; i++) apply(d.items[i]);
      }
    })
    .catch(function () { /* 本地服务重启中，下轮再试 */ })
    ["finally"](function () { setTimeout(pollLoop, 400); });
}

function boot() {
  bindStatic();
  fetch(APIBASE + "/api/boot")
    .then(function (r) { return r.json(); })
    .then(function (d) { applyBoot(d); pollLoop(); })
    .catch(function (e) {
      $("verTag").textContent = "界面服务未连接";
      toast("无法连接本机界面服务：" + e, "err");
    });
}

window.onerror = function (m, s, l) {
  post("error", { msg: m + " @" + l });
  return false;
};

if (document.readyState === "loading")
  document.addEventListener("DOMContentLoaded", boot);
else boot();
