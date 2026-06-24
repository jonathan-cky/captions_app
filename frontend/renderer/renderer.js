/**
 * renderer.js — Connects to the Python WebSocket server,
 * displays captions, and handles UI controls.
 */

// ── Config ────────────────────────────────────────────────────────────────────
const WS_URL         = "ws://127.0.0.1:8765";
const RECONNECT_MS   = 2000;
const MAX_LINES      = 6;        // how many caption lines to keep visible
const PREV_LINES     = 2;        // how many older lines to show dimmed

const LANG_LABELS = {
  en: "EN",
  zh: "中文",
  ko: "한국어",
  ja: "日本語",
};

// ── State ─────────────────────────────────────────────────────────────────────
let ws          = null;
let fontSize    = parseInt(
  getComputedStyle(document.documentElement).getPropertyValue("--font-size")
) || 18;
let captions    = [];            // array of { text, language }
let reconnectTimer = null;

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dot         = document.getElementById("status-dot");
const langBadge   = document.getElementById("lang-badge");
const scroll      = document.getElementById("caption-scroll");
const placeholder = document.getElementById("placeholder");

// ── WebSocket connection ──────────────────────────────────────────────────────
function connect() {
  if (ws) ws.close();

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log("Connected to Captioneer backend");
    setStatus("listening");
    clearReconnect();
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      handleMessage(msg);
    } catch (e) {
      console.error("Bad message:", e);
    }
  };

  ws.onclose = () => {
    setStatus("idle");
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    console.warn("WS error:", err);
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, RECONNECT_MS);
}

function clearReconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

// ── Message handler ───────────────────────────────────────────────────────────
function handleMessage(msg) {
  switch (msg.type) {

    case "caption":
      addCaption(msg.text, msg.language || "en");
      setStatus("listening");
      break;

    case "status":
      setStatus(msg.state);
      break;

    case "error":
      console.error("Backend error:", msg.message);
      break;
  }
}

// ── Caption rendering ─────────────────────────────────────────────────────────
function addCaption(text, language) {
  // Remove placeholder
  if (placeholder.parentNode) placeholder.remove();

  // Add to history
  captions.push({ text, language });

  // Keep history bounded
  if (captions.length > MAX_LINES) {
    captions = captions.slice(-MAX_LINES);
  }

  renderCaptions();
  updateLangBadge(language);
}

function renderCaptions() {
  scroll.innerHTML = "";

  captions.forEach((cap, i) => {
    const isPrev  = i < captions.length - 1;
    const isOld   = i < captions.length - PREV_LINES - 1;

    if (isOld) return;   // don't show very old lines

    const p = document.createElement("p");
    p.className = [
      "caption-line",
      isPrev ? "prev" : "",
      `lang-${cap.language}`,
    ].filter(Boolean).join(" ");

    p.textContent = cap.text;
    scroll.appendChild(p);
  });

  // Auto-scroll to bottom
  scroll.scrollTop = scroll.scrollHeight;

  // Tell main process to resize window height to fit content
  requestAnimationFrame(() => {
    const barH     = document.getElementById("drag-bar").offsetHeight;
    const scrollH  = Math.min(scroll.scrollHeight, 300);
    const total    = barH + scrollH + 24;
    window.captioneer?.resizeHeight(total);
  });
}

// ── Status indicator ──────────────────────────────────────────────────────────
function setStatus(state) {
  dot.className = `dot ${state}`;
  dot.title     = state.charAt(0).toUpperCase() + state.slice(1);
}

function updateLangBadge(lang) {
  langBadge.textContent = LANG_LABELS[lang] || lang.toUpperCase();
}

// ── Font size controls ────────────────────────────────────────────────────────
function setFontSize(size) {
  fontSize = Math.max(12, Math.min(32, size));
  document.documentElement.style.setProperty("--font-size", `${fontSize}px`);
}

document.getElementById("btn-font-up").addEventListener("click", () => {
  setFontSize(fontSize + 2);
});
document.getElementById("btn-font-down").addEventListener("click", () => {
  setFontSize(fontSize - 2);
});

// ── Clear captions ────────────────────────────────────────────────────────────
document.getElementById("btn-clear").addEventListener("click", () => {
  captions = [];
  scroll.innerHTML = "";
  scroll.appendChild(placeholder);
  window.captioneer?.resizeHeight(120);
});

// ── Quit ──────────────────────────────────────────────────────────────────────
document.getElementById("btn-quit").addEventListener("click", () => {
  window.captioneer?.quit();
});

// ── Boot ──────────────────────────────────────────────────────────────────────
connect();
