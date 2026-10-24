/* QQ 机器人控制台 —— 单页应用（无外部依赖） */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const S = {
  status: null,
  personas: [],
  current: "",
  scenes: [],
  settings: { allowed_groups: [], bot_admin_qq: [] },
  tab: "dashboard",
  chatScene: "",
  affScene: "",
  affSig: "",
  modal: { mode: "create", pid: "", avatarData: "", avatarExisting: "", avatarClear: false },
  stickerItems: [],
  stickerSublibs: [],
  stickerFilter: "all",
  recentItems: [],
  recentVisible: 5,
  recentObserver: null,
  recentResizeTimer: 0,
  models: { providers: [], current: {} },
};

const TAB_META = {
  dashboard: ["概览", "机器人运行状态、人设和最近对话"],
  groups: ["群与人设", "每个群独立挂人设、白名单与记忆管理"],
  personas: ["人设库", "管理角色、头像与默认展示"],
  chatlog: ["聊天记录", "筛选并查看机器人说过的话"],
  affinity: ["好感度", "按群成员记录的好感分数"],
  stickers: ["表情库", "收藏表情与标签管理"],
  settings: ["设置", "管理员、白名单与 .env 配置"],
};

/* ---------------- 基础工具 ---------------- */
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function n(v) {
  const x = Number(v ?? 0);
  return Number.isFinite(x) ? x.toLocaleString("zh-CN") : "0";
}
function pct(v) {
  const x = Number(v);
  return Number.isFinite(x) ? `${Math.round(x * 100)}%` : "—";
}
function initials(name) {
  const s = String(name || "?").trim();
  return Array.from(s).slice(0, 2).join("") || "?";
}
function avatarMarkup(p, className = "persona-avatar") {
  const url = p && p.avatar ? String(p.avatar) : "";
  return `<div class="${className}">${url
    ? `<img src="${esc(url)}" alt="" loading="lazy">`
    : esc(initials(p && p.name))}</div>`;
}
function timeAgo(ts) {
  const t = Number(ts || 0);
  if (!t) return "—";
  const d = Math.max(0, Date.now() / 1000 - t);
  if (d < 60) return "刚刚";
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  return `${Math.floor(d / 86400)} 天前`;
}
function fmtTs(s) {
  if (!s) return "—";
  return String(s).replace("T", " ").slice(0, 19);
}
function sceneKey(s) {
  return s.key || `${s.scene}:${s.id}`;
}
function sceneLabel(s) {
  const kind = s.scene || String(s.key || "").split(":")[0];
  const id = s.id ?? String(s.key || "").split(":")[1];
  return kind === "group" ? `群 ${id}` : `私聊 ${id}`;
}
function moodClass(v) {
  const x = Number(v || 0);
  if (x >= 2) return "green";
  if (x <= -2) return "red";
  return "warn";
}
function personaName(id) {
  const p = S.personas.find((x) => x.id === id);
  return p ? p.name : (id || "—");
}
function configChips(cfg) {
  const c = cfg || {};
  const out = [];
  if (c.AI_MAX_CHARS != null) out.push(`整轮 ${c.AI_MAX_CHARS} 字`);
  if (c.AI_MSG_MAX_CHARS != null) out.push(`单条 ${c.AI_MSG_MAX_CHARS}`);
  if (c.AI_MAX_MSGS != null) out.push(`一轮 ${c.AI_MAX_MSGS} 条`);
  if (c.AI_GROUP_AUTO_PROB != null) out.push(`接话 ${pct(c.AI_GROUP_AUTO_PROB)}`);
  if (c.AI_TYPE_CPS != null) out.push(`打字 ${c.AI_TYPE_CPS}/s`);
  return out.slice(0, 5);
}

const PERSONA_QUICK_DEFS = [
  { key: "AI_MAX_CHARS", label: "整轮字数", type: "int", min: 50, max: 2000, step: 1, unit: "字", desc: "一次回复的总字数上限" },
  { key: "AI_MSG_MAX_CHARS", label: "单条字数", type: "int", min: 8, max: 500, step: 1, unit: "字", desc: "单条消息最多几个字" },
  { key: "AI_GROUP_AUTO_PROB", label: "接话概率", type: "percent", min: 0, max: 100, step: 1, unit: "%", desc: "没被 @ 时主动接话的概率" },
  { key: "LLM_TEMPERATURE", label: "模型温度", type: "float", min: 0, max: 2, step: 0.05, unit: "", desc: "越高越随机，越低越稳定" },
  { key: "AI_MAX_MSGS", label: "单轮条数", type: "int", min: 1, max: 20, step: 1, unit: "条", desc: "一次最多连发几条" },
  { key: "AI_TYPE_CPS", label: "打字速度", type: "float", min: 0.1, max: 100, step: 0.5, unit: "字/秒", desc: "越小越慢，越像真人" },
  { key: "AI_THINK_MIN", label: "最短思考", type: "float", min: 0, max: 60, step: 0.1, unit: "秒", desc: "回复前最短停顿" },
  { key: "AI_THINK_MAX", label: "最长思考", type: "float", min: 0, max: 60, step: 0.1, unit: "秒", desc: "回复前最长停顿" },
  { key: "AI_GAP_MIN", label: "最短间隔", type: "float", min: 0, max: 60, step: 0.1, unit: "秒", desc: "多条消息之间的最短间隔" },
  { key: "AI_GAP_MAX", label: "最长间隔", type: "float", min: 0, max: 60, step: 0.1, unit: "秒", desc: "多条消息之间的最长间隔" },
  { key: "AI_QUOTE_PROB", label: "主动引用", type: "percent", min: 0, max: 100, step: 1, unit: "%", desc: "主动引用别人消息的概率" },
  { key: "AI_STICKER_PROB", label: "表情概率", type: "percent", min: 0, max: 100, step: 1, unit: "%", desc: "回复时附带表情的概率" },
];

let toastTimer = null;
function toast(msg, type = "ok") {
  const el = $("#toast");
  if (!el) return;
  el.textContent = String(msg || "");
  el.className = `toast show ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3400);
}

function qaiConfirm(message, options = {}) {
  const modal = $("#qaiConfirmModal");
  if (!modal) return Promise.resolve(false);
  return new Promise((resolve) => {
    const ok = $("#qaiConfirmOk");
    const cancel = $("#qaiConfirmCancel");
    const text = $("#qaiConfirmText");
    $("#qaiConfirmTitle").textContent = options.title || "请确认";
    text.textContent = String(message || "");
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

// 写接口令牌：服务端注入到 <meta name="qai-token">，只有同源脚本读得到。
const API_TOKEN = (document.querySelector('meta[name="qai-token"]') || {}).content || "";

async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (API_TOKEN) opts.headers["X-QAI-Token"] = API_TOKEN;
  if (opts.body !== undefined && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { cache: "no-store", ...opts });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) throw new Error((data && (data.error || data.msg)) || `HTTP ${res.status}`);
  if (data && data.ok === false) throw new Error(data.error || data.msg || "操作失败");
  return data;
}

async function withBusy(btn, fn) {
  if (!btn) return fn();
  const old = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try { return await fn(); } finally {
    btn.disabled = false;
    btn.innerHTML = old;
  }
}

/* ---------------- 导航 ---------------- */
function staggerCards(panel) {
  if (!panel || document.documentElement.classList.contains("card-layout-on")) return;
  const items = [...panel.querySelectorAll(".card, .stat-card, .quick-action")].filter((el) => el.closest(".tab-panel") === panel);
  items.forEach((el, i) => {
    el.classList.remove("qai-stagger");
    el.style.setProperty("--stagger-delay", `${Math.min(i, 12) * 55}ms`);
    void el.offsetWidth;
    el.classList.add("qai-stagger");
    el.addEventListener("animationend", () => {
      el.classList.remove("qai-stagger");
      el.style.removeProperty("--stagger-delay");
    }, { once: true });
  });
}

function activateTab(name, opts = {}) {
  if (!TAB_META[name]) name = "dashboard";
  S.tab = name;
  if (name !== "groups") closeBindForm(true);
  $$(".nav-item").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  let activePanel = null;
  $$(".tab-panel").forEach((p) => {
    const active = p.id === `tab-${name}`;
    p.classList.toggle("active", active);
    if (active) {
      activePanel = p;
      p.classList.remove("tab-enter");
      void p.offsetWidth;
      p.classList.add("tab-enter");
    }
  });
  if (activePanel) staggerCards(activePanel);
  window.scrollTo({ top: 0, behavior: "smooth" });
  const [title, sub] = TAB_META[name];
  $("#pageTitle").textContent = title;
  $("#pageSub").textContent = sub;
  if (name === "dashboard") {
    renderStats();
    renderPersonaHero();
    loadRecent();
  } else if (name === "groups") {
    renderScenes();
  } else if (name === "personas") {
    renderPersonas();
  } else if (name === "chatlog") {
    syncSceneSelect("chat");
    loadChatlog();
  } else if (name === "affinity") {
    syncSceneSelect("aff");
    loadAffinity();
  } else if (name === "stickers") {
    loadStickers();
  } else if (name === "settings") {
    loadSettings();
    loadConfig();
    loadEnvParams();
  }
  if (!opts.keepHash) {
    const hash = `#${name}`;
    if (location.hash !== hash) history.replaceState(null, "", hash);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
  syncCardLayout();
}

/* ---------------- 卡片布局 ---------------- */
function cardKey(el) {
  if (!el) return "";
  if (el.dataset.cardKey) return el.dataset.cardKey;
  const explicit = el.id || el.dataset.personaId || el.dataset.goto || "";
  const heading = el.querySelector("h2, .stat-label, b")?.textContent || "";
  const raw = (explicit || heading).replace(/\s+/g, " ").trim().slice(0, 64);
  el.dataset.cardKey = raw ? `${el.className.split(/\s+/)[0]}:${raw}` : `card:${Date.now()}-${Math.random()}`;
  return el.dataset.cardKey;
}

function isLayoutItem(el) {
  if (!el || !el.matches(".card, .stat-card")) return false;
  if (!el.closest(".tab-panel.active")) return false;
  return !el.parentElement || !el.parentElement.closest(".card, .stat-card");
}

function layoutItemFrom(node) {
  let el = node && node.closest(".card, .stat-card, .quick-action");
  while (el && !isLayoutItem(el)) {
    el = el.parentElement ? el.parentElement.closest(".card, .stat-card, .quick-action") : null;
  }
  return el;
}

function cardsInActiveTab() {
  return $$(".tab-panel.active .card, .tab-panel.active .stat-card").filter(isLayoutItem);
}

function applyCardOrder() {
  let saved = [];
  try {
    if (!localStorage.getItem("qai-overview-swapped-v2")) {
      localStorage.removeItem("qai-card-order");
      localStorage.setItem("qai-overview-swapped-v2", "1");
    }
    saved = JSON.parse(localStorage.getItem("qai-card-order") || "[]");
  } catch (e) { saved = []; }
  if (!saved.length) {
    cardsInActiveTab().forEach(cardKey);
    return;
  }
  const idx = new Map(saved.map((k, i) => [k, i]));
  const groups = new Map();
  cardsInActiveTab().forEach((el) => {
    cardKey(el);
    const parent = el.parentElement;
    if (!parent) return;
    if (!groups.has(parent)) groups.set(parent, []);
    groups.get(parent).push(el);
  });
  groups.forEach((items, parent) => {
    if (items.length < 2) return;
    if (parent.classList.contains("dashboard-top")) {
      const persona = parent.querySelector(".persona-hero-card");
      const recent = parent.querySelector(".recent-card");
      if (persona) parent.appendChild(persona);
      if (recent) parent.appendChild(recent);
      return;
    }
    items.sort((a, b) => (idx.get(a.dataset.cardKey) ?? 9999) - (idx.get(b.dataset.cardKey) ?? 9999));
    items.forEach((el) => parent.appendChild(el));
  });
}

