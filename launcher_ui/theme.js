/* QAI theme runtime: themes, background color, motion toggles, frame sync */
(function () {
  "use strict";

  const THEME_KEY = "qai-theme";
  const PREF_KEYS = {
    jiggle: "qai-jiggle",
    bgFollow: "qai-bg-follow",
    cardLayout: "qai-card-layout",
    bg: "qai-bg",
    glowSize: "qai-glow-size",
    glowColor: "qai-glow-color",
    glowShape: "qai-glow-shape",
  };
  const THEMES = { blue: "幽静蓝", purple: "炫酷紫", white: "清爽白", gray: "沉稳灰" };
  const META = { blue: "#06131d", purple: "#0a0c12", white: "#f4f7fb", gray: "#0b0d10" };
  const GLOW_COLORS = { blue: "#4da3ff", purple: "#a78bfa", white: "#2563eb", gray: "#94a3b8" };
  const GLOW_SHAPES = ["soft", "wide", "leaf", "ring"];
  const root = document.documentElement;
  const embedded = window.parent && window.parent !== window;
  let current = "blue";
  let washTimer = 0;

  function valid(theme) {
    return Object.prototype.hasOwnProperty.call(THEMES, theme);
  }

  function read(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }

  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }

  function storedTheme() {
    try {
      const q = new URLSearchParams(location.search).get("theme");
      if (valid(q)) return q;
      const s = localStorage.getItem(THEME_KEY);
      if (valid(s)) return s;
    } catch (e) {}
    return valid(root.dataset.theme) ? root.dataset.theme : "blue";
  }

  function getPrefs() {
    return {
      jiggle: read(PREF_KEYS.jiggle, "on") !== "off",
      bgFollow: read(PREF_KEYS.bgFollow, "on") !== "off",
      cardLayout: read(PREF_KEYS.cardLayout, "off") === "on",
      bg: read(PREF_KEYS.bg, ""),
      glowSize: Math.max(60, Math.min(180, Number(read(PREF_KEYS.glowSize, "100")) || 100)),
      glowColor: read(PREF_KEYS.glowColor, ""),
      glowShape: GLOW_SHAPES.includes(read(PREF_KEYS.glowShape, "soft")) ? read(PREF_KEYS.glowShape, "soft") : "soft",
    };
  }

  function postToFrame(frame) {
    try {
      frame.contentWindow.postMessage({ type: "qai-theme", theme: current }, "*");
      frame.contentWindow.postMessage({ type: "qai-prefs", prefs: getPrefs() }, "*");
    } catch (e) {}
  }

  function broadcastTheme() {
    if (embedded) {
      try { window.parent.postMessage({ type: "qai-theme-change", theme: current }, "*"); } catch (e) {}
      return;
    }
    document.querySelectorAll("iframe").forEach(postToFrame);
  }

  function broadcastPrefs() {
    const prefs = getPrefs();
    if (embedded) {
      try { window.parent.postMessage({ type: "qai-prefs-change", prefs }, "*"); } catch (e) {}
      return;
    }
    document.querySelectorAll("iframe").forEach((f) => {
      try { f.contentWindow.postMessage({ type: "qai-prefs", prefs }, "*"); } catch (e) {}
    });
  }

  function themeWash() {
    let wash = document.querySelector(".theme-wash");
    if (!wash) {
      wash = document.createElement("div");
      wash.className = "theme-wash";
      document.body.appendChild(wash);
    }
    wash.classList.add("show");
    clearTimeout(washTimer);
    washTimer = setTimeout(() => wash.classList.remove("show"), 180);
  }

  function applyTheme(theme, opts) {
    opts = opts || {};
    if (!valid(theme)) theme = "blue";
    const previous = root.dataset.theme;
    current = theme;
    const commit = () => {
      root.dataset.theme = theme;
      if (opts.persist !== false) write(THEME_KEY, theme);
      document.querySelectorAll(".theme-option").forEach((el) => {
        el.classList.toggle("active", el.dataset.theme === theme);
      });
      document.querySelectorAll('meta[name="theme-color"]').forEach((m) => {
        m.setAttribute("content", META[theme] || META.blue);
      });
      syncAppearance();
    };
    const animate = opts.animate !== false && previous && previous !== theme;
    if (animate && document.startViewTransition && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      document.startViewTransition(commit);
    } else {
      commit();
      if (animate) themeWash();
    }
    if (opts.broadcast !== false) broadcastTheme();
  }

  function syncAppearance() {
    const p = getPrefs();
    const jiggle = document.getElementById("toggleJiggle");
    const follow = document.getElementById("toggleBgFollow");
    const drag = document.getElementById("toggleCardDrag");
    const color = document.getElementById("bgColor");
    const label = document.getElementById("bgColorValue");
    const glowSize = document.getElementById("glowSize");
    const glowSizeValue = document.getElementById("glowSizeValue");
    const glowColor = document.getElementById("glowColor");
    if (jiggle) jiggle.checked = p.jiggle;
    if (follow) follow.checked = p.bgFollow;
    if (drag) drag.checked = p.cardLayout;
    if (color) color.value = /^#[0-9a-f]{6}$/i.test(p.bg) ? p.bg : (META[current] || "#06131d");
    if (label) label.textContent = p.bg ? `自定义 ${p.bg}` : "使用主题背景";
    if (glowSize) glowSize.value = String(p.glowSize);
    if (glowSizeValue) glowSizeValue.textContent = `${p.glowSize}%`;
    if (glowColor) glowColor.value = /^#[0-9a-f]{6}$/i.test(p.glowColor) ? p.glowColor : (GLOW_COLORS[current] || "#4da3ff");
    document.querySelectorAll(".glow-shape").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.shape === p.glowShape);
    });
  }

  function applyPrefs(prefs, opts) {
    opts = opts || {};
    const p = Object.assign(getPrefs(), prefs || {});
    if (opts.persist !== false) {
      write(PREF_KEYS.jiggle, p.jiggle ? "on" : "off");
      write(PREF_KEYS.bgFollow, p.bgFollow ? "on" : "off");
      write(PREF_KEYS.cardLayout, p.cardLayout ? "on" : "off");
      if (p.bg) write(PREF_KEYS.bg, p.bg);
      else { try { localStorage.removeItem(PREF_KEYS.bg); } catch (e) {} }
      write(PREF_KEYS.glowSize, String(p.glowSize));
      if (p.glowColor) write(PREF_KEYS.glowColor, p.glowColor);
      else { try { localStorage.removeItem(PREF_KEYS.glowColor); } catch (e) {} }
      write(PREF_KEYS.glowShape, p.glowShape);
    }
    root.classList.toggle("no-jiggle", !p.jiggle);
    root.classList.toggle("no-bg-follow", !p.bgFollow);
    root.dataset.cardLayout = p.cardLayout ? "on" : "off";
    root.dataset.glowShape = p.glowShape;
    root.style.setProperty("--glow-scale", String(p.glowSize / 100));
    if (/^#[0-9a-f]{6}$/i.test(p.glowColor)) root.style.setProperty("--glow-color", p.glowColor);
    else root.style.removeProperty("--glow-color");
    if (/^#[0-9a-f]{6}$/i.test(p.bg)) root.style.setProperty("--custom-bg", p.bg);
    else root.style.removeProperty("--custom-bg");
    if (!p.bgFollow) {
      root.style.setProperty("--mx", "50%");
      root.style.setProperty("--my", "32%");
    }
    document.documentElement.classList.toggle("card-layout-on", p.cardLayout);
    syncAppearance();
    try {
      window.dispatchEvent(new CustomEvent("qai-prefs-changed", { detail: getPrefs() }));
    } catch (e) {}
    if (opts.broadcast !== false) broadcastPrefs();
  }

  function withTheme(url) {
    if (!url) return url;
    try {
      const u = new URL(url, location.href);
      if (u.hostname !== "127.0.0.1" && u.hostname !== "localhost") return url;
      u.searchParams.set("theme", current);
      return u.toString();
    } catch (e) {
      return url;
    }
  }

  function closeAppearance() {
    const modal = document.getElementById("appearanceModal");
    if (modal) modal.classList.add("hidden");
  }

  function bindAppearance() {
    const modal = document.getElementById("appearanceModal");
    const open = document.getElementById("appearanceBtn");
    const close = document.getElementById("closeAppearance");
    const done = document.getElementById("appearanceDone");
    if (!modal || !open) return;
    open.addEventListener("click", () => {
      syncAppearance();
      modal.classList.remove("hidden");
    });
    [close, done].forEach((el) => el && el.addEventListener("click", closeAppearance));
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeAppearance();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !modal.classList.contains("hidden")) closeAppearance();
    });
    modal.querySelectorAll(".theme-option").forEach((el) => {
      el.addEventListener("click", () => applyTheme(el.dataset.theme, { persist: true, broadcast: true }));
    });
    const jiggle = document.getElementById("toggleJiggle");
    const follow = document.getElementById("toggleBgFollow");
    const drag = document.getElementById("toggleCardDrag");
    if (jiggle) jiggle.addEventListener("change", () => applyPrefs({ jiggle: jiggle.checked }, { persist: true, broadcast: true }));
    if (follow) follow.addEventListener("change", () => applyPrefs({ bgFollow: follow.checked }, { persist: true, broadcast: true }));
    if (drag) drag.addEventListener("change", () => applyPrefs({ cardLayout: drag.checked }, { persist: true, broadcast: true }));
    const color = document.getElementById("bgColor");
    const reset = document.getElementById("bgReset");
    if (color) color.addEventListener("input", () => applyPrefs({ bg: color.value }, { persist: true, broadcast: true }));
    if (reset) reset.addEventListener("click", () => applyPrefs({ bg: "" }, { persist: true, broadcast: true }));
    const glowSize = document.getElementById("glowSize");
    const glowColor = document.getElementById("glowColor");
    const glowReset = document.getElementById("glowReset");
    if (glowSize) glowSize.addEventListener("input", () => applyPrefs({ glowSize: Number(glowSize.value) }, { persist: true, broadcast: true }));
    if (glowColor) glowColor.addEventListener("input", () => applyPrefs({ glowColor: glowColor.value }, { persist: true, broadcast: true }));
    if (glowReset) glowReset.addEventListener("click", () => applyPrefs({ glowColor: "" }, { persist: true, broadcast: true }));
    document.querySelectorAll(".glow-shape").forEach((btn) => {
      btn.addEventListener("click", () => applyPrefs({ glowShape: btn.dataset.shape }, { persist: true, broadcast: true }));
    });
  }

  function bindAmbient() {
    let raf = 0;
    let lastX = 50;
    let lastY = 32;
    const move = (x, y) => {
      if (root.classList.contains("no-bg-follow")) return;
      lastX = Math.max(0, Math.min(100, (x / window.innerWidth) * 100));
      lastY = Math.max(0, Math.min(100, (y / window.innerHeight) * 100));
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        root.style.setProperty("--mx", lastX.toFixed(2) + "%");
        root.style.setProperty("--my", lastY.toFixed(2) + "%");
      });
    };
    window.addEventListener("pointermove", (e) => move(e.clientX, e.clientY), { passive: true });
    window.addEventListener("pointerdown", (e) => move(e.clientX, e.clientY), { passive: true });
    root.style.setProperty("--mx", "50%");
    root.style.setProperty("--my", "32%");
  }

  function bindScroll() {
    let raf = 0;
    const update = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const y = Math.max(0, window.scrollY || 0);
        root.style.setProperty("--scroll", String(y));
        root.style.setProperty("--scroll-shift", `${-Math.min(80, y * 0.018).toFixed(1)}px`);
      });
    };
    window.addEventListener("scroll", update, { passive: true });
    update();
  }

  function bindJiggle() {
    const selector = ".card, .stat-card, .persona-card, .quick-action, .nav-item, .theme-option";
    document.addEventListener("pointerdown", (e) => {
      if (root.classList.contains("no-jiggle") || root.classList.contains("card-layout-on")) return;
      const el = e.target.closest(selector);
      if (!el) return;
      el.classList.remove("qai-jiggle");
      void el.offsetWidth;
      el.classList.add("qai-jiggle");
      setTimeout(() => el.classList.remove("qai-jiggle"), 380);
    }, { passive: true });
  }

  function bindCustomSelects() {
    let openSelect = null;

    if (!window.__qaiSelectValuePatched) {
      window.__qaiSelectValuePatched = true;
      try {
        const proto = HTMLSelectElement.prototype;
        const vd = Object.getOwnPropertyDescriptor(proto, "value");
        if (vd && vd.set) {
          Object.defineProperty(proto, "value", {
            configurable: true,
            get: vd.get,
            set(v) {
              vd.set.call(this, v);
              this.dispatchEvent(new Event("qai-select-sync"));
            },
          });
        }
        const id = Object.getOwnPropertyDescriptor(proto, "selectedIndex");
        if (id && id.set) {
          Object.defineProperty(proto, "selectedIndex", {
            configurable: true,
            get: id.get,
            set(v) {
              id.set.call(this, v);
              this.dispatchEvent(new Event("qai-select-sync"));
            },
          });
        }
      } catch (e) {}
    }

    function closeMenu(sel) {
      if (!sel) return;
      sel.wrap.classList.remove("open");
      sel.menu.classList.remove("open");
      sel.btn.setAttribute("aria-expanded", "false");
      setTimeout(() => {
        if (!sel.menu.classList.contains("open")) sel.menu.style.display = "none";
      }, 140);
    }

    function sync(sel) {
      const opt = sel.select.options[sel.select.selectedIndex];
      sel.label.textContent = opt ? opt.textContent : "请选择";
      const disabled = !!sel.select.disabled;
      sel.btn.disabled = disabled;
      sel.wrap.classList.toggle("disabled", disabled);
      if (sel.select.style.maxWidth) sel.wrap.style.maxWidth = sel.select.style.maxWidth;
      if (sel.select.style.width) sel.wrap.style.width = sel.select.style.width;
    }

    function build(sel) {
      sel.menu.innerHTML = "";
      Array.from(sel.select.options).forEach((opt) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "qai-select-option";
        item.textContent = opt.textContent;
        item.dataset.value = opt.value;
        if (opt.disabled) item.disabled = true;
        if (opt.value === sel.select.value) item.classList.add("selected");
        item.addEventListener("click", (e) => {
          e.stopPropagation();
          if (opt.disabled) return;
          sel.select.value = opt.value;
          sel.select.dispatchEvent(new Event("change", { bubbles: true }));
          sync(sel);
          closeMenu(sel);
          if (openSelect === sel) openSelect = null;
        });
        sel.menu.appendChild(item);
      });
    }

    function open(sel) {
      if (openSelect && openSelect !== sel) closeMenu(openSelect);
      openSelect = sel;
      sync(sel);
      build(sel);
      const r = sel.btn.getBoundingClientRect();
      sel.menu.style.width = `${Math.max(120, r.width)}px`;
      sel.menu.style.left = `${Math.max(6, Math.min(r.left, window.innerWidth - r.width - 6))}px`;
      sel.menu.style.display = "block";
      sel.wrap.classList.add("open");
      const height = Math.min(280, sel.menu.scrollHeight + 10);
      const below = r.bottom + 7;
      const above = r.top - height - 7;
      sel.menu.style.top = `${below + height > window.innerHeight && above > 6 ? above : below}px`;
      requestAnimationFrame(() => sel.menu.classList.add("open"));
      sel.btn.setAttribute("aria-expanded", "true");
    }

    function enhance(select) {
      if (!select || select.dataset.qaiSelect || select.multiple || Number(select.size) > 1) return;
      if (select.closest(".qai-select")) return;
      const wrap = document.createElement("span");
      wrap.className = "qai-select";
      select.parentNode.insertBefore(wrap, select);
      wrap.appendChild(select);
      select.dataset.qaiSelect = "1";
      select.classList.add("qai-select-native");

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "qai-select-btn";
      btn.setAttribute("aria-haspopup", "listbox");
      btn.setAttribute("aria-expanded", "false");
      const label = document.createElement("span");
      label.className = "qai-select-label";
      const caret = document.createElement("span");
      caret.className = "qai-select-caret";
      caret.textContent = "⌄";
      btn.append(label, caret);
      wrap.appendChild(btn);

      const menu = document.createElement("div");
      menu.className = "qai-select-menu";
      menu.setAttribute("role", "listbox");
      document.body.appendChild(menu);

      const state = { select, wrap, btn, label, menu };
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (select.disabled) return;
        if (menu.classList.contains("open")) {
          closeMenu(state);
          if (openSelect === state) openSelect = null;
        } else {
          open(state);
        }
      });
      select.addEventListener("change", () => sync(state));
      select.addEventListener("qai-select-sync", () => sync(state));
      new MutationObserver(() => sync(state)).observe(select, {
        childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "style"],
      });
      sync(state);
    }

    function enhanceAll() {
      document.querySelectorAll("select:not([data-qai-select])").forEach(enhance);
    }

    document.addEventListener("click", () => {
      if (openSelect) {
        closeMenu(openSelect);
        openSelect = null;
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && openSelect) {
        closeMenu(openSelect);
        openSelect = null;
      }
    });
    window.addEventListener("resize", () => {
      if (openSelect) { closeMenu(openSelect); openSelect = null; }
    });
    document.addEventListener("scroll", () => {
      if (openSelect) { closeMenu(openSelect); openSelect = null; }
    }, true);

    enhanceAll();
    new MutationObserver(enhanceAll).observe(document.body, { childList: true, subtree: true });
  }

  function bindFrames() {
    if (embedded) return;
    document.querySelectorAll("iframe").forEach((frame) => {
      postToFrame(frame);
      frame.addEventListener("load", () => postToFrame(frame));
    });
  }

  function bindMessages() {
    window.addEventListener("message", (e) => {
      const data = e.data || {};
      if ((data.type === "qai-theme" || data.type === "qai-theme-change") && valid(data.theme)) {
        applyTheme(data.theme, { persist: true, broadcast: false, animate: false });
      }
      if ((data.type === "qai-prefs" || data.type === "qai-prefs-change") && data.prefs) {
        applyPrefs(data.prefs, { persist: true, broadcast: false });
      }
    });
  }

  function init() {
    current = storedTheme();
    applyTheme(current, { persist: false, broadcast: false, animate: false });
    applyPrefs(getPrefs(), { persist: false, broadcast: false });
    bindAppearance();
    bindAmbient();
    bindScroll();
    bindJiggle();
    bindCustomSelects();
    bindFrames();
    bindMessages();
  }

  window.QAITheme = {
    apply: applyTheme,
    withTheme,
    getPrefs,
    applyPrefs,
    get current() { return current; },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
