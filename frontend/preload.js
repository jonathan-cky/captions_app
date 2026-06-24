/**
 * preload.js — Secure context bridge between Electron main and renderer.
 * Only exposes the specific APIs the renderer needs — nothing more.
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("captioneer", {
  // Renderer → Main
  resizeHeight: (height) => ipcRenderer.send("resize-height", height),
  quit:         ()       => ipcRenderer.send("quit-app"),
});