function saveCardOrder() {
  const keys = cardsInActiveTab().map(cardKey).filter(Boolean);
  try { localStorage.setItem("qai-card-order", JSON.stringify(keys)); } catch (e) {}
}

function layoutOn() {
  return document.documentElement.dataset.cardLayout === "on";
}

function readCardSizes() {
  try { return JSON.parse(localStorage.getItem("qai-card-sizes") || "{}") || {}; } catch (e) { return {}; }
}

function gridColumnsForParent(parent) {
  if (!parent) return 1;
  if (parent.classList.contains("stats-grid")) return 4;
  if (parent.classList.contains("grid-2")) return 2;
  return 1;
}

function gridColumns(card) {
  return gridColumnsForParent(card && card.parentElement);
}

function applyCardSizes() {
  const sizes = readCardSizes();
  cardsInActiveTab().forEach((card) => {
    const s = sizes[cardKey(card)];
    const cols = gridColumns(card);
    const span = s && s.span > 1 ? Math.min(s.span, cols) : 1;
    card.style.gridColumn = span > 1 ? `span ${span}` : "";
    card.style.minHeight = s && s.height ? `${s.height}px` : "";
  });
}

function syncResizeHandles() {
  document.querySelectorAll(".card-resize").forEach((el) => el.remove());
  if (!layoutOn()) return;
  cardsInActiveTab().forEach((card) => {
    if (card.querySelector(":scope > .card-resize")) return;
    const handle = document.createElement("span");
    handle.className = "card-resize";
    handle.title = "拖动调整卡片大小，双击恢复";
    handle.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      const sizes = readCardSizes();
      delete sizes[cardKey(card)];
      try { localStorage.setItem("qai-card-sizes", JSON.stringify(sizes)); } catch (err) {}
      card.style.gridColumn = "";
      card.style.minHeight = "";
      toast("已恢复这张卡片的大小");
    });
    card.appendChild(handle);
  });
}

function syncCardLayout() {
  document.documentElement.classList.toggle("card-layout-on", layoutOn());
  applyCardOrder();
  applyCardSizes();
  syncResizeHandles();
}

let cardPressTimer = 0;
let cardCandidate = null;
let cardStartX = 0;
let cardStartY = 0;
let draggingCard = null;
let dragPlaceholder = null;
let dragGhost = null;
let dragLayer = null;
let dragStartX = 0;
let dragStartY = 0;
let dragOriginalStyle = "";
let suppressCardClick = false;

function cardSelector() {
  return ".tab-panel.active .card, .tab-panel.active .stat-card";
}

function clearCardPress() {
  clearTimeout(cardPressTimer);
  cardPressTimer = 0;
  cardCandidate = null;
}

function startCardDrag(card, pointer) {
  if (draggingCard || !card) return;
  clearCardPress();
  const rect = card.getBoundingClientRect();
  draggingCard = card;
  dragOriginalStyle = card.getAttribute("style") || "";
  dragStartX = pointer.clientX;
  dragStartY = pointer.clientY;
  dragPlaceholder = document.createElement("div");
  dragPlaceholder.className = "card-drag-placeholder";
  dragPlaceholder.style.height = `${rect.height}px`;
  dragPlaceholder.style.width = `${rect.width}px`;
  dragPlaceholder.style.gridColumn = card.style.gridColumn || "";
  dragPlaceholder.style.minHeight = card.style.minHeight || "";
  card.parentElement.insertBefore(dragPlaceholder, card);

  // A clone on a fixed body layer: avoids a transformed tab-panel becoming
  // the containing block for position:fixed, which made the card stick.
  dragLayer = document.createElement("div");
  dragLayer.className = "qai-panel card-drag-layer";
  dragGhost = card.cloneNode(true);
  dragGhost.querySelectorAll(".card-resize").forEach((el) => el.remove());
  dragGhost.removeAttribute("id");
  dragGhost.classList.add("card-dragging", "card-drag-ghost");
  dragGhost.style.position = "absolute";
  dragGhost.style.left = `${rect.left}px`;
  dragGhost.style.top = `${rect.top}px`;
  dragGhost.style.width = `${rect.width}px`;
  dragGhost.style.height = `${rect.height}px`;
  dragGhost.style.margin = "0";
  dragGhost.style.zIndex = "1000";
  dragGhost.style.pointerEvents = "none";
  dragGhost.style.boxSizing = "border-box";
  dragGhost.style.transform = "translate3d(0, 0, 0) scale(1.015)";
  dragGhost.style.transition = "none";
  dragGhost.style.willChange = "transform";
  dragLayer.appendChild(dragGhost);
  document.body.appendChild(dragLayer);

  card.style.display = "none";
  document.body.classList.add("card-dragging");
}

function moveCard(pointer) {
  if (!draggingCard || !dragPlaceholder || !dragGhost) return;
  const dx = pointer.clientX - dragStartX;
  const dy = pointer.clientY - dragStartY;
  dragGhost.style.transform = `translate3d(${dx}px, ${dy}px, 0) scale(1.015)`;
  const under = document.elementFromPoint(pointer.clientX, pointer.clientY);
  const over = layoutItemFrom(under);
  if (!over || over === draggingCard || over === dragPlaceholder) return;
  const parent = over.parentElement;
  if (parent !== dragPlaceholder.parentElement) {
    const cols = gridColumnsForParent(parent);
    const parsed = parseInt((draggingCard.style.gridColumn || "").replace(/\D/g, ""), 10);
    const span = Math.max(1, Math.min(cols, Number.isFinite(parsed) && parsed > 0 ? parsed : 1));
    dragPlaceholder.style.width = "";
    dragPlaceholder.style.height = `${over.getBoundingClientRect().height}px`;
    dragPlaceholder.style.gridColumn = span > 1 ? `span ${span}` : "";
  }
  const beforeRect = over.getBoundingClientRect();
  const middleY = beforeRect.top + beforeRect.height / 2;
  const before = pointer.clientY < middleY
    || (Math.abs(pointer.clientY - middleY) < beforeRect.height * .22
      && pointer.clientX < beforeRect.left + beforeRect.width / 2);
  const items = [...parent.children].filter((el) =>
    el !== draggingCard && el !== dragPlaceholder && isLayoutItem(el)
  );
  const first = new Map(items.map((el) => [el, el.getBoundingClientRect()]));
  parent.insertBefore(dragPlaceholder, before ? over : over.nextSibling);
  requestAnimationFrame(() => {
    items.forEach((el) => {
      const a = first.get(el);
      const b = el.getBoundingClientRect();
      const x = a.left - b.left;
      const y = a.top - b.top;
      if (Math.abs(x) < .5 && Math.abs(y) < .5) return;
      el.style.transition = "none";
      el.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      el.getBoundingClientRect();
      requestAnimationFrame(() => {
        el.style.transition = "transform .3s cubic-bezier(.22,1,.36,1)";
        el.style.transform = "";
        setTimeout(() => { el.style.transition = ""; }, 330);
      });
    });
  });
  document.querySelectorAll(".card-drag-over").forEach((el) => el.classList.remove("card-drag-over"));
  over.classList.add("card-drag-over");
}

function endCardDrag(save) {
  clearCardPress();
  if (!draggingCard) return;
  const card = draggingCard;
  const ph = dragPlaceholder;
  const ghost = dragGhost;
  const layer = dragLayer;
  draggingCard = null;
  dragGhost = null;
  dragLayer = null;
  document.body.classList.remove("card-dragging");
  document.querySelectorAll(".card-drag-over").forEach((el) => el.classList.remove("card-drag-over"));
  if (!ph || !ghost) {
    if (ph) ph.remove();
    if (layer) layer.remove();
    if (dragOriginalStyle) card.setAttribute("style", dragOriginalStyle);
    else card.removeAttribute("style");
    dragPlaceholder = null;
    return;
  }
  const phRect = ph.getBoundingClientRect();
  const ghostRect = ghost.getBoundingClientRect();
  ghost.style.transition = "transform .22s cubic-bezier(.22,1,.36,1)";
  ghost.style.transform = `translate3d(${phRect.left - ghostRect.left}px, ${phRect.top - ghostRect.top}px, 0) scale(1)`;
  dragPlaceholder = null;
  setTimeout(() => {
    if (ph.parentElement) ph.parentElement.insertBefore(card, ph);
    ph.remove();
    if (layer) layer.remove();
    card.classList.remove("card-dragging");
    if (dragOriginalStyle) card.setAttribute("style", dragOriginalStyle);
    else card.removeAttribute("style");
    const cols = gridColumns(card);
    const parsed = parseInt((card.style.gridColumn || "").replace(/\D/g, ""), 10);
    if (Number.isFinite(parsed) && parsed > cols) card.style.gridColumn = cols > 1 ? `span ${cols}` : "";
    if (save) {
      saveCardOrder();
      toast("布局已保存为默认");
      suppressCardClick = true;
      setTimeout(() => { suppressCardClick = false; }, 100);
    }
  }, 230);
}

function bindCardResize() {
  let state = null;

  function animateSpanChange(parent, card, before) {
    const items = [...parent.children].filter((el) =>
      el !== card && isLayoutItem(el)
    );
    requestAnimationFrame(() => {
      items.forEach((el) => {
        const a = before.get(el);
        if (!a) return;
        const b = el.getBoundingClientRect();
        const x = a.left - b.left;
        const y = a.top - b.top;
        if (Math.abs(x) < .5 && Math.abs(y) < .5) return;
        el.style.transition = "none";
        el.style.transform = `translate3d(${x}px, ${y}px, 0)`;
        el.getBoundingClientRect();
        requestAnimationFrame(() => {
          el.style.transition = "transform .3s cubic-bezier(.22,1,.36,1)";
          el.style.transform = "";
          setTimeout(() => { el.style.transition = ""; }, 330);
        });
      });
    });
  }

  document.addEventListener("pointerdown", (e) => {
    if (!layoutOn()) return;
    const handle = e.target.closest(".card-resize");
    if (!handle) return;
    const card = layoutItemFrom(handle);
    if (!card) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = card.getBoundingClientRect();
    const parsed = parseInt((card.style.gridColumn || "").replace(/\D/g, ""), 10);
    state = {
      card,
      cols: gridColumns(card),
      span: Number.isFinite(parsed) && parsed > 0 ? parsed : 1,
      startX: e.clientX,
      startY: e.clientY,
      startHeight: rect.height,
      guide: null,
    };
    state.guide = document.createElement("div");
    state.guide.className = "card-snap-guide";
    document.body.appendChild(state.guide);
    card.classList.add("card-resizing");
    document.body.classList.add("card-resizing");
  }, { passive: false });

  document.addEventListener("pointermove", (e) => {
    if (!state) return;
    e.preventDefault();
    const { card, cols, startX, startY, startHeight } = state;
    const parent = card.parentElement;
    const colWidth = Math.max(40, parent.getBoundingClientRect().width / cols);
    const deltaCols = Math.round((e.clientX - startX) / colWidth);
    const nextSpan = Math.max(1, Math.min(cols, state.span + deltaCols));
    if (nextSpan !== state.span && cols > 1) {
      const before = new Map([...parent.children]
        .filter((el) => el !== card && isLayoutItem(el))
        .map((el) => [el, el.getBoundingClientRect()]));
      card.style.gridColumn = nextSpan > 1 ? `span ${nextSpan}` : "";
      state.span = nextSpan;
      animateSpanChange(parent, card, before);
    }
    // snap height to 20px rows instead of arbitrary pixel sizes
    const height = Math.max(80, Math.round((startHeight + (e.clientY - startY)) / 20) * 20);
    card.style.minHeight = `${height}px`;
    const r = card.getBoundingClientRect();
    if (state.guide) {
      state.guide.style.left = `${r.left}px`;
      state.guide.style.width = `${r.width}px`;
      state.guide.style.top = `${r.top + height}px`;
      state.guide.style.opacity = "1";
    }
  });

  const finish = (save) => {
    if (!state) return;
    const card = state.card;
    if (state.guide) state.guide.remove();
    state.guide = null;
    card.classList.remove("card-resizing");
    document.body.classList.remove("card-resizing");
    if (save) {
      const sizes = readCardSizes();
      sizes[cardKey(card)] = {
        span: state.span,
        height: Math.round(card.getBoundingClientRect().height),
      };
      try { localStorage.setItem("qai-card-sizes", JSON.stringify(sizes)); } catch (e) {}
      toast("卡片大小已保存为默认");
    }
    state = null;
  };
  document.addEventListener("pointerup", () => finish(true));
  document.addEventListener("pointercancel", () => finish(false));
  window.addEventListener("blur", () => finish(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state) finish(false);
  });
}

