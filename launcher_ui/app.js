/* QAI 外壳：状态 + 启停 + 内嵌 8081 管理台 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
// 写接口令牌：由服务端注入到 <meta name="qai-token">，只有同源脚本读得到。
// 任何跨站页面都拿不到它，也就无法调用 POST 接口。
const API_TOKEN = (document.querySelector('meta[name="qai-token"]') || {}).content || "";

function authHeaders(extra) {
  const headers = Object.assign({}, extra || {});
  if (API_TOKEN) headers["X-QAI-Token"] = API_TOKEN;
  return headers;
}
const S = { status: null, busy: false, logOpen: false, offline: false, view: "panel", tokenInfo: null, tokenRevealed: false, setup: null, providerKeys: {}, update: null, lastFailureAt: null };

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

let toastTimer = null;
function toast(msg, type = "ok") {
  const el = $("#toast");
  if (!el) return;
  el.textContent = String(msg || "");
  el.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3600);
}

function qaiConfirm(message, options = {}) {
  const modal = $("#qaiConfirmModal");
  if (!modal) return Promise.resolve(false);
  return new Promise((resolve) => {
    const ok = $("#qaiConfirmOk");
    const cancel = $("#qaiConfirmCancel");
    $("#qaiConfirmTitle").textContent = options.title || "请确认";
    $("#qaiConfirmText").textContent = String(message || "");
    ok.textContent = options.okText || "确定";
    ok.className = `btn ${options.danger ? "btn-danger" : "btn-primary"}`;
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      modal.classList.add("hidden");
      document.removeEventListener("keydown", onKey);
      ok.onclick = null;
      cancel.onclick = null;
      modal.onclick = null;
      resolve(value);
    };
    const onKey = (e) => {
      if (e.key === "Escape") done(false);
      if (e.key === "Enter") done(true);
    };
    ok.onclick = () => done(true);
    cancel.onclick = () => done(false);
    modal.onclick = (e) => { if (e.target === modal) done(false); };
    document.addEventListener("keydown", onKey);
    modal.classList.remove("hidden");
    setTimeout(() => ok.focus(), 20);
  });
}

async function api(path, method = "GET", body = undefined) {
  const opts = { method, cache: "no-store", headers: authHeaders() };
  if (body !== undefined) {
    opts.headers = authHeaders({ "Content-Type": "application/json" });
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!r.ok) throw new Error((data && (data.error || data.message)) || `HTTP ${r.status}`);
  if (data && data.ok === false) throw new Error(data.error || data.message || "操作失败");
  return data;
}

function setChip(el, state, text) {
  if (!el) return;
  el.classList.remove("ok", "warn", "bad");
  if (state) el.classList.add(state);
  const b = el.querySelector("b");
  if (b) b.textContent = text;
}

function setView(view) {
  S.view = view === "napcat" ? "napcat" : "panel";
  $$(".view-tab").forEach((b) => b.classList.toggle("active", b.dataset.view === S.view));
  try { history.replaceState(null, "", S.view === "napcat" ? "#napcat" : "#panel"); } catch (e) {}
  renderStage();
  const stage = document.querySelector(".stage");
  if (stage) {
    stage.classList.remove("view-anim");
    void stage.offsetWidth;
    stage.classList.add("view-anim");
    setTimeout(() => stage.classList.remove("view-anim"), 520);
  }
}

function applyStatus(st) {
  S.status = st;
  const bot = !!st.bot_running;
  const panel = !!st.panel_up;
  const nap = !!st.napcat_up;
  const ws = !!st.ws_connected;

  setChip($("#chipBot"), bot ? "ok" : "warn", bot ? "运行中" : "未启动");
  setChip($("#chipPanel"), panel ? "ok" : "warn", panel ? "已就绪" : "未就绪");
  setChip($("#chipNapcat"), nap ? "ok" : "warn", nap ? "已启动" : "未启动");
  setChip($("#chipWs"), ws ? "ok" : "warn", ws ? "已连接" : (nap ? "等待连接" : "等待 NapCat"));

  $("#startBtn").disabled = S.busy || bot;
  $("#stopBtn").disabled = S.busy || !bot;
  $("#restartBtn").disabled = S.busy || !bot;
  $("#napcatBtn").textContent = nap ? "停止 NapCat" : "启动 NapCat";
  $("#napcatBtn").disabled = S.busy;
  $("#placeholderStart").disabled = S.busy || bot;
  $("#placeholderNapcat").disabled = S.busy || nap;

  const target = S.view === "napcat"
    ? (st.napcat_url || "")
    : (window.QAITheme ? QAITheme.withTheme(st.panel_url) : (st.panel_url || ""));
  if (st.log) $("#logMeta").textContent = `launcher-bot.log · ${st.time || ""}`;

  // 看门狗放弃自动重启：只在第一次看到时弹一次，避免每次轮询都刷屏
  const fail = st.bot_last_failure || null;
  if (fail && fail.at && S.lastFailureAt !== fail.at) {
    S.lastFailureAt = fail.at;
    const detail = fail.log ? `\n\n最后几行日志：\n${fail.log}` : "";
    toast(`机器人反复启动失败（退出码 ${fail.code ?? "未知"}），已停止自动重启。${fail.reason || ""}${detail}`, "error");
    toggleLog(true);
  }
  if (!fail) S.lastFailureAt = null;

  renderStage();
}

function sameFrameTarget(a, b) {
  if (!a || !b) return false;
  try {
    const ua = new URL(a, location.origin);
    const ub = new URL(b, location.origin);
    ua.searchParams.delete("theme");
    ub.searchParams.delete("theme");
    return ua.href === ub.href;
  } catch (e) {
    return false;
  }
}

function renderStage() {
  const st = S.status;
  if (!st) return;
  const frame = $("#panelFrame");
  const placeholder = $("#placeholder");
  const isNap = S.view === "napcat";
  const up = isNap ? !!st.napcat_up : !!st.panel_up;
  const rawTarget = isNap ? (st.napcat_url || "") : (st.panel_url || "");
  const target = (!isNap && window.QAITheme) ? QAITheme.withTheme(rawTarget) : rawTarget;

  if (up && target) {
    placeholder.classList.add("hidden");
    frame.classList.remove("hidden");
    if (frame.dataset.loaded !== target) {
      frame.dataset.loaded = target;
      // Theme changes only update the query string; never reload the iframe.
      if (!sameFrameTarget(frame.src, target)) frame.src = target;
    }
  } else {
    placeholder.classList.remove("hidden");
    frame.classList.add("hidden");
    if (frame.dataset.loaded) {
      frame.dataset.loaded = "";
      frame.src = "about:blank";
    }
    const startBtn = $("#placeholderStart");
    const napBtn = $("#placeholderNapcat");
    if (isNap) {
      startBtn.classList.add("hidden");
      napBtn.classList.remove("hidden");
      napBtn.textContent = "启动 NapCat";
      napBtn.disabled = S.busy || !!st.napcat_up;
      placeholder.classList.add("napcat");
      $("#placeholderTitle").textContent = st.napcat_up ? "NapCat WebUI 准备中…" : "NapCat 还没启动";
      $("#placeholderText").textContent = st.napcat_up
        ? "端口已经起来，WebUI 正在加载；稍等几秒会自动出现。"
        : "点「启动 NapCat」会请求管理员权限（UAC），确认后登录 QQ；扫码窗口会直接显示在这个页面里。";
      $("#placeholderHint").textContent = "NapCat 寄生在 QQ 客户端里，必须保持 QQ 登录；停止 NapCat 会结束托管它的 QQ 进程。Token 已自动读取，可点顶部「Token」查看/复制。";
    } else {
      startBtn.classList.remove("hidden");
      napBtn.classList.remove("hidden");
      napBtn.textContent = "启动 NapCat";
      napBtn.disabled = S.busy || !!st.napcat_up;
      napBtn.classList.toggle("hidden", !!st.napcat_up);
      placeholder.classList.remove("napcat");
      $("#placeholderTitle").textContent = st.bot_running ? "QAI 正在启动…" : "QAI 还没启动";
      $("#placeholderText").textContent = st.bot_running
        ? "进程已起来，控制台正在就绪；稍等几秒会自动出现。"
        : "点「启动机器人」会拉起 bot.py；启动后这里会嵌入完整的管理控制台。";
      $("#placeholderHint").textContent = "NapCat 需要管理员权限并登录 QQ；连接成功后右上角「连接」会变绿。";
    }
  }
}

async function napcatToggle() {
  const up = !!S.status?.napcat_up;
  if (up && !await qaiConfirm("停止 NapCat 会结束托管它的 QQ 进程（必要时按 QQ.exe 结束），并需要管理员权限（UAC）。", { title: "停止 NapCat", okText: "停止", danger: true })) return;
  await action($("#napcatBtn"), up ? "/api/napcat/stop" : "/api/napcat/start");
}

async function poll() {
  try {
    const st = await api("/api/status");
    S.offline = false;
    applyStatus(st);
  } catch (e) {
    if (!S.offline) {
      S.offline = true;
      toast("启动器失联：" + e.message, "error");
    }
    setChip($("#chipBot"), "bad", "失联");
  }
}

async function action(btn, path) {
  if (S.busy) return;
  S.busy = true;
  const old = btn.innerHTML;
  btn.innerHTML = '<span class="spinner"></span>';
  btn.disabled = true;
  try {
    const r = await api(path, "POST");
    toast(r.message || (r.ok ? "操作完成" : "操作失败"), r.ok ? "ok" : "error");
    // 启动失败时把机器人日志直接摆出来，用户不用自己去翻文件
    if (!r.ok && r.log) toggleLog(true);
    if (!r.ok && r.error && String(r.error).includes("最后几行日志")) {
      $("#logText").textContent = r.error;
    }
    await poll();
  } catch (e) {
    toast(e.message, "error");
  } finally {
    S.busy = false;
    btn.innerHTML = old;
    if (S.status) applyStatus(S.status);
  }
}

async function withBusy(btn, fn) {
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

async function loadLog() {
  try {
    const r = await api("/api/log?n=180");
    $("#logText").textContent = r.log || "（空）";
    $("#logMeta").textContent = `launcher-bot.log · ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  } catch (e) {
    $("#logText").textContent = "读取日志失败：" + e.message;
  }
}

function toggleLog(force) {
  S.logOpen = force === undefined ? !S.logOpen : !!force;
  $("#logDrawer").classList.toggle("hidden", !S.logOpen);
  if (S.logOpen) loadLog();
}

async function openToken() {
  try {
    S.tokenInfo = await api("/api/napcat/token");
    S.tokenRevealed = false;
    renderToken();
    $("#tokenModal").classList.remove("hidden");
  } catch (e) {
    toast("读取 token 失败：" + e.message, "error");
  }
}

function renderToken() {
  const info = S.tokenInfo;
  if (!info) return;
  const has = !!info.has_token;
  $("#tokenDisplay").textContent = has
    ? (S.tokenRevealed ? info.token : info.masked)
    : "未读取到 token";
  $("#toggleToken").textContent = S.tokenRevealed ? "隐藏" : "显示";
  $("#toggleToken").disabled = !has;
  $("#copyToken").disabled = !has;
  $("#tokenMeta").textContent = has
    ? `来源：${info.path} · 仅用于访问 NapCat WebUI，不是 QQ 密码`
    : `没有读到 token。请检查 ${info.path}，或确认 NapCat WebUI 已启用。`;
  const open = $("#tokenOpen");
  open.href = info.webui_url || "#";
  open.classList.toggle("hidden", !has);
}

function closeToken() {
  $("#tokenModal").classList.add("hidden");
}

async function copyToken() {
  const token = S.tokenInfo && S.tokenInfo.token;
  if (!token) return;
  try {
    await navigator.clipboard.writeText(token);
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = token;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  toast("NapCat WebUI Token 已复制");
}

const MODEL_PROVIDERS = (window.QAI_MODELS && window.QAI_MODELS.providers) || [];
const CAP_DEFS = [
  ["image", "🖼", "识图"],
  ["video", "🎬", "视频"],
  ["web", "🌐", "原生联网"],
  ["tools", "🛠", "工具"],
  ["reasoning", "🧠", "推理"],
  ["free", "🆓", "免费"],
  ["local", "🏠", "本地"],
];
let setupNapcatPoll = 0;

function providerById(id) {
  return MODEL_PROVIDERS.find((p) => p.id === id) || MODEL_PROVIDERS[0] || null;
}

function inferProvider(st) {
  if (st.llm_provider) {
    const p = MODEL_PROVIDERS.find((x) => x.id === st.llm_provider);
    if (p) return p;
  }
  const base = String(st.llm_base_url || "").toLowerCase();
  const direct = MODEL_PROVIDERS.find((p) =>
    (p.base && base.startsWith(p.base.toLowerCase().replace(/\/$/, "")))
    || (p.models || []).some((m) => m.base && base.startsWith(m.base.toLowerCase().replace(/\/$/, "")))
  );
  if (direct) return direct;
  if (base.includes("deepseek")) return providerById("deepseek");
  if (base.includes("dashscope")) return providerById("qwen");
  if (base.includes("moonshot")) return providerById("kimi");
  if (base.includes("bigmodel")) return providerById("zhipu");
  if (base.includes("tokenhub")) return providerById("hunyuan");
  return MODEL_PROVIDERS[0];
}

function fillProviderSelect(providerId) {
  const sel = $("#setupProvider");
  sel.innerHTML = MODEL_PROVIDERS.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
  sel.value = providerId || (MODEL_PROVIDERS[0] && MODEL_PROVIDERS[0].id) || "zhipu";
}

function currentCapabilityModel() {
  const provider = providerById($("#setupProvider").value);
  if (!provider) return null;
  const modelId = $("#setupModel").value;
  return (provider.models || []).find((m) => m.id === modelId) || null;
}

function renderCaps(model) {
  const box = $("#setupCaps");
  if (!box) return;
  if (!model) {
    box.innerHTML = '<span class="cap-chip off">自定义模型 · 能力未知</span>';
    return;
  }
  box.innerHTML = CAP_DEFS.map(([key, icon, label]) => {
    const on = !!model[key];
    return `<span class="cap-chip ${on ? "on" : "off"}">${icon} ${label}</span>`;
  }).join("") + (model.note ? `<span class="hint">${esc(model.note)}</span>` : "");
}

function updateKeyPlaceholder(provider, providerChanged) {
  if (!provider) return;
  const saved = !!(S.providerKeys && S.providerKeys[provider.id]);
  $("#setupKey").placeholder = saved
    ? "该厂商已保存 Key，留空沿用"
    : "请输入该厂商 API Key";
  if (providerChanged) $("#setupKey").value = "";
}

function refreshModelUi(selectedModel, providerChanged) {
  const provider = providerById($("#setupProvider").value);
  if (!provider) return;
  const sel = $("#setupModel");
  const models = provider.models || [];
  sel.innerHTML = models.map((m) => `<option value="${m.id}">${m.name}</option>`).join("");
  const pick = models.find((m) => m.id === selectedModel) || models[0];
  if (pick) sel.value = pick.id;
  const custom = $("#setupModelCustom");
  const wrap = $("#setupModelCustomWrap");
  if (sel.value === "__custom__") {
    wrap.classList.remove("hidden");
    if (selectedModel && selectedModel !== "__custom__") custom.value = selectedModel;
  } else {
    wrap.classList.add("hidden");
  }
  const modelBase = pick && pick.base ? pick.base : provider.base;
  if (modelBase) $("#setupBase").value = modelBase;
  $("#setupProviderHint").textContent = provider.hint || "";
  $("#setupModelDesc").textContent = pick ? (pick.desc || "") : "自定义模型";
  updateKeyPlaceholder(provider, !!providerChanged);
  renderCaps(sel.value === "__custom__" ? null : pick);
}

async function openSetup() {
  try {
    const st = await api("/api/setup/status");
    S.setup = st;
    S.providerKeys = st.provider_keys || {};
    $("#setupKey").value = "";
    $("#setupKey").placeholder = st.llm_key_set ? "已配置，留空保持不变" : "sk-...（必填）";
    const provider = inferProvider(st);
    fillProviderSelect(provider ? provider.id : "zhipu");
    refreshModelUi(st.llm_model);
    updateKeyPlaceholder(providerById($("#setupProvider").value), false);
    if (st.llm_model && $("#setupModel").value === "__custom__") {
      $("#setupModelCustom").value = st.llm_model;
    }
    if (st.llm_base_url) $("#setupBase").value = st.llm_base_url;
    $("#setupNapdir").value = st.napcat_dir || "";
    $("#setupAdmin").value = st.admin_qq || "";
    $("#setupGroups").value = st.allowed_groups || "";
    $("#setupCloseBehavior").value = ["background", "exit"].includes(st.close_behavior) ? st.close_behavior : "ask";
    $("#setupDetectResult").textContent = st.napcat_ok
      ? `已检测到 NapCat：${st.napcat_dir}`
      : `还没检测到有效的 NapCat。可点「安装到 QAI 目录」（默认 ${st.napcat_default_dir || "QAI\\napcat"}）或手动选择。`;
    $("#setupSaveHint").textContent = st.llm_key_set
      ? "API Key 已配置；留空会保持不变。NapCat 目录可以之后再填。"
      : "模型 Key 必填。NapCat 还没装也可以先保存，只是机器人暂时不会启动。";
    $("#setupNapcatBar").style.width = "0%";
    $("#setupNapcatState").textContent = "从 GitHub 下载最新 Windows Node 包，约 120MB。点「一键连接」前先手动关掉 NapCat 的命令行窗口，否则配置会被覆盖。";
    $("#setupModal").classList.remove("hidden");
    if (!st.napcat_ok) detectNapcat();
    checkNapcatInstall();
  } catch (e) {
    toast("读取配置状态失败：" + e.message, "error");
  }
}

function closeSetup() {
  clearTimeout(setupNapcatPoll);
  $("#setupModal").classList.add("hidden");
}

async function startNapcatInstall() {
  if (!await qaiConfirm("将从 GitHub 下载最新 NapCat 到 QAI\\napcat（约 100MB）。", { title: "安装 NapCat", okText: "继续" })) return;
  const btn = $("#setupNapcatInstall");
  btn.disabled = true;
  $("#setupNapcatBar").style.width = "4%";
  $("#setupNapcatState").textContent = "正在启动安装…";
  try {
    const r = await api("/api/napcat/install", "POST");
    toast(r.message || "开始安装");
    pollNapcatInstall();
  } catch (e) {
    btn.disabled = false;
    $("#setupNapcatState").textContent = "安装失败：" + e.message;
  }
}

async function openNapcatPan() {
  try {
    const r = await api("/api/napcat/pan");
    toast(r.url ? "已在浏览器打开夸克网盘" : "已打开夸克网盘");
  } catch (e) {
    toast("打开网盘失败：" + e.message, "error");
  }
}

async function quickConnectOnebot() {  if (!await qaiConfirm(
    "会自动检查 NapCat 的 OneBot 配置，并写入 QAI 的反向 WebSocket 地址和 Token。\n\n"
    + "⚠️ 请先手动关闭 NapCat 的命令行窗口（黑窗口），等 QQ 完全退出，再点「开始检查」。\n"
    + "命令行窗口还开着时，NapCat 退出会把刚写好的配置覆盖回去，这一步没做就会一直连不上。\n\n"
    + "完成后 QAI 会重新拉起 NapCat，可能需要重新扫码登录。",
    { title: "一键连接 OneBot", okText: "已关闭，开始检查" },
  )) return;
  const btn = $("#setupNapcatQuick");
  btn.disabled = true;
  $("#setupNapcatState").textContent = "正在检查 NapCat 配置和 8080 端口…";
  try {
    const r = await api("/api/napcat/quick-connect", "POST");
    if (!r.ok) throw new Error(r.error || "连接配置失败");
    let msg = "已写入 " + ((r.configs && r.configs.length) || 1) + " 个 NapCat 账号配置";
    if (r.token_changed) msg += "；已生成新的 OneBot Token 并重启机器人";
    if (!r.napcat_running) msg += "；已请求重启 NapCat，请确认 UAC 并按提示登录 QQ";
    $("#setupNapcatState").textContent = msg;
    toast(r.token_changed ? "配置已写入，Bot Token 已更新" : "配置已写入");
    setTimeout(poll, 1200);
  } catch (e) {
    const raw = String(e.message || "");
    const tip = /404|not found|未找到/i.test(raw)
      ? "当前启动器还是旧版本，请用「停止QAI.bat」完全停止后，从新版本文件夹重新打开 QAI.exe"
      : raw;
    $("#setupNapcatState").textContent = "一键连接失败：" + tip;
    toast(tip, "error");
  } finally {
    btn.disabled = false;
  }
}

async function pollNapcatInstall() {
  clearTimeout(setupNapcatPoll);
  try {
    const st = await api("/api/napcat/install/status");
    const running = st.status === "downloading" || st.status === "extracting";
    $("#setupNapcatBar").style.width = `${st.progress || 0}%`;
    $("#setupNapcatState").textContent = st.message || "正在安装…";
    $("#setupNapcatInstall").disabled = running;
    if (st.status === "done") {
      if (st.path) {
        $("#setupNapdir").value = st.path;
        $("#setupDetectResult").textContent = `NapCat 已安装：${st.path}`;
      }
      toast("NapCat 安装完成");
      return;
    }
    if (st.status === "error") {
      $("#setupNapcatInstall").disabled = false;
      toast(st.message || "NapCat 安装失败", "error");
      return;
    }
    setupNapcatPoll = setTimeout(pollNapcatInstall, 900);
  } catch (e) {
    $("#setupNapcatInstall").disabled = false;
  }
}

async function checkNapcatInstall() {
  try {
    const st = await api("/api/napcat/install/status");
    if (st.status === "downloading" || st.status === "extracting") pollNapcatInstall();
  } catch (e) {}
}

async function detectNapcat() {
  const btn = $("#setupDetect");
  const out = $("#setupDetectResult");
  try {
    await withBusy(btn, async () => {
      out.textContent = "正在查找 NapCat…（会扫一遍下载、桌面、各盘根目录，最多几秒）";
      const d = await api("/api/setup/detect");
      const list = d.napcat || [];
      const usable = list.filter((x) => x.launcher);
      if (usable.length) {
        // 有能直接启动的，直接填进去；多个时优先取第一个（路径最短的）
        $("#setupNapdir").value = usable[0].path;
        const extra = usable.length > 1 ? `（共找到 ${usable.length} 个，已填第一个）` : "";
        out.textContent = `✓ 已找到并填入：${usable[0].path}${extra}。点「保存」生效。`;
        return;
      }
      if (list.length) {
        // 有 NapCat 主体但缺 launcher-win10.bat：典型是解压层级不对
        $("#setupNapdir").value = list[0].path;
        out.textContent =
          `找到了 ${list[0].path}，但里面没有 launcher-win10.bat（可能是解压多套了一层）。` +
          "请把压缩包解压后，选到「包含 launcher-win10.bat」的那一层；或点「安装到 QAI 目录」让 QAI 自己装。";
        return;
      }
      out.textContent =
        "没有在常见位置找到 NapCat。可以：①点「安装到 QAI 目录」让 QAI 自动下载；" +
        "②把 NapCat 解压到 QAI 目录下再点一次「自动检测」；③手动选到包含 launcher-win10.bat 的那一层。";
    });
  } catch (e) {
    out.textContent = "自动检测失败：" + e.message;
  }
}

async function saveSetup() {
  const provider = $("#setupProvider").value;
  let model = $("#setupModel").value || "";
  if (model === "__custom__") model = $("#setupModelCustom").value.trim();
  const key = $("#setupKey").value.trim();
  const keySaved = !!(S.providerKeys && S.providerKeys[provider])
    || (provider === S.setup?.llm_provider && !!S.setup?.llm_key_set);
  if (!key && !keySaved) {
    toast("请填写该厂商的 API Key", "error");
    return;
  }
  if (!model) {
    toast("请选择模型", "error");
    return;
  }
  const body = {
    llm_provider: provider,
    llm_api_key: key,
    llm_base_url: $("#setupBase").value.trim(),
    llm_model: model,
    napcat_dir: $("#setupNapdir").value.trim(),
    admin_qq: $("#setupAdmin").value.trim(),
    allowed_groups: $("#setupGroups").value.trim(),
    start: true,
  };
  try {
    await withBusy($("#setupSave"), async () => {
      const r = await api("/api/setup/save", "POST", body);
      toast(r.message || "配置已保存");
      // 后端会把「填错但能救回来」的路径解析成真正的 NapCat 目录，回填让用户看见
      if (r.napcat_dir && r.napcat_dir !== body.napcat_dir) {
        $("#setupNapdir").value = r.napcat_dir;
        toast(`NapCat 目录已自动修正为：${r.napcat_dir}`);
      }
      if (r.warning) {
        $("#setupSaveHint").textContent = r.warning;
        toast(r.warning);
      }
    });
    closeSetup();
    await poll();
  } catch (e) {
    toast(e.message, "error");
    $("#setupSaveHint").textContent = "保存失败：" + e.message;
  }
}

/* ---------- 版本握手：识别「旧启动器进程 + 新界面文件」 ---------- */
// 与 launcher.py 的 API_LEVEL 对应。新增接口时两边一起加。
const MIN_API = 2;

