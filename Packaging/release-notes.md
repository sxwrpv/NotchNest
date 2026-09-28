## Install

Paste this into Terminal on an Apple Silicon Mac (macOS 14 or later):

```bash
curl -fsSL https://github.com/sxwrpv/NotchNest/releases/latest/download/install.sh | bash
```

It downloads the app, checks its SHA-256, installs it into Applications and opens it. NotchNest isn't notarized by Apple. A copy installed this way isn't flagged as downloaded, so macOS doesn't show the "can't be opened" prompt.

**Prefer a disk image?** Download `NotchNest-*.dmg` below. Macs block un-notarized apps that were downloaded in a browser, so the first open needs **System Settings → Privacy & Security → Open Anyway** (see *READ ME FIRST* inside the image).

## What's new in 1.1

- **Installs itself on a new Mac.** The setup assistant runs on first launch. It sets up a private Python 3.12 and the hash-locked dictation engine, then downloads the speech and AI cleanup models (~2.5 GB, one time) with live progress. After that, everything runs offline.
- **Tuned to your Mac.** Macs with 12 GB of memory or more get the Qwen2.5-3B cleanup model; 8 GB Macs get the 1.5B model. Dictation is set to the right ⌥ key.
- **Guided permissions.** Microphone, Accessibility and Calendar each show live status, and NotchNest turns on open-at-login.
- **Moves itself to Applications** when opened from Downloads or the disk image.
- **Repair Engine** in Settings re-checks the engine and fixes anything missing.
- New app icon.

Dictate by holding **right ⌥** and talking, or double-tap it to toggle. The text is copied to the clipboard and shown in the notch.