function bindCardLayout() {
  const selector = cardSelector();
  document.addEventListener("pointerdown", (e) => {
    if (!layoutOn() || e.button !== 0) return;
    const card = layoutItemFrom(e.target);
    if (!card) return;
    const interactive = e.target.closest("a, button, input, select, textarea, label, .card-resize");
    if (interactive && !interactive.classList.contains("quick-action")) return;
    cardCandidate = card;
    cardStartX = e.clientX;
    cardStartY = e.clientY;
    clearTimeout(cardPressTimer);
    cardPressTimer = setTimeout(() => {
      if (!cardCandidate) return;
      startCardDrag(cardCandidate, { clientX: cardStartX, clientY: cardStartY });
    }, 350);
  }, { passive: true });

  document.addEventListener("pointermove", (e) => {
    if (cardCandidate) {
      if (Math.hypot(e.clientX - cardStartX, e.clientY - cardStartY) > 8) clearCardPress();
      return;
    }
    if (!draggingCard) return;
    e.preventDefault();
    moveCard(e);
  });

  document.addEventListener("pointerup", () => endCardDrag(true));
  document.addEventListener("pointercancel", () => endCardDrag(false));
  window.addEventListener("blur", () => endCardDrag(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && draggingCard) endCardDrag(false);
  });
  document.addEventListener("click", (e) => {
    if (!suppressCardClick) return;
    e.preventDefault();
    e.stopPropagation();
    suppressCardClick = false;
  }, true);
  document.addEventListener("click", (e) => {
    if (!layoutOn() || e.target.closest(".card-resize")) return;
    const card = layoutItemFrom(e.target);
    if (!card) return;
    e.preventDefault();
    e.stopPropagation();
  }, true);
  window.addEventListener("qai-prefs-changed", () => {
    if (!layoutOn()) endCardDrag(false);
    syncCardLayout();
  });
}