function showStaleBanner(detail) {
  const el = $("#staleBanner");
  if (!el) return;
  const text = $("#staleBannerText");
  if (text) {
    text.textContent = detail
      ? `控制台已更新，但正在运行的启动器还是旧版（接口层级 ${detail}），部分按钮会点了没反应。请双击项目目录里的「停止QAI.bat」，再重新打开 QAI。`
      : "控制台已更新，但正在运行的启动器还是旧版（没有版本接口），部分按钮会点了没反应。请双击项目目录里的「停止QAI.bat」，再重新打开 QAI。";
  }
  el.classList.remove("hidden");
}

async function checkApiVersion() {
  try {
    const v = await api("/api/version");
    const level = Number(v && v.api);
    if (!Number.isFinite(level) || level < MIN_API) showStaleBanner(level);
  } catch (e) {
    // 旧启动器没有 /api/version，正好就是要提醒的情况
    showStaleBanner(null);
  }
}

/* ---------- 在线更新 ---------- */
function renderUpdate(info) {
  S.update = info;
  $("#updateSub").textContent = `当前版本 ${info.current || "?"}` + (info.latest ? ` · 最新 ${info.latest}` : "");
  const state = $("#updateState");
  const notes = $("#updateNotes");
  const apply = $("#updateApply");

  if (!info.ok) {
    // 多行提示（含离线建议），textContent 会把换行吃掉，这里按行拼
    state.innerHTML = esc(info.error || "检查更新失败").replace(/\n/g, "<br>");
    notes.classList.add("hidden");
    apply.disabled = true;
    return;
  }
  if (info.has_update) {
    const size = info.asset_size ? `，${(info.asset_size / 1024 / 1024).toFixed(1)} MB` : "";
    state.innerHTML = `发现新版本 <b>${esc(info.latest)}</b>（当前 ${esc(info.current)}）${esc(size)}`
      + (info.asset_is_update_pack ? "，只更新程序文件" : "，会从完整包里只取程序文件");
    notes.textContent = info.notes || "（这个版本没有写更新说明）";
    notes.classList.remove("hidden");
    apply.disabled = false;
  } else {
    state.textContent = `已是最新版本（${info.current}）。`;
    notes.classList.add("hidden");
    apply.disabled = true;
  }
}

