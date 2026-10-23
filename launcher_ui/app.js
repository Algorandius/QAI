/* QAI 外壳：状态 + 启停 + 内嵌 8081 管理台 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const S = { status: null, busy: false, logOpen: false, offline: false, view: "panel", tokenInfo: null, tokenRevealed: false, setup: null, providerKeys: {} };

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
  const opts = { method, cache: "no-store" };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
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
    $("#setupDetectResult").textContent = st.napcat_ok
      ? `已检测到 NapCat：${st.napcat_dir}`
      : `还没检测到有效的 NapCat。可点「安装到 QAI 目录」（默认 ${st.napcat_default_dir || "QAI\\napcat"}）或手动选择。`;
    $("#setupSaveHint").textContent = st.llm_key_set
      ? "API Key 已配置；留空会保持不变。保存后如果机器人正在运行，会重启一次使配置生效。"
      : "保存后会写入 `.env`，并自动启动机器人。";
    $("#setupNapcatBar").style.width = "0%";
    $("#setupNapcatState").textContent = "从 GitHub 下载最新 Windows Node 包，约 120MB；安装后仍需扫码登录 QQ。";
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
  try {
    await withBusy(btn, async () => {
      const d = await api("/api/setup/detect");
      const list = d.napcat || [];
      const valid = list.find((x) => x.launcher) || list[0];
      if (valid) $("#setupNapdir").value = valid.path;
      const lines = [];
      if (list.length) {
        for (const x of list) {
          lines.push(`${x.launcher ? "✓" : "×"} ${x.path}${x.launcher ? "" : "（缺 launcher-win10.bat）"}`);
        }
      } else {
        lines.push("没有在常见目录找到 NapCat；请手动填写安装目录。");
      }
      if (d.qq && d.qq.length) lines.push(`QQ：${d.qq.join(" / ")}`);
      else lines.push("QQ：没在常见目录找到，请确认 QQNT 已安装。");
      $("#setupDetectResult").textContent = lines.join("  ·  ");
    });
  } catch (e) {
    toast("自动检测失败：" + e.message, "error");
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
    });
    closeSetup();
    await poll();
  } catch (e) {
    toast(e.message, "error");
    $("#setupSaveHint").textContent = "保存失败：" + e.message;
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

function renderTutorialMarkdown(md) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
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

async function openTutorial() {
  const modal = $("#tutorialModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  const body = $("#tutorialBody");
  body.innerHTML = '<div class="empty-state">正在加载教程…</div>';
  try {
    const r = await api("/api/tutorial");
    body.innerHTML = renderTutorialMarkdown(r.text || "");
  } catch (e) {
    body.innerHTML = `<div class="empty-state">教程读取失败：${esc(e.message)}<br>可直接查看项目根目录的「新手教程.md」。</div>`;
  }
}

async function closeTutorial() {
  $("#tutorialModal")?.classList.add("hidden");
  try { localStorage.setItem("qai-tutorial-done", "1"); } catch (e) {}
  await checkSetup();
}

document.addEventListener("DOMContentLoaded", () => {
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
  $("#setupModal").addEventListener("click", (e) => {
    if (e.target === $("#setupModal")) closeSetup();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("#tutorialModal").classList.contains("hidden")) closeTutorial();
    if (!$("#tokenModal").classList.contains("hidden")) closeToken();
    if (!$("#setupModal").classList.contains("hidden")) closeSetup();
  });
  $("#logBtn").addEventListener("click", () => toggleLog());
  $("#closeLog").addEventListener("click", () => toggleLog(false));

  setView(location.hash === "#napcat" ? "napcat" : "panel");
  poll();
  let tutorialDone = "1";
  try { tutorialDone = localStorage.getItem("qai-tutorial-done") || ""; } catch (e) {}
  if (tutorialDone === "1") checkSetup();
  else openTutorial();
  setInterval(() => { if (!document.hidden) poll(); }, 2200);
  setInterval(() => { if (S.logOpen && !document.hidden) loadLog(); }, 3000);
});