/* ---------------- 状态 / 概览 ---------------- */
async function loadStatus() {
  const data = await api("/api/status");
  S.status = data;
  S.personas = data.persona?.list || [];
  S.current = data.persona?.id || "";
  S.scenes = data.scenes || [];
  renderStats();
  renderTopPersona();
  renderPersonaHero();
  renderRunInfo();
  renderScenes();
  fillSceneSelects();
  $("#connBadge")?.classList.remove("offline");
  const footer = $("#footerText");
  if (footer) {
    footer.textContent = `数据来自本机 · 更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  }
}

function renderStats() {
  const s = S.status;
  if (!s) return;
  const cards = [
    ["运行时长", s.uptime || "—", "机器人已在线", "purple"],
    ["收到消息", n(s.recv), "本进程累计", ""],
    ["发出消息", n(s.sent), "本进程累计", "green"],
    ["AI 调用", n(s.ai_calls), `失败 ${n(s.ai_fail)}`, s.ai_fail ? "red" : "purple"],
    ["被限流", n(s.blocked), "冷却 / 概率拦下", "amber"],
    ["场景", n((s.scenes || []).length), "群 + 私聊", ""],
    ["长期记忆", n(s.longterm_total), "条", "purple"],
    ["人设展示", s.persona?.name || "—", "默认", "green"],
  ];
  const grid = $("#statGrid");
  if (!grid) return;
  const existing = new Map();
  grid.querySelectorAll(".stat-card").forEach((el) => {
    if (el.dataset.cardKey) existing.set(el.dataset.cardKey, el);
  });
  const wanted = new Set(cards.map(([label]) => `stat:${label}`));
  let changed = false;
  cards.forEach(([label, value, sub, cls]) => {
    const key = `stat:${label}`;
    let el = existing.get(key);
    if (!el) {
      el = document.createElement("div");
      el.dataset.cardKey = key;
      el.innerHTML = '<div class="stat-label"></div><div class="stat-value"></div><div class="stat-sub"></div>';
      grid.appendChild(el);
      changed = true;
    }
    const className = `stat-card ${cls}`.trim();
    if (el.className !== className) el.className = className;
    const labelEl = el.querySelector(".stat-label");
    const valueEl = el.querySelector(".stat-value");
    const subEl = el.querySelector(".stat-sub");
    if (labelEl && labelEl.textContent !== label) labelEl.textContent = label;
    if (valueEl && valueEl.textContent !== value) valueEl.textContent = value;
    if (subEl && subEl.textContent !== sub) subEl.textContent = sub;
  });
  grid.querySelectorAll(".stat-card").forEach((el) => {
    if (!wanted.has(el.dataset.cardKey)) { el.remove(); changed = true; }
  });
  if (changed) applyCardOrder();
}

function renderTopPersona() {
  const el = $("#topPersona");
  if (el) el.textContent = S.status?.persona ? `人设展示 · ${S.status.persona.name}` : "人设展示 · —";
}

function renderPersonaHero() {
  const box = $("#personaHero");
  if (!box) return;
  if (!S.status) {
    box.innerHTML = '<div class="empty-state">还没有数据，点右上角刷新</div>';
    return;
  }
  const cur = S.personas.find((p) => p.id === S.current) || S.personas[0];
  if (!cur) {
    box.innerHTML = '<div class="empty-state">personas/ 里还没有人设</div>';
    return;
  }
  const cfg = cur.config || {};
  const chips = configChips(cfg).map((x) => `<span class="badge purple">${esc(x)}</span>`).join("");
  const fmt = (v, suffix = "") => (v == null || v === "" ? "默认" : `${v}${suffix}`);
  const overrideCount = (S.scenes || []).filter((s) => s.persona).length;
  const sceneCount = (S.scenes || []).length;
  const facts = [
    ["使用范围", overrideCount ? `${overrideCount} 个场景已单独设置` : "默认展示人设"],
    ["场景", `${sceneCount} 个`],
    ["长期记忆", `${n(S.status.longterm_total)} 条`],
    ["参数", `${Object.keys(cfg).length} 项`],
  ];
  const params = [
    ["整轮字数", fmt(cfg.AI_MAX_CHARS, " 字")],
    ["单条字数", fmt(cfg.AI_MSG_MAX_CHARS, " 字")],
    ["单轮条数", fmt(cfg.AI_MAX_MSGS, " 条")],
    ["接话概率", cfg.AI_GROUP_AUTO_PROB != null ? pct(cfg.AI_GROUP_AUTO_PROB) : "默认"],
    ["打字速度", fmt(cfg.AI_TYPE_CPS, " 字/秒")],
    ["温度", fmt(cfg.LLM_TEMPERATURE)],
    ["思考间隔", cfg.AI_THINK_MIN != null || cfg.AI_THINK_MAX != null
      ? `${cfg.AI_THINK_MIN ?? "?"}–${cfg.AI_THINK_MAX ?? "?"} 秒` : "默认"],
    ["消息间隔", cfg.AI_GAP_MIN != null || cfg.AI_GAP_MAX != null
      ? `${cfg.AI_GAP_MIN ?? "?"}–${cfg.AI_GAP_MAX ?? "?"} 秒` : "默认"],
    ["主动引用", cfg.AI_QUOTE_PROB != null ? pct(cfg.AI_QUOTE_PROB) : "默认"],
    ["表情概率", cfg.AI_STICKER_PROB != null ? pct(cfg.AI_STICKER_PROB) : "默认"],
  ];
  const quickFields = PERSONA_QUICK_DEFS;
  const quickValue = (key, percent = false) => {
    const v = cfg[key];
    if (v == null || v === "") return "";
    return percent ? String(Math.round(Number(v) * 100)) : String(v);
  };
  box.innerHTML = `
    <div class="persona-hero">
      ${avatarMarkup(cur)}
      <div class="persona-hero-main">
        <div class="persona-hero-name">${esc(cur.name)} <span class="persona-card-id">${esc(cur.id)}</span></div>
        <div class="persona-hero-desc">${esc(cur.desc || "暂无简介")}</div>
        <div class="persona-hero-meta">${chips || '<span class="badge">使用 .env 默认参数</span>'}</div>
      </div>
    </div>
    <div class="persona-facts">
      ${facts.map(([k, v]) => `<div class="persona-fact"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}
    </div>
    <div class="persona-params">
      ${params.map(([k, v]) => `<div class="persona-param"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}
    </div>
    <div class="persona-quick">
      <div class="persona-quick-head">
        <span>快捷调整</span><span class="hint">全部 ${quickFields.length} 项</span>
      </div>
      <div class="persona-quick-tip">鼠标滚轮或按住中键拖动，右侧滚动条可上下拖动</div>
      <div class="persona-quick-scroll" tabindex="0">
        <div class="persona-quick-grid">
          ${quickFields.map((d) => `
            <label>${esc(d.label)}${d.unit ? ` ${esc(d.unit)}` : ""}
              <input type="number" min="${d.min}" max="${d.max}" step="${d.step}" data-persona-param="${esc(d.key)}" ${d.type === "percent" ? 'data-percent="1"' : ""} value="${esc(quickValue(d.key, d.type === "percent"))}" placeholder="跟随 .env">
            </label>`).join("")}
        </div>
      </div>
      <div class="persona-quick-actions">
        <button class="btn btn-primary btn-sm" id="savePersonaQuick" type="button">保存人设参数</button>
        <span class="hint">${quickFields.length}/${PERSONA_QUICK_DEFS.length} 项</span>
      </div>
    </div>
    <div class="persona-actions">
      <button class="btn btn-primary btn-sm" id="editGlobalBtn" type="button">编辑人设</button>
    </div>`;
}

function renderRunInfo() {
  const s = S.status;
  const box = $("#runInfo");
  if (!box || !s) return;
  const rows = [
    ["人设展示", `${s.persona?.name || "—"}（${s.persona?.id || "—"}）`],
    ["场景数量", `${n((s.scenes || []).length)} 个`],
    ["长期记忆", `${n(s.longterm_total)} 条`],
    ["面板地址", `${location.host || "127.0.0.1:8081"}`],
  ];
  const sig = JSON.stringify(rows);
  if (box.dataset.sig === sig) return;
  box.dataset.sig = sig;
  box.innerHTML = rows.map(([k, v]) => `<div class="kv-item"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
}

function recentCapacity() {
  const box = $("#recentChats");
  const card = box && box.closest(".card");
  if (!box || !card) return 5;
  const cs = getComputedStyle(card);
  const padTop = parseFloat(cs.paddingTop) || 0;
  const padBottom = parseFloat(cs.paddingBottom) || 0;
  const head = card.querySelector(".card-head")?.getBoundingClientRect().height || 0;
  const available = Math.max(86, card.clientHeight - padTop - padBottom - head - 6);
  const row = 86;
  const gap = 9;
  return Math.max(2, Math.min(40, Math.floor((available + gap) / (row + gap))));
}

function renderRecent() {
  const box = $("#recentChats");
  if (!box) return;
  const count = recentCapacity();
  S.recentVisible = count;
  const items = (S.recentItems || []).slice(-count).reverse();
  if (!items.length) {
    box.innerHTML = '<div class="empty-state chat-empty-recent">暂无对话记录，快开始对话吧！</div>';
    return;
  }
  box.innerHTML = items.map((r) => `
    <div class="chat-mini-item">
      <div class="chat-mini-meta"><span>${esc(r.scene === "group" ? `群 ${r.scene_id}` : `私聊 ${r.scene_id}`)} · ${esc(r.user_id)}${r.persona_name ? ` · ${esc(r.persona_name)}` : ""}</span><span>${esc(fmtTs(r.ts))}</span></div>
      <div class="chat-mini-user">${esc(r.user || "（空）")}</div>
      <div class="chat-mini-bot">${esc(r.bot || "（无文字回复）")}</div>
    </div>`).join("");
}

function observeRecent() {
  if (S.recentObserver || !window.ResizeObserver) return;
  const card = $("#recentChats")?.closest(".card");
  if (!card) return;
  let last = 0;
  S.recentObserver = new ResizeObserver(() => {
    const h = card.clientHeight;
    if (Math.abs(h - last) < 2) return;
    last = h;
    clearTimeout(S.recentResizeTimer);
    S.recentResizeTimer = setTimeout(renderRecent, 50);
  });
  S.recentObserver.observe(card);
}

async function loadRecent(opts = {}) {
  const box = $("#recentChats");
  if (!box) return;
  try {
    const items = await api("/api/chatlog?n=50");
    const next = Array.isArray(items) ? items : [];
    const sig = JSON.stringify(next);
    if (opts.silent && sig === S.recentSig) return;
    S.recentSig = sig;
    S.recentItems = next;
    renderRecent();
    observeRecent();
  } catch (e) {
    if (opts.silent) return;
    box.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

async function pollDashboard() {
  if (document.hidden) return;
  try {
    const data = await api("/api/status");
    const prevCurrent = S.current;
    S.status = data;
    S.personas = data.persona?.list || [];
    S.current = data.persona?.id || "";
    S.scenes = data.scenes || [];
    renderStats();
    renderTopPersona();
    renderRunInfo();
    if (prevCurrent !== S.current) {
      renderPersonaHero();
      renderScenes();
      fillSceneSelects();
    }
    const footer = $("#footerText");
    if (footer) {
      footer.textContent = `数据来自本机 · 更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    }
    if (S.tab === "dashboard") await loadRecent({ silent: true });
    if (S.tab === "chatlog" && document.activeElement !== $("#chatSearch")) await loadChatlog({ silent: true });
    if (S.tab === "affinity") await loadAffinity({ silent: true });
  } catch (e) {
    // 静默轮询失败不弹窗，下一次再试
  }
}

function startLiveUpdates() {
  if (S.liveTimer) return;
  S.liveTimer = setInterval(pollDashboard, 400);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) pollDashboard();
  });
}

function personaOptions(selected = "") {
  return S.personas.map((p) =>
    `<option value="${esc(p.id)}"${p.id === selected ? " selected" : ""}>${esc(p.name)}</option>`
  ).join("");
}

function renderScenes() {
  const box = $("#sceneList");
  if (!box) return;
  if (!S.scenes.length) {
    box.innerHTML = '<div class="empty-state">还没有聊过天的群/私聊。<br>可以在上方「绑定群」里先给一个群指定人设，或等机器人收到消息。</div>';
    return;
  }
  box.innerHTML = S.scenes.map((s) => {
    const key = sceneKey(s);
    const mood = Number(s.mood || 0);
    return `
      <div class="scene-row" data-scene="${esc(key)}">
        <div class="scene-main">
          <div class="scene-title">${esc(sceneLabel(s))}
            <span class="badge">${s.size ? `${n(s.size)} 条记忆` : "还没聊过"}</span>
            ${s.persona ? `<span class="badge purple">${esc(personaName(s.persona))}</span>` : ""}
          </div>
          <div class="scene-meta">
            <span>心情 <b class="badge ${moodClass(mood)}">${n(mood)} · ${esc(s.mood_label || "—")}</b></span>
            <span>摘要 ${n(s.summary_len)} 字</span>
            <span>长期记忆 ${n(s.longterm)} 条</span>
            <span>最后活跃 ${esc(timeAgo(s.last_active))}</span>
          </div>
        </div>
        <div class="scene-persona">
          <select data-act="persona" aria-label="${esc(sceneLabel(s))} 的人设">
            ${personaOptions(s.persona || S.current)}
          </select>
        </div>
        <div class="scene-actions">
          <button class="btn btn-ghost btn-sm" data-act="aff" type="button">好感度</button>
          <button class="btn btn-ghost btn-sm" data-act="logs" type="button">记录</button>
          <button class="btn btn-ghost btn-sm" data-act="mood" type="button">重置心情</button>
          ${s.persona ? '<button class="btn btn-danger btn-sm" data-act="delete-persona" type="button">删除人设</button>' : ""}
          <button class="btn btn-danger btn-sm" data-act="clear" type="button">清空记忆</button>
        </div>
      </div>`;
  }).join("");
}

async function setScenePersona(kind, sid, pid) {
  const name = pid ? personaName(pid) : "默认展示人设";
  if (pid && !await qaiConfirm(`把 ${kind === "group" ? "群" : "私聊"} ${sid} 切到「${name}」？该场景的对话记忆会保留。`, { title: "切换场景人设", okText: "切换" })) {
    renderScenes();
    return;
  }
  try {
    const r = await api("/api/scene_persona", { method: "POST", body: { scene: kind, id: sid, pid } });
    toast(`已更新：${name}（已载入该角色记忆）`);
    await loadStatus();
  } catch (e) {
    toast(e.message, "error");
    await loadStatus();
  }
}

function closeBindForm(reset = true) {
  const form = $("#bindForm");
  if (!form) return;
  form.classList.add("hidden");
  if (reset) {
    const input = $("#bindGroupId");
    if (input) input.value = "";
  }
}

function bindSceneEvents() {
  const box = $("#sceneList");
  box.addEventListener("change", (e) => {
    const sel = e.target.closest('select[data-act="persona"]');
    if (!sel) return;
    const key = sel.closest(".scene-row")?.dataset.scene || "";
    const [kind, sid] = key.split(":");
    if (kind && sid) setScenePersona(kind, sid, sel.value);
  });
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const key = btn.closest(".scene-row")?.dataset.scene || "";
    const [kind, sid] = key.split(":");
    if (!kind || !sid) return;
    const act = btn.dataset.act;
    if (act === "aff") {
      S.affScene = key;
      location.hash = "#affinity";
      activateTab("affinity");
      return;
    }
    if (act === "logs") {
      S.chatScene = key;
      location.hash = "#chatlog";
      activateTab("chatlog");
      return;
    }
    if (act === "mood") {
      try {
        await api("/api/reset_mood", { method: "POST", body: { id: sid } });
        toast("心情已重置");
        await loadStatus();
      } catch (err) { toast(err.message, "error"); }
      return;
    }
    if (act === "delete-persona") {
      if (!await qaiConfirm(`删除 ${sceneLabel({ scene: kind, id: sid })} 的独立人设「${personaName(s.persona)}」？\n删除后该场景会使用默认展示人设，不会删除人设文件本身。`, { title: "删除场景人设", okText: "删除", danger: true })) return;
      await setScenePersona(kind, sid, "");
      return;
    }
    if (act === "clear") {
      if (!await qaiConfirm(`确定清空 ${sceneLabel({ scene: kind, id: sid })} 的对话记忆？长期记忆不会删。`, { title: "清空对话记忆", okText: "清空", danger: true })) return;
      try {
        await api("/api/clear", { method: "POST", body: { scene: kind, id: sid } });
        toast("已清空该场景对话记忆");
        await loadStatus();
      } catch (err) { toast(err.message, "error"); }
    }
  });

  $("#bindGroupBtn").addEventListener("click", () => {
    const form = $("#bindForm");
    form.classList.toggle("hidden");
    if (!form.classList.contains("hidden")) {
      $("#bindPersona").innerHTML = personaOptions(S.current);
      $("#bindGroupId").focus();
    }
  });
  $("#bindCancel").addEventListener("click", () => closeBindForm(true));
  $("#bindSubmit").addEventListener("click", async () => {
    const gid = $("#bindGroupId").value.trim();
    if (!/^\d{5,12}$/.test(gid)) { toast("群号应为 5~12 位数字", "error"); return; }
    try {
      await api("/api/scene_persona", { method: "POST", body: { scene: "group", id: Number(gid), pid: $("#bindPersona").value } });
      closeBindForm(true);
      toast("群绑定已更新（已载入该角色记忆）");
      await loadStatus();
    } catch (e) { toast(e.message, "error"); }
  });

  $("#whitelistChips").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-remove-group]");
    if (!btn) return;
    const g = btn.dataset.removeGroup;
    const groups = (S.settings.allowed_groups || []).filter((x) => x !== g);
    saveWhitelist(groups);
  });
  $("#saveWhitelist").addEventListener("click", () => {
    const raw = $("#whitelistInput").value.trim();
    const groups = raw ? raw.split(/[,，\s]+/).filter(Boolean) : [];
    saveWhitelist(groups);
  });
}

async function saveWhitelist(groups) {
  try {
    const r = await api("/api/config/allowed_groups", { method: "POST", body: { groups } });
    S.settings.allowed_groups = r.groups || [];
    renderWhitelist();
    toast("白名单已保存，重启机器人后生效");
  } catch (e) {
    toast(e.message, "error");
  }
}

function renderWhitelist() {
  const groups = S.settings.allowed_groups || [];
  const state = $("#whitelistState");
  if (state) {
    state.textContent = groups.length ? `${groups.length} 个群` : "未设置（所有群）";
    state.className = `badge ${groups.length ? "green" : "warn"}`;
  }
  const chips = $("#whitelistChips");
  if (chips) {
    chips.innerHTML = groups.length
      ? groups.map((g) => `<span class="chip">${esc(g)}<button type="button" data-remove-group="${esc(g)}" aria-label="移除">×</button></span>`).join("")
      : '<span class="chip empty">未设置：所有群都会响应</span>';
  }
  const input = $("#whitelistInput");
  if (input) input.value = groups.join(",");
  const sum = $("#settingsWhitelist");
  if (sum) sum.textContent = groups.length ? groups.join("、") : "未设置（所有群都响应）";
}

/* ---------------- 人设库 ---------------- */
function renderPersonas() {
  const box = $("#personaGrid");
  if (!box) return;
  if (!S.personas.length) {
    box.innerHTML = '<div class="empty-state">还没有人设，点右上角「新增人物」创建第一套。</div>';
    return;
  }
  box.innerHTML = S.personas.map((p) => {
    const isCur = p.id === S.current;
    const chips = configChips(p.config).map((x) => `<span class="badge">${esc(x)}</span>`).join("");
    return `
      <div class="persona-card ${isCur ? "current" : ""}">
        <div class="persona-card-head">
          ${avatarMarkup(p, "persona-card-avatar")}
          <div style="min-width:0">
            <div class="persona-card-name">${esc(p.name)} ${isCur ? '<span class="badge green">默认展示</span>' : ""}</div>
            <div class="persona-card-id">${esc(p.id)}</div>
          </div>
        </div>
        <div class="persona-card-desc">${esc(p.desc || "暂无简介")}</div>
        <div class="persona-card-config">${chips || '<span class="badge">默认参数</span>'}</div>
        <div class="persona-card-actions">
          <button class="btn btn-ghost btn-sm" data-edit="${esc(p.id)}" type="button">编辑</button>
          <button class="btn btn-danger btn-sm" data-delete-persona="${esc(p.id)}" type="button">删除</button>
        </div>
      </div>`;
  }).join("");
}

async function deletePersona(pid) {
  const p = S.personas.find((x) => x.id === pid);
  if (!p) return;
  const isCur = pid === S.current;
  const msg = isCur
    ? `确定删除人设「${p.name}」？\n这是默认展示人设，删除后会自动切换到另一套人设；群/私聊里指向它的绑定也会被移除。\n\n人设正文、参数和头像会一起删除，聊天记录不会自动清空。`
    : `确定删除人设「${p.name}」？\n人设正文、参数和头像会一起删除；群/私聊里指向它的绑定也会被移除。\n\n聊天记录不会自动清空。`;
  if (!await qaiConfirm(msg, { title: "删除人设", okText: "删除", danger: true })) return;
  try {
    const r = await api("/api/personas/delete", { method: "POST", body: { id: pid } });
    toast(r.switched_to ? `人设已删除，已切换到 ${personaName(r.switched_to)}` : "人设已删除");
    await loadStatus();
    renderPersonas();
    renderScenes();
  } catch (e) {
    toast(e.message, "error");
  }
}

async function savePersonaQuickParams() {
  const pid = S.current;
  if (!pid) return;
  const params = {};
  $$("#personaHero [data-persona-param]").forEach((input) => {
    const key = input.dataset.personaParam;
    const raw = input.value.trim();
    if (!raw) { params[key] = null; return; }
    const num = Number(raw);
    if (!Number.isFinite(num)) throw new Error(`「${key}」不是有效数字`);
    params[key] = input.dataset.percent ? num / 100 : num;
  });
  try {
    await api("/api/persona/params", { method: "POST", body: { pid, params } });
    toast("人设展示参数已保存，下一轮生效");
    await loadStatus();
    renderPersonas();
  } catch (e) {
    toast(e.message, "error");
  }
}

/* ---------------- 人设弹窗 ---------------- */
const DEFAULT_CONFIG = {
  AI_MAX_CHARS: 100,
  AI_MSG_MAX_CHARS: 24,
  AI_MAX_MSGS: 5,
  AI_GROUP_AUTO_PROB: 0.5,
  AI_GROUP_AUTO_COOLDOWN: 10,
  AI_NAME_ALIASES: "新角色",
  AI_TYPE_CPS: 14,
  LLM_TEMPERATURE: 1.0,
};

const PERSONA_FORMAT_TEMPLATE = `你是「{名字}」，……（在这里写身份、来处，以及现在为什么在群里）。

【最高原则】
……（先像真人，再像这个角色；不要为了像角色而变成客服/说明书）

【说话铁律】
1. 默认一条 …… 个字，最多 …… 行。
2. 常用语气：……。
3. 禁止：Markdown、客服腔、总结、列清单、编造事实。
4. 标点和断句：……。

【群聊规则】
· 先判断别人是不是在跟你说话。
· 没人叫你时：……。
· 被 @ / 被引用时：……。
· 冷场时：……。

【你是谁】
……（性格、经历、在意的东西、害怕的东西）

【偏好与梗】
· 喜欢：……。
· 讨厌：……。
· 常用小动作/口癖：……。

【动作与能力】
· 需要引用时：在开头写 [引N]。
· 发表情包：调用 send_custom_sticker 工具。
· 戳一戳：调用 send_poke 工具，少用。
· 联网/长期记忆：按需调用对应工具。

【禁忌】
· 不编造身世、事实、数据、出处。
· 不装懂，不知道就说不知道。
· 不复述图片，不假装自己发了照片。

【语气示例】
对方：……
你：……
`;

function sublibOptionsHtml(selected = "") {
  const names = (S.stickerSublibs || []).map((x) => typeof x === "string" ? x : x.name).filter(Boolean);
  const opts = [`<option value="">整个表情库</option>`]
    .concat(names.map((name) => `<option value="${esc(name)}"${name === selected ? " selected" : ""}>${esc(name)}</option>`));
  if (selected && !names.includes(selected)) {
    opts.push(`<option value="${esc(selected)}" selected>${esc(selected)}（已不存在）</option>`);
  }
  return opts.join("");
}

function renderPersonaSublibOptions(selected) {
  const sel = $("#pConfigSublib");
  if (!sel) return;
  const cur = selected !== undefined ? selected : sel.value;
  sel.innerHTML = sublibOptionsHtml(cur || "");
  sel.value = cur || "";
}

async function loadStickerSublibs() {
  try {
    const data = await api("/api/sticker-sublibs");
    S.stickerSublibs = data.sublibs || [];
    if ($("#pConfigSublib")) renderPersonaSublibOptions();
  } catch (e) {
    // 子库接口失败不影响人设保存
  }
}

async function loadModels() {
  try {
    S.models = await api("/api/models");
  } catch (e) {
    S.models = { providers: [], current: {} };
  }
}

function modelPair(config) {
  const provider = String((config && config.LLM_PROVIDER) || "");
  const model = String((config && config.LLM_MODEL) || "");
  return provider && model ? `${provider}|${model}` : "";
}

function modelBase(pair) {
  if (!pair) return "";
  const [provider, modelId] = pair.split("|");
  const p = (S.models.providers || []).find((x) => x.id === provider);
  const m = p && (p.models || []).find((x) => x.id === modelId);
  return (m && m.base) || (p && p.base) || "";
}

function modelLabel(pair) {
  if (!pair) return "跟随默认";
  const [provider, modelId] = pair.split("|");
  const p = (S.models.providers || []).find((x) => x.id === provider);
  const m = p && (p.models || []).find((x) => x.id === modelId);
  return m ? `${p.name} · ${m.name}` : pair;
}

function updateModelHint() {
  const hint = $("#pModelHint");
  if (!hint) return;
  const pair = $("#pModel")?.value || "";
  if (!pair) {
    hint.textContent = "不单独选择时，跟随「模型配置」里的默认模型。";
    return;
  }
  const [provider] = pair.split("|");
  const p = (S.models.providers || []).find((x) => x.id === provider);
  if (p && !p.key_set) {
    hint.innerHTML = `「${esc(p.name)}」还没有配置 Key。`
      + `<button class="link-btn" type="button" data-open-keys="${esc(provider)}">去配置这个厂商的 Key</button>`;
    return;
  }
  hint.textContent = `这个角色将使用 ${modelLabel(pair)}。`;
}

/* 打开启动器的「各厂商 Key」面板。
   管理台在 8081 的 iframe 里，跨端口拿不到父页面，只能靠 postMessage
   让外壳去开；直接在浏览器里打开管理台时退化成新标签页。 */
function launcherUrl() {
  const port = (S.models && S.models.launcher_port) || 8082;
  return `http://127.0.0.1:${port}/`;
}

function openProviderKeys(provider) {
  const payload = { type: "qai:open-keys", provider: provider || "" };
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage(payload, "*");
      return;
    }
  } catch (e) { /* 拿不到 parent 就走新标签页 */ }
  window.open(`${launcherUrl()}?view=keys&provider=${encodeURIComponent(provider || "")}`, "_blank");
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-open-keys]");
  if (!btn) return;
  e.preventDefault();
  openProviderKeys(btn.dataset.openKeys);
});

function renderPersonaConfigFields(config) {
  const box = $("#pConfigFields");
  if (!box) return;
  const modelKeys = ["LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL"];
  const known = new Set([...PERSONA_QUICK_DEFS.map((d) => d.key), "AI_STICKER_SUBLIB", ...modelKeys]);
  const extras = {};
  Object.keys(config || {}).forEach((k) => { if (!known.has(k)) extras[k] = config[k]; });
  S.modal.configExtra = extras;
  // 模型选择用 <details> 做下拉：展开是浏览器原生行为（不依赖 JS），
  // 选项是普通 DOM，不会像原生 <select> 弹层那样「滚到边界就关闭」。
  box.innerHTML = `<div class="persona-config-field persona-model-field">
      <span>角色模型</span>
      <small>留空跟随默认；每个角色可以选不同厂商和模型</small>
      <details class="model-picker" id="pModelPicker">
        <summary class="model-picker-summary" id="pModelSummary">跟随默认</summary>
        <div class="model-picker-menu" id="pModelMenu"></div>
      </details>
      <input type="hidden" id="pModel" value="${esc(modelPair(config))}">
      <div class="hint" id="pModelHint"></div>
    </div>` + PERSONA_QUICK_DEFS.map((d) => {
    const raw = config ? config[d.key] : null;
    const val = raw == null || raw === "" ? "" : (d.type === "percent" ? Math.round(Number(raw) * 100) : raw);
    return `<label class="persona-config-field">
      <span>${esc(d.label)}${d.unit ? ` <em>${esc(d.unit)}</em>` : ""}</span>
      <small>${esc(d.desc)}</small>
      <input type="number" data-config-key="${esc(d.key)}" data-percent="${d.type === "percent" ? "1" : ""}" min="${d.min}" max="${d.max}" step="${d.step}" value="${esc(val)}" placeholder="跟随 .env">
    </label>`;
  }).join("") + `<label class="persona-config-field">
      <span>表情包子库</span>
      <small>留空 = 整个表情库；选择后这个角色只从这个子库挑表情</small>
      <select id="pConfigSublib">${sublibOptionsHtml(String((config && config.AI_STICKER_SUBLIB) || ""))}</select>
    </label>`;
  renderModelPicker(config);
  updateModelHint();
  loadStickerSublibs();
}

/* 角色模型下拉：<details> + 选项按钮。
   为什么要自建：原生 <select> 的弹层在「滚动到列表边界」时会被浏览器关闭，
   这个列表有 10 个模型 + 5 个分组标题，弹层必然要滚，所以必然复现。
   自建之后选项就是普通 DOM，滚动只会滚它自己，不存在被关闭的可能。 */
function renderModelPicker(config) {
  const menu = $("#pModelMenu");
  const summary = $("#pModelSummary");
  const picker = $("#pModelPicker");
  const hidden = $("#pModel");
  if (!menu || !summary || !hidden) return;

  const value = modelPair(config);
  const cur = S.models.current || {};
  const curText = cur.model ? `${cur.provider || "默认"} · ${cur.model}` : "模型配置里的默认模型";

  let html = `<button type="button" class="model-opt${value ? "" : " selected"}" data-value="">`
    + `<span>跟随默认</span><em>${esc(curText)}</em></button>`;
  for (const p of (S.models.providers || [])) {
    const note = p.key_set ? "" : " · 未配置 Key";
    html += `<div class="model-opt-group">${esc(p.name)}${note}</div>`;
    for (const m of (p.models || [])) {
      const pair = p.id + "|" + m.id;
      html += `<button type="button" class="model-opt${pair === value ? " selected" : ""}"`
        + ` data-value="${esc(pair)}"><span>${esc(m.name)}</span></button>`;
    }
  }
  menu.innerHTML = html;
  hidden.value = value;
  summary.textContent = value ? modelLabel(value) : "跟随默认";

  // 每次渲染都是新节点，直接绑（没有重复绑定问题）
  menu.onclick = (e) => {
    const opt = e.target.closest(".model-opt");
    if (!opt) return;
    e.preventDefault();
    const picked = opt.dataset.value || "";
    hidden.value = picked;
    menu.querySelectorAll(".model-opt").forEach((el) => {
      el.classList.toggle("selected", el === opt);
    });
    summary.textContent = picked ? modelLabel(picked) : "跟随默认";
    if (picker) picker.open = false;   // 选完自动收起
    updateModelHint();
  };
}

/* ---------------- 角色模型下拉 ----------------
   实现见 renderModelPicker()：用 <details> + 选项按钮，不用原生 <select>。
   原生控件的弹层在滚动到边界时会被浏览器关闭，而这个列表必然要滚。 */

function collectPersonaConfig() {
  const out = { ...(S.modal.configExtra || {}) };
  delete out.LLM_PROVIDER;
  delete out.LLM_MODEL;
  delete out.LLM_BASE_URL;
  const pair = $("#pModel")?.value || "";
  if (pair) {
    const [provider, modelId] = pair.split("|");
    out.LLM_PROVIDER = provider;
    out.LLM_MODEL = modelId;
    const base = modelBase(pair);
    if (base) out.LLM_BASE_URL = base;
  }
  for (const input of $$("#pConfigFields [data-config-key]")) {
    const key = input.dataset.configKey;
    const raw = input.value.trim();
    if (!raw) { delete out[key]; continue; }
    const num = Number(raw);
    if (!Number.isFinite(num)) throw new Error(`「${key}」不是有效数字`);
    out[key] = input.dataset.percent ? num / 100 : num;
  }
  const sublib = $("#pConfigSublib")?.value || "";
  if (sublib) out.AI_STICKER_SUBLIB = sublib;
  else delete out.AI_STICKER_SUBLIB;
  return out;
}

async function fillPersonaFormatTemplate() {
  const area = $("#pText");
  if (area.value.trim() && !await qaiConfirm("当前人设正文不为空，确定用空模板覆盖？", { title: "覆盖人设正文", okText: "覆盖" })) return;
  const name = $("#pName").value.trim() || "新人设";
  area.value = PERSONA_FORMAT_TEMPLATE.replace(/\{名字\}/g, name);
  area.focus();
  toast("格式模板已填入，请按角色补充内容");
}

function updateAvatarPreview() {
  const box = $("#pAvatarPreview");
  if (!box) return;
  const src = S.modal.avatarData || S.modal.avatarExisting || "";
  if (src) box.innerHTML = `<img src="${esc(src)}" alt="">`;
  else box.textContent = initials($("#pName")?.value.trim() || "?");
}

function openPersonaModal(mode = "create", pid = "") {
  S.modal = { mode, pid, avatarData: "", avatarExisting: "", avatarClear: false };
  const modal = $("#personaModal");
  const idEl = $("#pId");
  $("#personaModalTitle").textContent = mode === "create" ? "新增人物" : "编辑人设";
  $("#personaModalSub").textContent = mode === "create" ? "" : `正在编辑 ${pid}`;
  $("#pFileName").textContent = "未选择文件";
  $("#pFile").value = "";
  $("#pAvatar").value = "";
  idEl.disabled = mode === "edit";
  idEl.value = "";
  $("#pName").value = "";
  $("#pDesc").value = "";
  $("#pText").value = "";
  renderPersonaConfigFields(DEFAULT_CONFIG);
  updateAvatarPreview();
  modal.classList.remove("hidden");
  if (mode === "create") {
    setTimeout(() => idEl.focus(), 20);
  } else {
    $("#pSave").disabled = true;
    api(`/api/persona?pid=${encodeURIComponent(pid)}`)
      .then((data) => {
        $("#pId").value = data.id;
        $("#pName").value = data.name || "";
        $("#pDesc").value = data.desc || "";
        $("#pText").value = data.text || "";
        renderPersonaConfigFields(data.config || {});
        S.modal.avatarExisting = data.avatar || "";
        updateAvatarPreview();
        $("#pFileName").textContent = `已加载 ${data.id} 的人设正文（${(data.text || "").length} 字）`;
      })
      .catch((e) => toast(e.message, "error"))
      .finally(() => { $("#pSave").disabled = false; });
  }
}

function closePersonaModal() {
  $("#personaModal").classList.add("hidden");
}

function slugId(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "").slice(0, 24);
}

async function savePersonaModal() {
  const mode = S.modal.mode;
  const pid = $("#pId").value.trim();
  const name = $("#pName").value.trim();
  const desc = $("#pDesc").value.trim();
  const text = $("#pText").value;
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(pid)) { toast("ID 只能包含字母/数字/下划线/短横线，1~32 位", "error"); return; }
  if (!text.trim()) { toast("人设正文不能为空", "error"); return; }
  let config;
  try {
    config = collectPersonaConfig();
  } catch (e) {
    toast(e.message, "error");
    return;
  }
  try {
    await withBusy($("#pSave"), async () => {
      if (mode === "create") {
        await api("/api/personas/create", {
          method: "POST",
          body: { id: pid, name, desc, text, config, avatar: S.modal.avatarData || "" },
        });
        toast("人设已创建");
      } else {
        await api("/api/persona/meta", {
          method: "POST",
          body: {
            pid, name, desc, config,
            avatar: S.modal.avatarData || undefined,
            avatar_clear: !!S.modal.avatarClear,
          },
        });
        await api("/api/persona", { method: "POST", body: { pid, text } });
        toast("人设已保存");
      }
    });
    closePersonaModal();
    await loadStatus();
    renderPersonas();
    renderScenes();
    renderPersonaHero();
  } catch (e) {
    toast(e.message, "error");
  }
}

