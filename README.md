# NotchNest

**Install** (Apple Silicon, macOS 14+) — paste into Terminal:

```bash
curl -fsSL https://github.com/sxwrpv/NotchNest/releases/latest/download/install.sh | bash
```

Or grab the disk image from [Releases](https://github.com/sxwrpv/NotchNest/releases/latest).

A personal, fully-editable macOS notch utility — a clean-room reimplementation of the
NotchNest feature set. Native **Swift + SwiftUI + AppKit**, no external dependencies,
builds with Swift Package Manager (no Xcode required).

> This is original code written from scratch to replicate the *behaviour* of the
> App Store app. It does not contain any of that app's code or assets.

## Features

| Module        | What it does |
|---------------|--------------|
| **Now Playing** | Controls Apple Music & Spotify (play/pause/next/prev, title, artist, artwork). Only scripts players that are already running — never launches them. For Spotify there's a **Like button** that saves/removes the current track from Liked Songs (see "Spotify Like button" below). |
| **Dictation**   | Built-in push-to-talk dictation (the merged Murmur engine in `DictationEngine/`): live state, mic button, latest transcript, history (click to copy). New transcripts are copied to the clipboard. Settings live in NotchNest → Settings → Dictation. See "Dictation engine" below. |
| **File Tray**   | Drag files onto the notch to stash them, drag them back out to any app (a copy — the original stays put), double-click to open, right-click to Reveal in Finder / Share (AirDrop) / Remove. Holds references to your files, persisted across launches via bookmarks; files *promised* by Photos or Mail are saved to `~/Library/Application Support/NotchNest/File Tray` and deleted when removed from the tray. |
| **Clipboard**   | Searchable, pinnable text history; click any entry to re-copy. **Off by default** — turn it on in Settings → Modules. Never records passwords or anything apps mark as private, nor copies made while a password manager is in front. Pause it from the panel; unpinned items are forgotten after 7 days by default (Settings → Clipboard), pinned ones never expire. |
| **Timer**       | Pomodoro: focus → short break, long break every 4th session. Ring progress + a system notification on each transition. |
| **Note**        | A persistent quick-note scratchpad (auto-saves). |
| **Calendar**    | Today's events from the system calendar (EventKit). |

The pill hugs the notch; **hover it to expand**, move away to collapse. Works on
external / non-notched displays too (shows a small pill at top-center). A menu-bar
icon gives Toggle Notch / Settings / Quit.

**Look & feel:** Apple **Liquid Glass** on macOS 26 (frosted `NSVisualEffectView`
fallback on earlier releases). Collapsed, the pill goes near-solid to blend with the
physical notch; expanded, it's a lit glass panel. The reveal rides a single spring.

**Dragging files:** because you can't hover-to-expand while a drag is in progress,
dragging any file onto the notch **auto-opens the File Tray** to catch the drop.

**Adaptive popup:** the expanded panel sizes itself to the selected module —
compact for Now Playing / Timer / Dictation, larger for list modules — and
re-springs when you switch tabs (sizes live in `ModuleID.panelSize`).

**Settings** expose per-module toggles, launch-at-login, Pomodoro durations,
clipboard limit, the glass tint, the full reveal animation (spring duration,
bounce, collapse delay, content fade-in/out timing), and Spotify connection.

## Spotify Like button

The heart in Now Playing saves/removes the current track from Liked Songs.
Two paths, chosen automatically:

- **Local control (default, zero setup):** NotchNest drives the Spotify app
  itself — it presses the "Save to Your Library" item in Spotify's native menu
  bar in the background, or posts Spotify's own ⌥⇧B Like shortcut straight to
  the Spotify process. Needs a one-time **Accessibility** grant (System
  Settings → Privacy & Security → Accessibility → NotchNest); the app prompts
  on first use. Builds signed with the "NotchNest Dev" certificate keep the
  grant across rebuilds; an ad-hoc signed build needs it re-ticked.
- **Web API (optional):** real liked-state sync via OAuth/PKCE (Settings →
  Spotify). Spotify policy (since 2025) requires the developer-app owner to
  have **Premium**; on a free account the API returns 403 and NotchNest
  permanently falls back to local control (clicking Connect retries the API).
  The tokens are kept in the login Keychain, and the sign-in redirect is
  caught on 127.0.0.1 only.

## Build & run

```bash
./build.sh          # builds NotchNest.app
./build.sh --run    # builds and launches it
./package.sh        # builds dist/NotchNest-<version>.dmg, .zip and install.sh
./release.sh        # tags v<version> and publishes those as a GitHub release
```

`build.sh` runs `swift build -c release`, assembles a self-contained `NotchNest.app`
(the dictation engine's source, its hash-locked `requirements.lock` and the `uv`
installer ride inside the bundle) and signs it inside-out. Building needs Command
Line Tools and [`uv`](https://docs.astral.sh/uv/) (`brew install uv`; `UV_BIN`
overrides which binary is bundled).

## Sharing it / installing on a new Mac

Easiest: send the one-line installer above. `curl` downloads aren't quarantined,
so macOS opens the app with no Gatekeeper prompt; `install.sh` pins the release
zip's SHA-256. Alternatively `./package.sh` produces `dist/NotchNest-<version>.dmg`
(~25 MB) with the app, an Applications shortcut and a *READ ME FIRST*. Either way,
on first launch the app sets itself up — no Python or Homebrew needed:

1. **Moves itself to Applications** if opened from Downloads or the disk image
   (and relaunches), so open-at-login and permissions stick.
2. **Setup assistant** opens and installs the dictation engine on its own:
   a private Python 3.12 (via the bundled `uv`), the 59 hash-verified packages
   from `requirements.lock`, then the models — with live download progress.
3. **Auto-configures for that Mac** (`setup_engine.py configure`, fresh configs
   only): Whisper large-v3-turbo-q4, plus the Qwen2.5 **3B** cleanup LLM on
   ≥12 GB of RAM or **1.5B** on 8 GB machines; right ⌥ as the dictation key.
4. Walks through **Microphone / Accessibility / Calendar** with live status
   (restarting the engine once Accessibility is granted), and turns on
   **open at login** for installed copies.

Requirements: Apple Silicon, macOS 14+, ~4 GB free, internet on first launch.
The build isn't notarized (no paid Apple Developer ID), so a DMG downloaded in a
browser needs System Settings → Privacy & Security → **Open Anyway** on first open
(macOS 15+) or right-click → Open (macOS 14) — the one-line installer avoids this. Builds signed with the same "NotchNest Dev"
certificate keep users' permission grants across updates.

## Permissions (the setup assistant asks for these)

- **Microphone** + **Accessibility** → dictation (hotkeys, text insertion) and the Spotify Like button.
- **Automation** → to read/control Music & Spotify (Now Playing), asked on first use.
- **Calendar** → to show today's events.
- **Notifications** → for Pomodoro alerts.

If you deny one by mistake: System Settings → Privacy & Security → the relevant section.

## Dictation engine

NotchNest is **one app**: the former standalone Murmur dictation app is merged
in as `DictationEngine/` (Python + MLX Whisper). Its source ships **inside the
signed bundle** (`Contents/Resources/DictationEngine`); `EngineInstaller`
provisions the runtime on first launch:

```
~/Library/Application Support/NotchNest/python/                 uv-managed Python 3.12
~/Library/Application Support/NotchNest/DictationEngine/.venv/  packages from requirements.lock
~/Library/Application Support/NotchNest/DictationEngine/install.json   marker (lockfile SHA-256)
~/Library/Caches/NotchNest/{uv,pycache}/                         download + bytecode caches
~/.cache/huggingface/hub/                                        Whisper + LLM weights
~/Library/Logs/NotchNest-setup.log                               installer log
```

A new `requirements.lock` in an app update triggers a quick re-sync; Settings →
Dictation → **Repair Engine** re-runs every step. Before each launch the venv
is checked: it must resolve into NotchNest's own Python install, and every
directory on the way must be owned by the user and not group/world-writable.

- `DictationManager` spawns `<venv>/bin/python -u main.py --headless` (cwd = the
  bundled source) as a child process (no menu-bar icon, no dock, no pill
  overlay — the notch is the only UI), auto-restarts it if it dies, and
  terminates it on quit. The engine keeps a single-instance guard as a backstop.
- User state stays in `~/.murmur/` (config.yaml, history db, log), readable
  only by you.
- The repo's own `DictationEngine/.venv` is only for development (tests,
  regenerating the lockfile — see the header of `requirements.lock`).

