const { app, BrowserWindow, ipcMain, screen } = require("electron");
const path = require("path");

// ── Single instance lock ───────────────────────────────────────────────────
const gotLock = true; //app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
  process.exit(0);
}

let overlayWindow = null;

// ── Create the floating caption overlay ───────────────────────────────────
function createOverlay() {
  const { width: sw } = screen.getPrimaryDisplay().workAreaSize;

  overlayWindow = new BrowserWindow({
    // Initial position: top-center of screen
    x:      Math.round(sw / 2 - 480),
    y:      40,
    width:  960,
    height: 120,

    // Overlay window flags
    alwaysOnTop:     true,
    frame:           false,          // no title bar
    transparent:     true,           // allow rounded corners / blur
    hasShadow:       false,
    resizable:       true,
    movable:         true,
    minimizable:     false,
    maximizable:     false,
    skipTaskbar:     true,           // don't appear in Dock
    titleBarStyle:   "customButtonsOnHover",

    // Follow across ALL macOS Spaces / full-screen apps
    visibleOnAllWorkspaces: true,
    fullscreenable:          false,

    webPreferences: {
      preload:            path.join(__dirname, "preload.js"),
      contextIsolation:   true,
      nodeIntegration:    false,
    },
  });

  // macOS: float above full-screen apps too
  overlayWindow.setAlwaysOnTop(true, "screen-saver");
  overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  overlayWindow.loadFile(path.join(__dirname, "renderer/index.html"));

  // Keep window on top when other apps take focus
  overlayWindow.on("blur", () => {
    overlayWindow.setAlwaysOnTop(true, "screen-saver");
  });

  // Dev tools (comment out for production)
  overlayWindow.webContents.openDevTools({ mode: "detach" });
}

// ── App lifecycle ──────────────────────────────────────────────────────────
app.whenReady().then(() => {
  createOverlay();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createOverlay();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// ── IPC handlers (from renderer via preload) ───────────────────────────────

// Resize window height dynamically as captions grow/shrink
ipcMain.on("resize-height", (_, height) => {
  if (!overlayWindow) return;
  const [w] = overlayWindow.getSize();
  const clamped = Math.max(80, Math.min(400, height));
  overlayWindow.setSize(w, clamped, true);
});

// Quit app from renderer
ipcMain.on("quit-app", () => {
  app.quit();
});