function bindPersonaModal() {
  $("#createPersonaBtn").addEventListener("click", () => openPersonaModal("create"));
  $("#closePersonaModal").addEventListener("click", closePersonaModal);
  $("#pCancel").addEventListener("click", closePersonaModal);
  $("#personaModal").addEventListener("click", (e) => {
    if (e.target === $("#personaModal")) closePersonaModal();
  });
  $("#pSave").addEventListener("click", savePersonaModal);
  $("#pModel")?.addEventListener("change", updateModelHint);
  $("#pConfigFields").addEventListener("change", (e) => {
    if (e.target && e.target.id === "pModel") updateModelHint();
  });
  $("#pTemplateFormat").addEventListener("click", fillPersonaFormatTemplate);
  $("#pName").addEventListener("input", () => {
    if (!S.modal.avatarData && !S.modal.avatarExisting) updateAvatarPreview();
  });
  $("#pAvatar").addEventListener("change", async (e) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    if (!/^image\/(png|jpe?g|webp|gif)$/i.test(f.type)) {
      toast("头像只支持 PNG/JPG/WebP/GIF", "error");
      return;
    }
    if (f.size > 5 * 1024 * 1024) {
      toast("头像不能超过 5MB", "error");
      return;
    }
    try {
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("读取头像失败"));
        reader.readAsDataURL(f);
      });
      S.modal.avatarData = data;
      S.modal.avatarClear = false;
      updateAvatarPreview();
      toast("头像已选择");
    } catch (err) { toast(err.message, "error"); }
  });
  $("#pAvatarClear").addEventListener("click", () => {
    S.modal.avatarData = "";
    S.modal.avatarExisting = "";
    S.modal.avatarClear = true;
    updateAvatarPreview();
  });
  $("#pFile").addEventListener("change", async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    $("#pFileName").textContent = f.name;
    const text = await f.text();
    if (/\.json$/i.test(f.name)) {
      try {
        const obj = JSON.parse(text);
        if (obj && typeof obj === "object" && !Array.isArray(obj)) {
          if (obj.id && !$("#pId").value) $("#pId").value = String(obj.id);
          if (obj.name && !$("#pName").value) $("#pName").value = String(obj.name);
          if (obj.desc && !$("#pDesc").value) $("#pDesc").value = String(obj.desc);
          const body = obj.persona ?? obj.text ?? obj.persona_text;
          if (typeof body === "string" && body.trim()) $("#pText").value = body;
          if (obj.config && typeof obj.config === "object") {
            renderPersonaConfigFields(obj.config);
          } else if (!("persona" in obj) && !("text" in obj) && !("config" in obj)) {
            renderPersonaConfigFields(obj);
          }
          toast("已从 JSON 填充表单");
        }
      } catch {
        $("#pText").value = text;
        toast("JSON 解析失败，已按纯文本导入", "error");
      }
    } else {
      $("#pText").value = text;
      if (!$("#pName").value) $("#pName").value = f.name.replace(/\.[^.]+$/, "");
      if (!$("#pId").value) $("#pId").value = slugId(f.name.replace(/\.[^.]+$/, ""));
      toast("已读入人物性格文件");
    }
  });
  $("#personaGrid").addEventListener("click", (e) => {
    const edit = e.target.closest("button[data-edit]");
    if (edit) { openPersonaModal("edit", edit.dataset.edit); return; }
    const del = e.target.closest("button[data-delete-persona]");
    if (del) deletePersona(del.dataset.deletePersona);
  });
  $("#personaHero").addEventListener("click", (e) => {
    if (e.target.closest("#savePersonaQuick")) savePersonaQuickParams();
    if (e.target.closest("#editGlobalBtn")) {
      const cur = S.personas.find((p) => p.id === S.current);
      if (cur) openPersonaModal("edit", cur.id);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#personaModal").classList.contains("hidden")) closePersonaModal();
  });
}