async function checkUpdate(manual) {
  try {
    renderUpdate(await api("/api/update/check"));
  } catch (e) {
    renderUpdate({ ok: false, current: "", error: "检查更新失败：" + e.message });
    if (manual) toast("检查更新失败：" + e.message, "error");
    return;
  }
  const info = S.update;
  const btn = $("#updateBtn");
  if (btn) {
    if (info && info.ok && info.has_update) {
      btn.textContent = `更新 ${info.latest}`;
      btn.classList.add("has-update");
    } else if (info && info.current) {
      btn.textContent = `v${info.current}`;
      btn.classList.remove("has-update");
    }
  }
  if (manual && !$("#updateModal").classList.contains("hidden")) return;
}

async function openUpdate() {
  $("#updateModal").classList.remove("hidden");
  $("#updateState").textContent = "正在检查…";
  $("#updateNotes").classList.add("hidden");
  $("#updateApply").disabled = true;
  await checkUpdate(true);
}

function closeUpdate() {
  $("#updateModal").classList.add("hidden");
}

async function applyUpdate() {
  const info = S.update || {};
  const ok = await qaiConfirm(
    `要更新到 ${info.latest || "新版本"} 吗？\n\n`
    + "只替换程序文件，聊天记录 / 记忆 / 人设 / .env 都会保留。\n"
    + "更新期间 QAI 会关闭并自动重开，别手动去开，等它自己起来即可。",
    { title: "立即更新", okText: "更新并重启" },
  );
  if (!ok) return;
  try {
    await withBusy($("#updateApply"), async () => {
      const r = await api("/api/update/apply", "POST", {});
      toast(r.message || "正在更新");
      $("#updateState").textContent = r.message || "正在更新，QAI 即将关闭…";
    });
  } catch (e) {
    toast("更新失败：" + e.message, "error");
    $("#updateState").textContent = "更新失败：" + e.message;
  }
}

