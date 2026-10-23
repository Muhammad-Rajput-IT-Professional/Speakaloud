# Speakaloud - Native Windows Desktop PDF Reader & Notepad

A high-performance **100% Native Windows Desktop Application** designed for reading PDFs and drafting notes/emails with natural speech cadence. Speakaloud leverages **Microsoft Edge Cloud Neural Voices** and **Kokoro-82M Local Neural TTS** with customizable pronunciation rules and Edge-style sentence and word highlighting.

---

## ✨ Key Features

### 📄 1. High-Performance Native PDF Engine (PyMuPDF / C++)
- **Crisp High-DPI Rendering**: Zero browser/Chromium overhead. Renders authentic PDF pages with crisp font kerning, original layouts, margins, tables, and images.
- **Collapsible Sidebar**: Access your Library history and PDF Bookmarks/TOC with one click, or collapse the sidebar for a clean, full-width view.
- **Interactive Click-to-Play**: Click any word to immediately start audio playback from that exact word without jumping the viewport.
- **Microsoft Edge Read Aloud Highlighting**:
  - Entire spoken sentence is highlighted in soft pastel blue (`#b2d6f3`).
  - Active spoken word is highlighted in real-time in vibrant yellow (`#ffff00`).
  - Font letters remain 100% sharp jet black with zero opacity masking.
- **Smooth Auto-Scroll**: Viewport stays stationary when reading on-screen text, smoothly scrolling only when reading advances beyond the visible page fold.

### 📝 2. Built-in Rich Text Notepad & Drafter
- **Rich Text Toolbar**: Bold (**𝐁**), Italic (*𝘐*), Underline (**U̲**), Headings (**H1**, **H2**), Bullet points (**•**), and Numbered lists (**1.**).
- **Smart Formatting**: Automatic bullet continuation when pressing `Enter`.
- **Listen & Edit**: Click **▶ Read From Cursor** or double-click any word to listen to your draft aloud. Catch typos and awkward phrasing before sending emails.
- **Draft Autosave**: Drafts persist automatically between sessions.

### 🎙️ 3. Dual Speech Engines & Storage Management
- **Microsoft Edge Cloud Voices**: 320+ natural voices with zero local disk footprint.
- **Kokoro-82M Local Neural TTS**: Fully offline high-quality speech generation.
- **Storage & Cache Cleaner**:
  - One-click **🧹 Clear Audio Cache** button in the sidebar and Voice Manager frees temporary audio clips.
  - One-click **🗑️ Delete Local Models** frees ~400 MB if you only want to use Edge Cloud Voices.
  - Completely preserves reading history, bookmarks, speech rules, and drafts.

### 🗣️ 4. Pronunciation & Speech Rules
- **Acronym & Term Expansion**: Automatically pronounces abbreviations phonetically (e.g., replaces `AMA` with `Azure Monitor Agent`).
- **Interactive Live Tester**: Test pronunciation substitutions in real time as you type.

---

## 🚀 Getting Started

### Launch with One Click
Double-click `Speakaloud.bat` in the project root:
```cmd
Speakaloud.bat
```
> **Portable on Any Windows PC**: `Speakaloud.bat` is self-healing. If Python or required libraries are missing on a PC, it detects this, automatically installs required dependencies, and launches the app smoothly.

### Manual Setup (Optional)
```cmd
pip install -r requirements.txt
python desktop_app.py
```

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| **Spacebar** | Play / Pause |
| **Ctrl + B** | Toggle Bold in Notepad |
| **Ctrl + I** | Toggle Italic in Notepad |
| **Ctrl + U** | Toggle Underline in Notepad |
| **Left Arrow (←)** | Previous Sentence |
| **Right Arrow (→)** | Next Sentence |
| **Page Up** | Previous Page |
| **Page Down** | Next Page |
| **+ / =** | Zoom In |
| **-** | Zoom Out |