/* ---------------- 场景下拉 ---------------- */
function fillSceneSelects() {
  syncSceneSelect("chat");
  syncSceneSelect("aff");
}

function syncSceneSelect(which) {
  const sel = which === "chat" ? $("#chatScene") : $("#affScene");
  if (!sel) return;
  const all = which === "chat" ? '<option value="">全部场景</option>' : "";
  const preferred = which === "chat" ? S.chatScene : S.affScene;
  const current = preferred || sel.value || "";
  const opts = S.scenes.map((s) => {
    const key = sceneKey(s);
    return `<option value="${esc(key)}">${esc(sceneLabel(s))}${s.persona ? ` · ${esc(personaName(s.persona))}` : ""}</option>`;
  }).join("");
  sel.innerHTML = all + opts;
  if (current && S.scenes.some((s) => sceneKey(s) === current)) {
    sel.value = current;
  } else if (which === "aff") {
    const firstGroup = S.scenes.find((s) => s.scene === "group") || S.scenes[0];
    if (firstGroup) sel.value = sceneKey(firstGroup);
  }
  if (which === "chat") S.chatScene = sel.value;
  if (which === "aff") S.affScene = sel.value;
}

/* ---------------- 聊天记录 ---------------- */
function chatMediaMarkup(media) {
  if (!Array.isArray(media) || !media.length) return "";
  const items = media.map((m) => {
    if (!m || typeof m !== "object") return "";
    let src = "";
    if (typeof m.url === "string" && /^https?:\/\//i.test(m.url)) src = m.url;
    else if (typeof m.file === "string" && m.file) src = `/sticker-img/${encodeURIComponent(m.file)}`;
    if (!src) return "";
    const label = m.kind === "sticker" ? "表情" : "图片";
    return `<a class="chat-media-item" href="${esc(src)}" target="_blank" rel="noreferrer" title="${label}"><img loading="lazy" src="${esc(src)}" alt="${label}" onerror="this.parentElement.style.display='none'"></a>`;
  }).join("");
  return items ? `<div class="chat-media">${items}</div>` : "";
}

async function loadChatlog(opts = {}) {
  const box = $("#chatList");
  if (!box) return;
  const scene = $("#chatScene")?.value ?? "";
  const q = $("#chatSearch")?.value.trim() ?? "";
  const n = $("#chatLimit")?.value ?? "200";
  S.chatScene = scene;
  if (!opts.silent) box.innerHTML = '<div class="empty-state"><span class="spinner"></span> 加载中…</div>';
  try {
    const items = await api(`/api/chatlog?n=${encodeURIComponent(n)}&scene=${encodeURIComponent(scene)}&q=${encodeURIComponent(q)}`);
    const sig = JSON.stringify({ scene, q, n, items });
    if (opts.silent && sig === S.chatSig) return;
    S.chatSig = sig;
    if (!Array.isArray(items) || !items.length) {
      box.innerHTML = '<div class="chat-empty">没有匹配的聊天记录。<br>换个场景或关键词试试。</div>';
      return;
    }
    box.innerHTML = items.slice().reverse().map((r) => `
      <div class="chat-entry">
        <div class="chat-entry-head">
          <span>${esc(r.scene === "group" ? `群 ${r.scene_id}` : `私聊 ${r.scene_id}`)} · ${esc(r.user_id)}${r.persona_name ? ` · 人设 ${esc(r.persona_name)}` : ""}</span>
          <span>${esc(fmtTs(r.ts))}</span>
        </div>
        <div class="chat-entry-user">${esc(r.user || "（空）")}</div>
        ${chatMediaMarkup(r.media)}
        <div class="chat-entry-bot">${esc(r.bot || "（无文字回复）")}</div>
        ${chatMediaMarkup(r.bot_media)}
      </div>`).join("");
  } catch (e) {
    if (opts.silent) return;
    box.innerHTML = `<div class="chat-empty">${esc(e.message)}</div>`;
  }
}

function bindChatlog() {
  $("#chatRefresh").addEventListener("click", loadChatlog);
  $("#chatlogFolderBtn").addEventListener("click", async () => {
    try {
      await api("/api/chatlog/open-folder", { method: "POST", body: {} });
      toast("已打开记录文件夹");
    } catch (e) { toast(e.message, "error"); }
  });
  $("#chatScene").addEventListener("change", () => { S.chatScene = $("#chatScene").value; loadChatlog(); });
  $("#chatLimit").addEventListener("change", loadChatlog);
  $("#chatSearch").addEventListener("keydown", (e) => { if (e.key === "Enter") loadChatlog(); });
}

/* ---------------- 好感度 ---------------- */
async function loadAffinity(opts = {}) {
  const box = $("#affList");
  const sum = $("#affSummary");
  if (!box) return;
  const scene = $("#affScene")?.value || S.affScene;
  if (!scene) {
    box.innerHTML = '<div class="empty-state">还没有场景，先让机器人在群里聊几句。</div>';
    if (sum) sum.innerHTML = "";
    return;
  }
  S.affScene = scene;
  if (!opts.silent) box.innerHTML = '<div class="empty-state"><span class="spinner"></span> 加载中…</div>';
  try {
    const data = await api(`/api/affinity?scene=${encodeURIComponent(scene)}`);
    const items = data.items || [];
    const sig = JSON.stringify({ scene, items });
    if (opts.silent && sig === S.affSig) return;
    S.affSig = sig;
    if (!items.length) {
      if (sum) sum.innerHTML = '<span class="badge">暂无好感度记录</span>';
      box.innerHTML = '<div class="empty-state">这个场景还没有人跟机器人聊过。</div>';
      return;
    }
    const avg = items.reduce((a, b) => a + Number(b.score || 0), 0) / items.length;
    const maxScore = Math.max(1, ...items.map((x) => Math.max(0, Number(x.score || 0))));
    if (sum) sum.innerHTML = `
      <span class="badge purple">${items.length} 位群友</span>
      <span class="badge green">最高 ${n(Math.max(...items.map((x) => Number(x.score || 0))))}</span>
      <span class="badge">平均 ${avg.toFixed(1)}</span>`;
    box.innerHTML = items.map((x, i) => {
      const score = Number(x.score || 0);
      const width = score >= 0
        ? Math.max(6, Math.min(100, (score / maxScore) * 100))
        : Math.max(2, Math.min(100, (Math.abs(score) / 20) * 100));
      return `
        <div class="aff-row">
          <div class="aff-rank">#${i + 1}</div>
          <div>
            <div class="aff-name">${esc(x.nick || x.user_id)}</div>
            <div class="aff-uid">${esc(x.user_id)} · ${esc(timeAgo(x.last_chat))}</div>
          </div>
          <div class="aff-bar-wrap"><div class="aff-bar ${score < 0 ? "neg" : ""}" style="width:${width}%"></div></div>
          <div class="aff-score ${score < 0 ? "neg" : ""}">${score > 0 ? "+" : ""}${score}</div>
        </div>`;
    }).join("");
  } catch (e) {
    if (opts.silent) return;
    box.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

function bindAffinity() {
  $("#affScene").addEventListener("change", () => { S.affScene = $("#affScene").value; loadAffinity(); });
}

/* ---------------- 表情库 ---------------- */
function stickerSublibNames() {
  return (S.stickerSublibs || []).map((x) => typeof x === "string" ? x : x.name).filter(Boolean);
}

function stickerSublibChips(it) {
  const selected = new Set(it.sublibs || []);
  const names = stickerSublibNames();
  if (!names.length) return '<div class="sticker-sublib-select"><span class="muted tiny">子库：未分组</span></div>';
  return `<div class="sticker-sublib-select"><span class="sublib-label">子库</span>${names.map((name) =>
    `<button type="button" class="sublib-toggle ${selected.has(name) ? "active" : ""}" data-sublib-toggle="${esc(name)}">${esc(name)}</button>`
  ).join("")}</div>`;
}

function renderStickerSublibUI() {
  const list = $("#stickerSublibList");
  if (list) {
    list.innerHTML = S.stickerSublibs.length
      ? S.stickerSublibs.map((x) => {
          const name = typeof x === "string" ? x : x.name;
          const count = typeof x === "string" ? 0 : x.count;
          return `<span class="sublib-chip"><b>${esc(name)}</b><span>${n(count)}</span><button type="button" data-del-sublib="${esc(name)}" title="删除子库">×</button></span>`;
        }).join("")
      : '<span class="muted tiny">还没有子库；创建后可以把表情分进去</span>';
  }
  const bar = $("#stickerFilterBar");
  if (!bar) return;
  const noneCount = S.stickerItems.filter((it) => !(it.sublibs || []).length).length;
  const chips = [
    `<button type="button" class="sticker-filter ${S.stickerFilter === "all" ? "active" : ""}" data-filter="all">全部 ${n(S.stickerItems.length)}</button>`,
    `<button type="button" class="sticker-filter ${S.stickerFilter === "__none__" ? "active" : ""}" data-filter="__none__">未分组 ${n(noneCount)}</button>`,
    ...S.stickerSublibs.map((x) => {
      const name = typeof x === "string" ? x : x.name;
      const count = typeof x === "string" ? 0 : x.count;
      return `<button type="button" class="sticker-filter ${S.stickerFilter === name ? "active" : ""}" data-filter="${esc(name)}">${esc(name)} ${n(count)}</button>`;
    }),
  ];
  bar.innerHTML = chips.join("");
}

async function loadStickers() {
  const grid = $("#stickerGrid");
  if (!grid) return;
  grid.innerHTML = '<div class="empty-state"><span class="spinner"></span> 加载中…</div>';
  try {
    const data = await api("/api/stickers");
    S.stickerItems = data.items || [];
    S.stickerSublibs = data.sublibs || [];
    const names = stickerSublibNames();
    if (S.stickerFilter !== "all" && S.stickerFilter !== "__none__" && !names.includes(S.stickerFilter)) S.stickerFilter = "all";
    $("#stickerMeta").textContent = `共 ${n(data.count)} 张`;
    $("#stickerCatalog").textContent = data.catalog ? `标签概览：${data.catalog}` : "";
    renderStickerSublibUI();
    if (!S.stickerItems.length) {
      grid.innerHTML = '<div class="empty-state">还没有表情。点「导入表情」或先在机器人 QQ 上收藏表情，再在聊天里发 <code>/建表情库</code>。</div>';
      return;
    }
    const items = S.stickerItems.filter((it) => {
      if (S.stickerFilter === "all") return true;
      if (S.stickerFilter === "__none__") return !(it.sublibs || []).length;
      return (it.sublibs || []).includes(S.stickerFilter);
    });
    if (!items.length) {
      grid.innerHTML = '<div class="empty-state">这个子库还没有表情；用上方「导入表情」或把其他表情改到该子库。</div>';
      return;
    }
    grid.innerHTML = items.map((it) => `
      <div class="sticker-item" data-file="${esc(it.file)}">
        <div class="sticker-thumb"><img loading="lazy" src="/sticker-img/${encodeURIComponent(it.file)}" alt=""></div>
        <div class="sticker-file">${esc(it.file)}</div>
        <div class="sticker-tags"><input data-tags value="${esc((it.tags || []).join("，"))}" placeholder="标签，用逗号分隔"></div>
        ${stickerSublibChips(it)}
        <div class="sticker-actions">
          <button class="btn btn-primary btn-sm sticker-save" data-save type="button">保存标签</button>
          <button class="btn btn-ghost btn-sm sticker-del" data-delete type="button">删除</button>
        </div>
      </div>`).join("");
  } catch (e) {
    grid.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

function bindStickers() {
  $("#stickerGrid").addEventListener("click", async (e) => {
    const tog = e.target.closest("button[data-sublib-toggle]");
    if (tog) {
      const item = tog.closest(".sticker-item");
      const file = item?.dataset.file;
      if (!file) return;
      const names = [...item.querySelectorAll("button[data-sublib-toggle].active")].map((x) => x.dataset.sublibToggle);
      const name = tog.dataset.sublibToggle;
      const next = names.includes(name) ? names.filter((x) => x !== name) : [...names, name];
      try {
        await api("/api/stickers/sublib", { method: "POST", body: { file, sublibs: next } });
        toast(next.length ? `已更新子库：${next.join("、")}` : "已移到未分组");
        await loadStickers();
      } catch (err) { toast(err.message, "error"); }
      return;
    }
    const btn = e.target.closest("button[data-save]");
    if (btn) {
      const item = btn.closest(".sticker-item");
      const file = item?.dataset.file;
      const input = item?.querySelector("input[data-tags]");
      if (!file || !input) return;
      try {
        await api("/api/stickers", { method: "POST", body: { file, tags: input.value } });
        toast("标签已保存");
      } catch (err) { toast(err.message, "error"); }
      return;
    }
    const del = e.target.closest("button[data-delete]");
    if (del) {
      const item = del.closest(".sticker-item");
      const file = item?.dataset.file;
      if (!file) return;
      if (!await qaiConfirm(`删除表情「${file}」？本地文件也会一起删除。`, { title: "删除表情", okText: "删除", danger: true })) return;
      try {
        await withBusy(del, () => api("/api/stickers/delete", { method: "POST", body: { file } }));
        toast("表情已删除");
        await loadStickers();
      } catch (err) { toast(err.message, "error"); }
    }
  });

  $("#stickerFilterBar").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-filter]");
    if (!btn) return;
    S.stickerFilter = btn.dataset.filter || "all";
    loadStickers();
  });
  $("#stickerSublibAdd").addEventListener("click", async () => {
    const input = $("#stickerSublibName");
    const name = input.value.trim();
    if (!name) { toast("先填子库名称", "error"); return; }
    try {
      await api("/api/stickers/sublib/create", { method: "POST", body: { name } });
      input.value = "";
      toast(`子库「${name}」已创建`);
      await loadStickers();
    } catch (err) { toast(err.message, "error"); }
  });
  $("#stickerSublibList").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-del-sublib]");
    if (!btn) return;
    const name = btn.dataset.delSublib;
    if (!await qaiConfirm(`删除子库「${name}」？\n子库里的表情会保留，但会变成「未分组」。`, { title: "删除表情子库", okText: "删除", danger: true })) return;
    try {
      const r = await api("/api/stickers/sublib/delete", { method: "POST", body: { name } });
      if (S.stickerFilter === name) S.stickerFilter = "all";
      toast(`子库已删除${r.moved ? `，${r.moved} 张表情回到未分组` : ""}`);
      await loadStickers();
    } catch (err) { toast(err.message, "error"); }
  });

  $("#stickerImportBtn").addEventListener("click", () => $("#stickerImportInput").click());
  $("#stickerFolderBtn").addEventListener("click", async () => {
    try {
      await api("/api/stickers/open-folder", { method: "POST", body: {} });
      toast("已打开表情文件夹");
    } catch (e) { toast(e.message, "error"); }
  });
  $("#stickerImportInput").addEventListener("change", async (e) => {
    const files = [...(e.target.files || [])];
    e.target.value = "";
    if (!files.length) return;
    let ok = 0;
    const warnings = [];
    const targetSublib = S.stickerFilter !== "all" && S.stickerFilter !== "__none__" ? S.stickerFilter : "";
    for (const file of files) {
      if (file.size > 8 * 1024 * 1024) {
        toast(`${file.name} 超过 8MB，已跳过`, "error");
        continue;
      }
      try {
        const data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = () => reject(new Error("读取文件失败"));
          reader.readAsDataURL(file);
        });
        const r = await api("/api/stickers/import", { method: "POST", body: { name: file.name, data, sublibs: targetSublib ? [targetSublib] : [] } });
        if (r.item?.warning) warnings.push(`${file.name}：${r.item.warning}`);
        ok += 1;
      } catch (err) {
        toast(`${file.name} 导入失败：${err.message}`, "error");
      }
    }
    if (ok) {
      toast(`已导入 ${ok} 张表情`);
      await loadStickers();
    }
    if (warnings.length) toast(warnings.join("；"), "error");
  });
}