async function updateFromLocalFile(btn) {
  const input = $("#updateLocalFile");
  const file = input && input.files && input.files[0];
  if (!file) return;
  const ok = await qaiConfirm(
    `用「${file.name}」更新吗？\n\n`
    + "只会替换程序文件，聊天记录 / 记忆 / 人设 / .env 里的 Key 都会保留。\n"
    + "更新期间 QAI 会关闭并自动重开，等它自己起来即可。",
    { title: "从本地文件更新", okText: "更新并重启" },
  );
  if (!ok) {
    input.value = "";
    return;
  }
  try {
    await withBusy(btn, async () => {
      const r = await fetch("/api/update/from-file", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/zip" }),
        body: file,
      });
      const data = await r.json().catch(() => null);
      if (!r.ok || !data || data.ok === false) {
        throw new Error((data && data.error) || `HTTP ${r.status}`);
      }
      toast(data.message || "正在更新");
      $("#updateState").textContent = data.message || "正在更新，QAI 即将关闭…";
    });
  } catch (e) {
    toast("更新失败：" + e.message, "error");
    $("#updateState").textContent = "本地更新失败：" + e.message;
  }
  input.value = "";
}

/* ---------- 数据备份 ---------- */
async function exportData(btn) {
  try {
    await withBusy(btn, async () => {
      const r = await fetch("/api/data/export", { cache: "no-store", headers: authHeaders() });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try { msg = (JSON.parse(await r.text()) || {}).error || msg; } catch (e) {}
        throw new Error(msg);
      }
      const blob = await r.blob();
      const match = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)"?/);
      const name = match ? match[1] : "QAI-backup.zip";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      $("#dataState").textContent = `已导出 ${name}（${(blob.size / 1024 / 1024).toFixed(2)} MB）。`;
      toast("数据已导出");
    });
  } catch (e) {
    toast("导出失败：" + e.message, "error");
  }
}