They talk through two files in `~/.murmur`:

```
notch.json   engine -> NotchNest   {"state","text","ts","running","settings"}   (~1s heartbeat;
                                    "text" is cleared 30 s after each transcript)
notch.cmd    NotchNest -> engine   toggle|start|stop|cancel  OR  set <dotted.key> <json>
```

**Dictation settings live in NotchNest → Settings → Dictation** (hotkeys,
Whisper model, language, partials, AI cleanup, LLM backend, insertion mode,
notifications). Writes go through the engine's comment-preserving config
writer and hot-reload live; the `settings` block in `notch.json` mirrors them
back, and only allowlisted keys are writable via the cmd file.

**Permissions:** as a child process the engine uses NotchNest's TCC identity —
NotchNest needs Microphone plus the same Accessibility grant the Like button
uses (hotkeys + text insertion). Old Murmur.app grants don't carry over.

## Privacy & security

Everything runs on the Mac. What NotchNest does to keep it that way:

- **Dictated words stay out of the logs.** `murmur.log` records how long each
  transcript was, not what was said (set `logging.level: DEBUG` to see the
  words while debugging). `~/.murmur` and its files are readable only by you.
  The engine hands each transcript to NotchNest and clears it from `notch.json`
  30 seconds later; NotchNest keeps the session's history in memory only.