/* ---------------- 设置 ---------------- */
async function loadSettings() {
  try {
    const data = await api("/api/settings");
    S.settings = data;
    renderWhitelist();
    const admin = $("#adminInput");
    if (admin) admin.value = (data.bot_admin_qq || []).join(",");
  } catch (e) {
    toast(e.message, "error");
  }
}

async function loadEnvParams() {
  const box = $("#envParamGroups");
  if (!box) return;
  try {
    const data = await api("/api/env/params");
    renderEnvParams(data.groups || []);
    const hint = $("#envModelHint");
    if (hint) {
      const f = data.features || {};
      const hidden = [];
      if (!f.image) hidden.push("图像");
      if (!f.tools) hidden.push("工具");
      hint.textContent = data.model
        ? `当前 ${data.model}${hidden.length ? ` · 已隐藏${hidden.join("/")}参数` : ""}`
        : "";
    }
  } catch (e) {
    box.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
  }
}

function renderEnvParams(groups) {
  const box = $("#envParamGroups");
  if (!box) return;
  box.innerHTML = groups.map((group) => `
    <div class="env-param-group">
      <div class="env-param-title">${esc(group.title)}</div>
      <div class="env-param-fields">
        ${(group.items || []).map((it) => {
          const isBool = it.type === "bool";
          const on = ["1", "true", "on", "yes"].includes(String(it.value).toLowerCase());
          if (isBool) {
            return `<div class="env-param-field env-param-bool">
              <span class="env-param-meta"><b>${esc(it.label)}</b><small>${esc(it.desc || "")}</small></span>
              <label class="switch"><input type="checkbox" data-env-key="${esc(it.key)}" data-env-type="bool" ${on ? "checked" : ""}><i></i></label>
            </div>`;
          }
          const value = it.type === "percent" ? String(Math.round(Number(it.value) * 100)) : it.value;
          const step = it.type === "int" ? "1" : "0.05";
          return `<label class="env-param-field">
            <span class="env-param-meta"><b>${esc(it.label)}</b><small>${esc(it.desc || "")}</small></span>
            <span class="env-param-input">
              <input type="number" data-env-key="${esc(it.key)}" data-env-type="${esc(it.type)}" min="${it.min}" max="${it.max}" step="${step}" value="${esc(value)}">
              <em>${esc(it.unit || "")}</em>
            </span>
          </label>`;
        }).join("")}
      </div>
    </div>`).join("");
}