async function importData(btn) {
  const input = $("#dataImportFile");
  const file = input && input.files && input.files[0];
  if (!file) return;
  const ok = await qaiConfirm(
    `会用「${file.name}」覆盖当前 data/ 目录（聊天记录、长期记忆、好感度、人设绑定）。\n\n`
    + "当前数据会先备份成 data.bak-时间戳；导入前请先停止机器人，否则内存里的记忆会盖回去。",
    { title: "导入数据", okText: "覆盖导入", danger: true },
  );
  if (!ok) {
    input.value = "";
    return;
  }
  try {
    await withBusy(btn, async () => {
      const r = await fetch("/api/data/import", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/zip" }),
        body: file,
      });
      const data = await r.json().catch(() => null);
      if (!r.ok || !data || data.ok === false) {
        throw new Error((data && data.error) || `HTTP ${r.status}`);
      }
      $("#dataState").textContent = (data.message || "导入完成") + " 重新启动机器人后生效。";
      toast(data.message || "导入完成");
    });
  } catch (e) {
    toast("导入失败：" + e.message, "error");
  }
  input.value = "";
}

/* ---------- 关窗口询问：后台运行 还是 退出 ---------- */
function askClose() {
  const modal = $("#closeModal");
  if (!modal) return;
  modal.classList.remove("hidden");
}
// qai_window.py 会调这个：它拦下了关闭动作，让页面问用户
window.__qaiAskClose = askClose;