- **The AI cleanup server must be on this Mac.** `llm.ollama.url` has to be
  `localhost`, `127.0.0.1` or `::1`. Requests ignore proxy settings and never
  follow redirects, so a transcript can't be routed elsewhere.
- **Models are pinned.** The speech and cleanup models the installer sets up
  load at the exact Hugging Face commits they were tested with
  (`DictationEngine/murmur/models.py`), not whatever the repo holds today.
  Packages are hash-locked (`requirements.lock`).
- **Secrets live in the Keychain** (Spotify tokens), and the Spotify sign-in
  listener answers on 127.0.0.1 only.
- **Hardened runtime.** The app and its `uv` helper are signed with it, so no
  other process can inject code into the one holding the microphone and
  Accessibility grants. `Resources/NotchNest.entitlements` lists the only
  exceptions: Apple Events, microphone, calendars.
- **CI** builds the app and runs the engine tests on every push and pull
  request (`.github/workflows/ci.yml`).
- Not notarized (that needs a paid Apple Developer ID); see "Sharing it".

## Architecture

```
Sources/NotchNest/
  main.swift                 App entry (AppKit lifecycle, accessory app)
  App/
    AppDelegate.swift        Status-bar item, settings window, wiring
    AppEnvironment.swift     Owns all managers; injects EnvironmentObjects
  Notch/
    NotchGeometry.swift      Computes notch/pill frame from NSScreen
    NotchPanel.swift         Borderless non-activating floating NSPanel
    NotchController.swift     Positions panel, animates collapse/expand
    NotchViewModel.swift     Expanded/pinned/selected-tab state
    NotchRootView.swift      SwiftUI surface: shape, hover, header, tabs
  Modules/<Feature>/         One Manager (ObservableObject) + one View each
  Settings/                  SettingsStore (UserDefaults) + SettingsView
  Setup/                     EngineInstaller (first-run provisioning), SetupView
                             (setup assistant + permissions), AppMover
  Support/                   Theme, shared views, extensions
```

Each module is a self-contained `Manager` (state/logic) + `View` (SwiftUI). To add a
module: add a case to `ModuleID`, a manager to `AppEnvironment`, and a `case` in
`NotchModuleContainer`.

## Notes / possible extensions

- **Artwork**: Spotify via artwork URL, Music via raw artwork data.
- Not yet built (were in the original): camera mirror, app/URL bookmarks launcher,
  the mini-game, and the on-device "AI" refinements. The module system makes each a
  drop-in addition.
- Clipboard history is text-only by design (images/files intentionally skipped).
