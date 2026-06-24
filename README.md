# Captioneer 🎙️

Live, local captions for any audio playing on your Mac.  
No internet. No cloud. No API keys. Fully private.

**Supported languages:** English · 中文 · 한국어 · 日本語

---

## ⚠️ macOS Tahoe (macOS 16) Compatibility

**Captioneer does not work on macOS Tahoe (macOS 16).**

The `AudioTap` binary relies on ScreenCaptureKit's audio tap API, which has a known bug in macOS Tahoe that prevents system audio capture from functioning correctly. This is an Apple-side issue and has not been resolved as of June 24, 2026.

**Supported:** macOS 13 Ventura · macOS 14 Sonoma · macOS 15 Sequoia  
**Not supported:** macOS 16 Tahoe (until Apple ships a fix)

If you are on Tahoe and want to track progress, watch this repo for updates.

---

## How it works

```
System Audio (ScreenCaptureKit)
        ↓
  Silero VAD (silence detection → clean chunks)
        ↓
  Whisper (faster-whisper, local model)
        ↓
  WebSocket → Electron overlay window
```

---

## Requirements

| Tool                     | Version                        | Install                                                   |
| ------------------------ | ------------------------------ | --------------------------------------------------------- |
| macOS                    | 13.0–15.x (Ventura–Sequoia) ⚠️ | —                                                         |
| Xcode Command Line Tools | latest                         | `xcode-select --install`                                  |
| Python                   | 3.10+                          | [python.org](https://python.org) or `brew install python` |
| Node.js                  | 18+                            | [nodejs.org](https://nodejs.org) or `brew install node`   |
| npm                      | 9+                             | bundled with Node                                         |

> ⚠️ macOS 16 Tahoe is **not supported** — see compatibility note above.

---

## Setup

### 1. Clone / download the project

```
cd ~/Projects
git clone https://github.com/jonathan-cky/captions_app captioneer
cd captioneer
```

### 2. Compile the Swift audio tap

```
swiftc audio-tap/AudioTap.swift \
  -o audio-tap/AudioTap \
  -framework ScreenCaptureKit \
  -framework AVFoundation \
  -framework CoreAudio \
  -framework CoreMedia
```
> **Tip:** You only need to do this once. The binary is ~500KB.

### 3. Install Python dependencies

```
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
> On first run, `faster-whisper` will download the Whisper model (~465MB for `small`).  
> All models are cached in `backend/models/` — never downloaded again.

### 4. Install Node / Electron dependencies

```
npm install
```

---

## Running (development)

You need **two terminal windows**:

**Terminal 1 — Python backend:**

```
source venv/bin/activate
python backend/main.py --model small
```

**Terminal 2 — Electron overlay:**

```
printf "Electron.app/Contents/MacOS/Electron" > node_modules/electron/path.txt
chmod +x node_modules/electron/dist/Electron.app/Contents/MacOS/Electron
npm start
```

The caption bar will appear at the top of your screen. Play any video or audio — captions will appear within ~4 seconds.

<details>
<summary>Electron not launching? Manual reinstall steps</summary>

```
rm -rf node_modules/electron
npm install electron
curl -L -o /tmp/electron.zip https://github.com/electron/electron/releases/download/v30.5.1/electron-v30.5.1-darwin-arm64.zip
mkdir -p node_modules/electron/dist
unzip /tmp/electron.zip -d node_modules/electron/dist/
printf "Electron.app/Contents/MacOS/Electron" > node_modules/electron/path.txt
chmod +x node_modules/electron/dist/Electron.app/Contents/MacOS/Electron
npm start
```
</details>

---

## macOS Permission (one-time)

On first launch, macOS will ask for **Screen Recording** permission.

`System Settings → Privacy & Security → Screen Recording → Captioneer ✓`

This is required for ScreenCaptureKit to tap system audio.  
**No screen pixels are ever captured** — audio only.

---

## Model size guide

```
# Fastest (testing)
python backend/main.py --model tiny

# Recommended (good CJK accuracy, fast)
python backend/main.py --model small

# Best accuracy (needs ~5GB RAM, still real-time on M1+)
python backend/main.py --model medium

# Maximum accuracy
python backend/main.py --model large-v3
```

---

## Overlay controls

| Control          | Action                        |
| ---------------- | ----------------------------- |
| Drag the top bar | Move the window               |
| Drag bottom edge | Resize height                 |
| **A+** / **A−**  | Increase / decrease font size |
| **✕ Clear**      | Clear caption history         |
| **⏻**            | Quit Captioneer               |

The window follows you across all macOS Spaces and full-screen apps.

---

## Project structure

```
captioneer/
├── audio-tap/
│   └── AudioTap.swift       # ScreenCaptureKit audio tap (Swift CLI)
├── backend/
│   ├── main.py              # Entry point — wires everything together
│   ├── audio_capture.py     # Launches AudioTap, reads PCM stream
│   ├── vad.py               # Silero VAD — speech chunking
│   ├── transcriber.py       # faster-whisper wrapper
│   ├── ws_server.py         # WebSocket server → Electron
│   └── models/              # Auto-downloaded model files (gitignored)
├── frontend/
│   ├── main.js              # Electron main process
│   ├── preload.js           # Secure IPC bridge
│   └── renderer/
│       ├── index.html       # Overlay HTML
│       ├── style.css        # Frosted glass caption bar
│       └── renderer.js      # WebSocket client + caption logic
├── requirements.txt
├── package.json
└── README.md
```

---

## Troubleshooting

**"No audio captured" / captions not appearing:**
- Make sure Screen Recording permission is granted
- Check that audio is actually playing (not muted)
- Try `--model tiny` first to rule out model loading issues
- If on macOS Tahoe (16), this is a known incompatibility — see the note at the top

**AudioTap compile error:**
- Run `xcode-select --install` to ensure Xcode CLI tools are present
- Requires macOS 13–15 SDK (Tahoe SDK not supported)

**Whisper not detecting CJK correctly:**
- Use `--model medium` or `--model large-v3` for better CJK accuracy
- Speak clearly; Whisper needs ~2s of speech to detect language

**Overlay not staying on top:**
- This is normal if another app requests screen-saver level — rare
- Re-launch fixes it

---

## Roadmap

- [ ] Windows support (WASAPI loopback)
- [ ] Linux support (PipeWire / PulseAudio monitor)
- [ ] Per-app audio tap (macOS 14.2+)
- [ ] Translation mode (ZH/KO/JA → EN)
- [ ] Caption history export (.srt / .txt)
- [ ] Packaged .app with embedded Python (PyInstaller)
- [ ] macOS Tahoe (16) support — pending Apple fix for ScreenCaptureKit audio tap bug