async function decideClose(mode, btn) {
  const remember = !!$("#closeRemember")?.checked;
  try {
    await withBusy(btn, () => api("/api/close/decision", "POST", { mode, remember }));
  } catch (e) {
    toast("操作失败：" + e.message, "error");
    return;
  }
  $("#closeModal")?.classList.add("hidden");
  if (mode === "background") toast("已转入后台运行，双击 QAI 可重新打开界面");
}

/* ---------- 用系统浏览器打开链接（教程里的网址） ---------- */
async function openExternal(url) {
  try {
    await api("/api/open-url", "POST", { url });
  } catch (e) {
    toast("打不开浏览器，请手动复制链接：" + url, "error");
  }
}

async function checkSetup() {
  try {
    const st = await api("/api/setup/status");
    S.setup = st;
    if (st.needs_setup && $("#tutorialModal").classList.contains("hidden")) await openSetup();
  } catch (e) {
    // 启动器 API 暂时不通时不打扰
  }
}

/* ---------- 各厂商 Key：角色单独选厂商时用，不动默认模型 ---------- */
async function openKeys(focusProvider) {
  const modal = $("#keysModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  await refreshKeys(focusProvider || "");
}

function closeKeys() {
  $("#keysModal")?.classList.add("hidden");
}

async function refreshKeys(focusProvider) {
  const list = $("#keysList");
  if (!list) return;
  list.innerHTML = '<div class="empty-state">正在读取…</div>';
  try {
    renderKeys(await api("/api/provider-keys"), focusProvider || "");
  } catch (e) {
    list.innerHTML = `<div class="empty-state">读取失败：${esc(e.message)}</div>`;
  }
}

function renderKeys(st, focusProvider) {
  const list = $("#keysList");
  if (!list) return;
  const items = (st && st.items) || [];
  if (!items.length) {
    list.innerHTML = '<div class="empty-state">没有可配置的厂商</div>';
    return;
  }
  list.innerHTML = items.map((item) => {
    const meta = MODEL_PROVIDERS.find((p) => p.id === item.id) || { name: item.id };
    const badge = item.set
      ? '<b class="key-badge on">已配置</b>'
      : '<b class="key-badge">未配置</b>';
    const masked = item.set && item.masked ? `<span class="muted">${esc(item.masked)}</span>` : "";
    const current = item.is_current ? '<span class="key-current">· 当前默认模型</span>' : "";
    const placeholder = item.set ? "已配置，输入新 Key 可覆盖" : "填入这个厂商的 API Key";
    const clearBtn = item.set
      ? `<button class="btn btn-ghost btn-sm" data-key-clear="${esc(item.id)}" type="button">清除</button>`
      : "";
    const testBtn = item.set
      ? `<button class="btn btn-ghost btn-sm" data-key-test="${esc(item.id)}" type="button">测试</button>`
      : "";
    return `<div class="key-row${item.id === focusProvider ? " focus" : ""}" data-key-row="${esc(item.id)}">
      <div class="key-head">
        <div><b>${esc(meta.name)}</b> ${current}</div>
        <div>${badge} ${masked}</div>
      </div>
      <div class="key-input">
        <input type="password" data-key-input="${esc(item.id)}" placeholder="${esc(placeholder)}" autocomplete="off">
        <button class="btn btn-primary btn-sm" data-key-save="${esc(item.id)}" type="button">保存</button>
        ${testBtn}
        ${clearBtn}
      </div>
      <div class="hint" data-key-result="${esc(item.id)}">写入 ${esc(item.env)}</div>
    </div>`;
  }).join("");

  list.querySelectorAll("[data-key-save]").forEach((btn) =>
    btn.addEventListener("click", () => saveKey(btn.dataset.keySave, btn)));
  list.querySelectorAll("[data-key-clear]").forEach((btn) =>
    btn.addEventListener("click", () => clearKey(btn.dataset.keyClear, btn)));
  list.querySelectorAll("[data-key-test]").forEach((btn) =>
    btn.addEventListener("click", () => testKey(btn.dataset.keyTest, btn)));
  list.querySelectorAll("[data-key-input]").forEach((input) => {
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const btn = list.querySelector(`[data-key-save="${input.dataset.keyInput}"]`);
      if (btn) btn.click();
    });
  });

  if (focusProvider) {
    const target = list.querySelector(`[data-key-input="${focusProvider}"]`);
    if (target) setTimeout(() => target.focus(), 60);
  }
}

async function saveKey(provider, btn) {
  const input = $(`[data-key-input="${provider}"]`);
  const key = input ? input.value.trim() : "";
  if (!key) {
    toast("请先填入这个厂商的 API Key", "error");
    return;
  }
  try {
    const r = await withBusy(btn, () => api("/api/provider-keys", "POST", { provider, key }));
    toast(r.message || "已保存");
    renderKeys(r, provider);
  } catch (e) {
    toast(e.message, "error");
  }
}

async function clearKey(provider, btn) {
  const meta = MODEL_PROVIDERS.find((p) => p.id === provider);
  const ok = await qaiConfirm(`清除「${meta ? meta.name : provider}」的 API Key？`, {
    title: "清除 Key", okText: "清除", danger: true,
  });
  if (!ok) return;
  try {
    const r = await withBusy(btn, () => api("/api/provider-keys", "POST", { provider, clear: true }));
    toast(r.message || "已清除");
    renderKeys(r, provider);
  } catch (e) {
    toast(e.message, "error");
  }
}

async function testKey(provider, btn) {
  const out = $(`[data-key-result="${provider}"]`);
  if (out) out.textContent = "正在向厂商发一次最小请求…";
  try {
    const r = await withBusy(btn, () => api("/api/provider-keys/test", "POST", { provider }));
    if (out) out.textContent = r.message || "可用";
    toast(r.message || "Key 可用");
  } catch (e) {
    if (out) out.textContent = "测试失败：" + e.message;
    toast("测试失败：" + e.message, "error");
  }
}