async function saveEnvParams() {
  const btn = $("#saveEnvParams");
  const updates = {};
  $$("#envParamGroups [data-env-key]").forEach((el) => {
    updates[el.dataset.envKey] = el.type === "checkbox" ? el.checked : el.value.trim();
  });
  if (!Object.keys(updates).length) return;
  try {
    await withBusy(btn, async () => {
      const r = await api("/api/env/params", { method: "POST", body: { updates } });
      renderEnvParams(r.groups || []);
      toast("常用参数已保存，下一轮生效");
    });
  } catch (e) {
    toast(e.message, "error");
  }
}

async function loadConfig() {
  const pre = $("#configView");
  if (!pre) return;
  try {
    const rows = await api("/api/config");
    pre.textContent = (rows || []).map((c) => c.masked ? `${c.key}=***（已隐藏）` : `${c.key}=${c.value}`).join("\n");
  } catch (e) {
    pre.textContent = e.message;
  }
}

function bindSettings() {
  $("#saveAdmin").addEventListener("click", async () => {
    const raw = $("#adminInput").value.trim();
    const admins = raw ? raw.split(/[,，\s]+/).filter(Boolean) : [];
    try {
      const r = await api("/api/config/admin", { method: "POST", body: { admins } });
      S.settings.bot_admin_qq = r.admins || [];
      toast("管理员已保存，重启机器人后生效");
    } catch (e) { toast(e.message, "error"); }
  });
  $("#reloadConfig").addEventListener("click", loadConfig);
  $("#saveEnvParams").addEventListener("click", saveEnvParams);
}

let midScroll = null;

function bindMiddleScroll() {
  document.addEventListener("pointerdown", (e) => {
    if (e.button !== 1) return;
    const box = e.target.closest(".persona-quick-scroll");
    if (!box) return;
    e.preventDefault();
    midScroll = { box, y: e.clientY, top: box.scrollTop };
    box.classList.add("mid-drag");
  }, { passive: false });
  document.addEventListener("pointermove", (e) => {
    if (!midScroll) return;
    e.preventDefault();
    midScroll.box.scrollTop = midScroll.top - (e.clientY - midScroll.y);
  }, { passive: false });
  const end = () => {
    if (!midScroll) return;
    midScroll.box.classList.remove("mid-drag");
    midScroll = null;
  };
  document.addEventListener("pointerup", end);
  document.addEventListener("pointercancel", end);
  document.addEventListener("auxclick", (e) => {
    if (e.button === 1 && e.target.closest(".persona-quick-scroll")) e.preventDefault();
  });
}

/* ---------------- 新手向导（已下线） ----------------
   悬浮向导现在只由启动器外壳 launcher_ui 提供，避免两处实现行为不一致。
   webpanel 里对应的 DOM 已删除，下面这段 initGuide 会因为找不到元素直接 return，
   保留它只是为了不改动旧的 initGuide() 调用点；下次清理时整段删掉即可。
------------------------------------------------ */
function renderGuideMarkdown(md) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let list = "";
  let code = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = ""; } };
  const closeCode = () => {
    if (code) { out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`); code = null; }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("```")) {
      closeList();
      if (code) closeCode(); else code = [];
      continue;
    }
    if (code) { code.push(raw); continue; }
    if (!line) { closeList(); continue; }
    const h = line.match(/^(#{1,4})\s+(.+)$/);
    if (h) {
      closeList();
      out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);
      continue;
    }
    if (/^---+$/.test(line)) { closeList(); out.push("<hr>"); continue; }
    if (line.startsWith(">")) {
      closeList();
      out.push(`<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`);
      continue;
    }
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
  closeCode();
  return out.join("");
}

function initGuide() {
  const orb = $("#guideOrb");
  const menu = $("#guideMenu");
  const modal = $("#guideTutorialModal");
  const body = $("#guideTutorialBody");
  if (!orb || !menu || !modal || !body) return;

  const POS_KEY = "qai-guide-pos";
  const VISIBLE_KEY = "qai-guide-visible";
  let drag = null;
  let lastDragAt = 0;

  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
  const size = () => orb.offsetWidth || 62;

  function readVisible() {
    try { return localStorage.getItem(VISIBLE_KEY) === "1"; } catch (e) { return false; }
  }
  function saveVisible(v) {
    try { localStorage.setItem(VISIBLE_KEY, v ? "1" : "0"); } catch (e) {}
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
  }
  function openGuideMenu() {
    menu.classList.remove("hidden");
    const r = orb.getBoundingClientRect();
    const m = menu.getBoundingClientRect();
    const below = r.top - m.height - 12 < 8;
    menu.classList.toggle("below", below);
    menu.style.left = `${clamp(r.left + r.width / 2 - m.width / 2, 8, window.innerWidth - m.width - 8)}px`;
    menu.style.top = `${below ? r.bottom + 12 : r.top - m.height - 12}px`;
  }
  async function openGuideTutorial() {
    hideGuideMenu();
    modal.classList.remove("hidden");
    body.innerHTML = '<div class="empty-state">正在加载教程…</div>';
    try {
      const r = await api("/api/tutorial");
      body.innerHTML = renderGuideMarkdown(r.text || "");
      body.scrollTop = 0;
    } catch (e) {
      body.innerHTML = `<div class="empty-state">教程读取失败：${esc(e.message)}</div>`;
    }
  }
  function closeGuideTutorial() {
    modal.classList.add("hidden");
  }
  function closeGuideForever() {
    hideGuideMenu();
    saveVisible(false);
    orb.classList.add("fading");
    setTimeout(() => {
      orb.classList.add("hidden");
      orb.classList.remove("fading");
    }, 320);
    toast("新手向导已关闭，可在「主题与布局 → 唤醒向导」重新开启");
  }

  if (readVisible()) requestAnimationFrame(showGuide);

  $("#wakeGuide")?.addEventListener("click", () => {
    saveVisible(true);
    showGuide();
    $("#appearanceModal")?.classList.add("hidden");
    toast("新手向导已唤醒");
  });

  orb.addEventListener("click", (e) => {
    if (e.button !== 0) return;
    if (Date.now() - lastDragAt < 280) return;
    openGuideTutorial();
  });
  orb.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openGuideTutorial(); }
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
  $("#guideNo")?.addEventListener("click", hideGuideMenu);
  $("#guideYes")?.addEventListener("click", closeGuideForever);
  $("#guideTutorialClose")?.addEventListener("click", closeGuideTutorial);
  $("#guideTutorialDone")?.addEventListener("click", closeGuideTutorial);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeGuideTutorial(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!modal.classList.contains("hidden")) closeGuideTutorial();
      if (!menu.classList.contains("hidden")) hideGuideMenu();
    }
  });
}

/* ---------------- 启动 ---------------- */
function bindGlobal() {
  window.addEventListener("hashchange", () => activateTab(location.hash.slice(1) || "dashboard"));
  $("#refreshBtn").addEventListener("click", async () => {
    try {
      await withBusy($("#refreshBtn"), async () => {
        await loadStatus();
        await loadRecent();
        if (S.tab === "chatlog") await loadChatlog();
        if (S.tab === "affinity") await loadAffinity();
        if (S.tab === "stickers") await loadStickers();
        toast("已刷新");
      });
    } catch (e) { toast(e.message, "error"); }
  });
  $$("[data-goto]").forEach((el) => el.addEventListener("click", () => { location.hash = `#${el.dataset.goto}`; }));
  bindSceneEvents();
  bindPersonaModal();
  bindChatlog();
  bindAffinity();
  bindStickers();
  bindSettings();
  bindCardLayout();
  bindCardResize();
  bindMiddleScroll();
}

document.addEventListener("DOMContentLoaded", async () => {
  bindGlobal();
  initGuide();
  await loadModels().catch(() => {});
  try {
    await loadStatus();
  } catch (e) {
    $("#connBadge")?.classList.add("offline");
    toast(e.message, "error");
  }
  await loadSettings().catch(() => {});
  await loadRecent().catch(() => {});
  activateTab(location.hash.slice(1) || "dashboard", { keepHash: true });
  startLiveUpdates();
});
