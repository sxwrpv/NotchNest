## Install

Paste this into Terminal on an Apple Silicon Mac (macOS 14 or later):

```bash
curl -fsSL https://github.com/sxwrpv/NotchNest/releases/latest/download/install.sh | bash
```

It downloads the app, checks its SHA-256, installs it into Applications and opens it. NotchNest isn't notarized by Apple. A copy installed this way isn't flagged as downloaded, so macOS doesn't show the "can't be opened" prompt.

**Prefer a disk image?** Download `NotchNest-*.dmg` below. Macs block un-notarized apps that were downloaded in a browser, so the first open needs **System Settings → Privacy & Security → Open Anyway** (see *READ ME FIRST* inside the image).

## What's new in 1.2.1

- **Fixes a launch freeze.** If you had connected Spotify's Web API, 1.2.0 could sit frozen for about a minute after an update while it waited for Keychain approval. NotchNest no longer reads the Spotify tokens at launch, and it only asks the Keychain for them when the Web API is actually used, without ever blocking the app.

## What's new in 1.2

A privacy and security release. Everything still runs on your Mac; now less of what you say and copy stays behind.

- **Clipboard history is opt-in.** On a new install it starts off (turn it on in Settings → Modules); if you already use it, it stays on. It only records while the module is on, never records passwords or anything apps mark as private, and skips copies made while a password manager is in front. Pause it from the panel; unpinned items are forgotten after 7 days by default (Settings → Clipboard), and **Delete All Clipboard History** clears everything. Pinned items never expire.
- **Your words stay out of the logs.** The dictation log now records how long each transcript was, not what you said. `~/.murmur` is readable only by you, and the latest transcript is cleared from it 30 seconds after it's handed to the notch.
- **Spotify sign-in tokens are kept in the Keychain** (moved there automatically), and the sign-in only listens on your own Mac.
- **The AI cleanup server must be on this Mac**: an Ollama address has to be localhost, and requests can't be redirected elsewhere.
- **Pinned models.** The speech and cleanup models load at the exact versions they were tested with.
- **Hardened runtime**: no other program can inject code into NotchNest while it holds your microphone and Accessibility permissions.

## What's new in 1.1.1

The **File Tray** works now. In 1.1.0, files dropped on the notch never landed.

- **Drop files straight onto the closed notch.** The tray opens to catch them and folds away again once you move on.
- **Drag files back out to any app** and you get the file itself, under its real name. It's always a copy, so your original stays where it was.
- **Photos and Mail attachments** can be dropped in too. The tray keeps its own copy and deletes it when you remove the item.
- **Share…** (right-click a file) opens AirDrop, Mail, Messages and more right under the file.

## What's new in 1.1

- **Installs itself on a new Mac.** The setup assistant runs on first launch. It sets up a private Python 3.12 and the hash-locked dictation engine, then downloads the speech and AI cleanup models (~2.5 GB, one time) with live progress. After that, everything runs offline.
- **Tuned to your Mac.** Macs with 12 GB of memory or more get the Qwen2.5-3B cleanup model; 8 GB Macs get the 1.5B model. Dictation is set to the right ⌥ key.
- **Guided permissions.** Microphone, Accessibility and Calendar each show live status, and NotchNest turns on open-at-login.
- **Moves itself to Applications** when opened from Downloads or the disk image.
- **Repair Engine** in Settings re-checks the engine and fixes anything missing.
- New app icon.

Dictate by holding **right ⌥** and talking, or double-tap it to toggle. The text is copied to the clipboard and shown in the notch.