function renderTutorialMarkdown(md) {
  // 教程里的网址要能点：markdown 链接 [文字](地址) 和裸露的网址都转成
  // data-href 链接，点击时交给系统浏览器打开（WebView 里直接跳会丢窗口）。
  const link = (text, url) =>
    `<a href="#" data-href="${esc(url)}" class="doc-link">${esc(text)}</a>`;
  const inline = (s) => {
    let html = esc(s);
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_m, t, u) => link(t, u));
    html = html.replace(/(^|[\s(（])(https?:\/\/[^\s<)）]+)/g,
      (_m, pre, u) => `${pre}${link(u, u)}`);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    return html;
  };
  const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let list = "";
  const closeList = () => { if (list) { out.push(`</${list}>`); list = ""; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { closeList(); continue; }
    const h = line.match(/^(#{1,3})\s+(.+)$/);
    if (h) { closeList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }
    const ul = line.match(/^[-*]\s+(.+)$/);
    if (ul) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(ul[1])}</li>`);
      continue;
    }
    const ol = line.match(/^\d+\.\s+(.+)$/);
    if (ol) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(ol[1])}</li>`);
      continue;
    }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  return out.join("");
}

let tutorialFromGuide = false;

async function openTutorial(fromGuide = false) {
  tutorialFromGuide = !!fromGuide;
  const footer = $("#tutorialFooter");
  if (footer) footer.classList.toggle("hidden", tutorialFromGuide);
  const modal = $("#tutorialModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  const body = $("#tutorialBody");
  body.innerHTML = '<div class="empty-state">正在加载教程…</div>';
  try {
    const r = await api("/api/tutorial");
    body.innerHTML = renderTutorialMarkdown(r.text || "");
    body.scrollTop = 0;
  } catch (e) {
    body.innerHTML = `<div class="empty-state">教程读取失败：${esc(e.message)}<br>可直接查看项目根目录的「新手教程.md」。</div>`;
  }
}

async function closeTutorial() {
  $("#tutorialModal")?.classList.add("hidden");
  if (tutorialFromGuide) {
    tutorialFromGuide = false;
    return;
  }
  try { localStorage.setItem("qai-tutorial-done", "1"); } catch (e) {}
  await checkSetup();
}

function initGuide() {
  const orb = $("#guideOrb");
  const menu = $("#guideMenu");
  const menuBackdrop = $("#guideMenuBackdrop");
  if (!orb || !menu) return;

  const POS_KEY = "qai-guide-pos";
  let drag = null;
  let lastDragAt = 0;

  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
  const size = () => orb.offsetWidth || 62;

  function firstRun() {
    try { return localStorage.getItem("qai-tutorial-done") !== "1"; } catch (e) { return true; }
  }
  function readPos() {
    try { return JSON.parse(localStorage.getItem(POS_KEY) || "null"); } catch (e) { return null; }
  }
  function savePos() {
    try {
      localStorage.setItem(POS_KEY, JSON.stringify({
        left: parseFloat(orb.style.left) || orb.getBoundingClientRect().left,
        top: parseFloat(orb.style.top) || orb.getBoundingClientRect().top,
      }));
    } catch (e) {}
  }
  function place(pos) {
    if (!pos || !Number.isFinite(Number(pos.left)) || !Number.isFinite(Number(pos.top))) return;
    const s = size();
    orb.style.left = `${clamp(Number(pos.left), 8, window.innerWidth - s - 8)}px`;
    orb.style.top = `${clamp(Number(pos.top), 8, window.innerHeight - s - 8)}px`;
    orb.style.right = "auto";
    orb.style.bottom = "auto";
  }
  function showGuide() {
    orb.classList.remove("hidden", "fading");
    place(readPos());
  }
  function hideGuideMenu() {
    menu.classList.add("hidden");
    menu.classList.remove("below");
    menuBackdrop?.classList.add("hidden");
  }
  function openGuideMenu() {
    menu.classList.remove("hidden");
    menuBackdrop?.classList.remove("hidden");
    const r = orb.getBoundingClientRect();
    const m = menu.getBoundingClientRect();
    const below = r.top - m.height - 12 < 8;
    menu.classList.toggle("below", below);
    menu.style.left = `${clamp(r.left + r.width / 2 - m.width / 2, 8, window.innerWidth - m.width - 8)}px`;
    menu.style.top = `${below ? r.bottom + 12 : r.top - m.height - 12}px`;
  }
  function closeGuideForever() {
    hideGuideMenu();
    orb.classList.add("fading");
    setTimeout(() => {
      orb.classList.add("hidden");
      orb.classList.remove("fading");
    }, 320);
    toast("新手向导已关闭，可在「主题与布局 → 唤醒向导」重新开启");
  }

  if (firstRun()) requestAnimationFrame(showGuide);

  $("#wakeGuide")?.addEventListener("click", () => {
    showGuide();
    $("#appearanceModal")?.classList.add("hidden");
    toast("新手向导已唤醒");
  });

  orb.addEventListener("click", (e) => {
    if (e.button !== 0) return;
    if (Date.now() - lastDragAt < 280) return;
    if (!$("#tutorialModal").classList.contains("hidden")) {
      closeTutorial();
      return;
    }
    hideGuideMenu();
    openTutorial(true);
  });
  orb.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openTutorial(true); }
  });
  orb.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openGuideMenu();
  });

  orb.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const r = orb.getBoundingClientRect();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, left: r.left, top: r.top, moved: false };
    orb.setPointerCapture?.(e.pointerId);
    orb.classList.add("dragging");
  });
  orb.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (Math.hypot(dx, dy) > 4) drag.moved = true;
    if (!drag.moved) return;
    const s = size();
    orb.style.left = `${clamp(drag.left + dx, 8, window.innerWidth - s - 8)}px`;
    orb.style.top = `${clamp(drag.top + dy, 8, window.innerHeight - s - 8)}px`;
    orb.style.right = "auto";
    orb.style.bottom = "auto";
  });
  const endDrag = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const moved = drag.moved;
    drag = null;
    orb.classList.remove("dragging");
    if (moved) { lastDragAt = Date.now(); savePos(); }
  };
  orb.addEventListener("pointerup", endDrag);
  orb.addEventListener("pointercancel", endDrag);

  document.addEventListener("pointerdown", (e) => {
    if (menu.classList.contains("hidden")) return;
    if (menu.contains(e.target) || orb.contains(e.target)) return;
    hideGuideMenu();
  });
  menuBackdrop?.addEventListener("pointerdown", hideGuideMenu);
  $("#guideNo")?.addEventListener("click", hideGuideMenu);
  $("#guideYes")?.addEventListener("click", closeGuideForever);
  $("#tutorialModal")?.addEventListener("click", (e) => {
    if (e.target === $("#tutorialModal") && tutorialFromGuide) closeTutorial();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initGuide();
  $$(".view-tab").forEach((btn) => btn.addEventListener("click", () => setView(btn.dataset.view)));
  $("#startBtn").addEventListener("click", () => action($("#startBtn"), "/api/start"));
  $("#placeholderStart").addEventListener("click", () => action($("#placeholderStart"), "/api/start"));
  $("#stopBtn").addEventListener("click", () => action($("#stopBtn"), "/api/stop"));
  $("#restartBtn").addEventListener("click", () => action($("#restartBtn"), "/api/restart"));
  $("#napcatBtn").addEventListener("click", napcatToggle);
  $("#placeholderNapcat").addEventListener("click", () => action($("#placeholderNapcat"), "/api/napcat/start"));
  $("#tokenBtn").addEventListener("click", openToken);
  $("#closeToken").addEventListener("click", closeToken);
  $("#toggleToken").addEventListener("click", () => {
    S.tokenRevealed = !S.tokenRevealed;
    renderToken();
  });
  $("#copyToken").addEventListener("click", copyToken);
  $("#tokenModal").addEventListener("click", (e) => {
    if (e.target === $("#tokenModal")) closeToken();
  });
  $("#setupBtn").addEventListener("click", openSetup);
  $("#closeSetup").addEventListener("click", closeSetup);
  $("#setupLater").addEventListener("click", closeSetup);
  $("#tutorialBtn").addEventListener("click", openTutorial);
  $("#tutorialDone").addEventListener("click", closeTutorial);
  $("#closeTutorial").addEventListener("click", closeTutorial);
  $("#setupDetect").addEventListener("click", detectNapcat);
  $("#setupSave").addEventListener("click", saveSetup);
  $("#setupKeysOpen")?.addEventListener("click", () => openKeys(""));
  $("#setupCloseBehavior")?.addEventListener("change", async () => {
    const mode = $("#setupCloseBehavior").value;
    try {
      const r = await api("/api/settings/close-behavior", "POST", { mode });
      toast(r.message || "已保存");
    } catch (e) {
      toast("保存失败：" + e.message, "error");
    }
  });
  $("#closeKeys")?.addEventListener("click", closeKeys);
  $("#keysDone")?.addEventListener("click", closeKeys);
  $("#keysModal")?.addEventListener("click", (e) => {
    if (e.target === $("#keysModal")) closeKeys();
  });
  $("#staleBannerClose")?.addEventListener("click", () => $("#staleBanner")?.classList.add("hidden"));
  $("#dataExport")?.addEventListener("click", () => exportData($("#dataExport")));
  $("#dataImport")?.addEventListener("click", () => $("#dataImportFile")?.click());
  $("#dataImportFile")?.addEventListener("change", () => importData($("#dataImport")));
  $("#updateBtn")?.addEventListener("click", openUpdate);
  $("#closeUpdate")?.addEventListener("click", closeUpdate);
  $("#updateModal")?.addEventListener("click", (e) => {
    if (e.target === $("#updateModal")) closeUpdate();
  });
  $("#updateApply")?.addEventListener("click", applyUpdate);
  $("#closeBackground")?.addEventListener("click", () => decideClose("background", $("#closeBackground")));
  $("#closeExit")?.addEventListener("click", () => decideClose("exit", $("#closeExit")));
  // 教程里的网址一律交给系统浏览器，不在 WebView 里跳走
  $("#tutorialBody")?.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-href]");
    if (!a) return;
    e.preventDefault();
    openExternal(a.dataset.href);
  });
  $("#updateLocal")?.addEventListener("click", () => $("#updateLocalFile")?.click());
  $("#updateLocalFile")?.addEventListener("change", () => updateFromLocalFile($("#updateLocal")));
  $("#updatePage")?.addEventListener("click", () => {
    const url = (S.update && S.update.page) || "https://github.com/Algorandius/QAI/releases/latest";
    window.open(url, "_blank");
  });
  checkApiVersion();
  // 启动时静默检查一次：有新版本就把按钮点亮，不打扰用户
  setTimeout(() => { checkUpdate(false); }, 2500);
  $("#setupProvider").addEventListener("change", () => refreshModelUi(null, true));
  $("#setupModel").addEventListener("change", () => {
    const custom = $("#setupModel").value === "__custom__";
    $("#setupModelCustomWrap").classList.toggle("hidden", !custom);
    const model = currentCapabilityModel();
    const provider = providerById($("#setupProvider").value);
    const base = model && model.base ? model.base : provider?.base;
    if (base) $("#setupBase").value = base;
    $("#setupModelDesc").textContent = custom ? "自定义模型" : (model?.desc || "");
    renderCaps(custom ? null : model);
  });
  $("#setupModelCustom").addEventListener("input", () => renderCaps(null));
  $("#setupNapcatInstall").addEventListener("click", startNapcatInstall);
  $("#setupNapcatPan").addEventListener("click", openNapcatPan);
  $("#setupNapcatQuick").addEventListener("click", quickConnectOnebot);
  $("#setupModal").addEventListener("click", (e) => {
    if (e.target === $("#setupModal")) closeSetup();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("#tutorialModal").classList.contains("hidden")) closeTutorial();
    if (!$("#tokenModal").classList.contains("hidden")) closeToken();
    if ($("#keysModal") && !$("#keysModal").classList.contains("hidden")) closeKeys();
    if ($("#updateModal") && !$("#updateModal").classList.contains("hidden")) closeUpdate();
    if (!$("#setupModal").classList.contains("hidden")) closeSetup();
  });
  $("#logBtn").addEventListener("click", () => toggleLog());
  $("#closeLog").addEventListener("click", () => toggleLog(false));

  setView(location.hash === "#napcat" ? "napcat" : "panel");
  // 管理台（8081）里点「去配置」时，跨端口用 postMessage 让外壳打开 Key 面板
  window.addEventListener("message", (e) => {
    let host = "";
    try { host = new URL(e.origin).hostname; } catch (err) { return; }
    if (host !== "127.0.0.1" && host !== "localhost") return;
    const data = e.data;
    if (!data || typeof data !== "object" || data.type !== "qai:open-keys") return;
    openKeys(String(data.provider || ""));
  });
  try {
    const params = new URLSearchParams(location.search);
    if (params.get("view") === "keys") openKeys(params.get("provider") || "");
  } catch (e) {}
  poll();
  let tutorialDone = "1";
  try { tutorialDone = localStorage.getItem("qai-tutorial-done") || ""; } catch (e) {}
  if (tutorialDone === "1") checkSetup();
  else openTutorial();
  setInterval(() => { if (!document.hidden) poll(); }, 2200);
  setInterval(() => { if (S.logOpen && !document.hidden) loadLog(); }, 3000);
});
