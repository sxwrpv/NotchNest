# NotchNest (local build)

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
| **File Tray**   | Drag files onto the notch to stash them, drag them back out to any app, double-click to open, right-click to Reveal in Finder / Share (AirDrop) / Remove. Persists across launches via bookmarks. |
| **Clipboard**   | Searchable, pinnable text history. Click any entry to re-copy. Pinned items never expire. |
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
  on first use. Note: the ad-hoc code signature changes on every rebuild, so
  the grant must be re-ticked after rebuilding.
- **Web API (optional):** real liked-state sync via OAuth/PKCE (Settings →
  Spotify). Spotify policy (since 2025) requires the developer-app owner to
  have **Premium**; on a free account the API returns 403 and NotchNest
  permanently falls back to local control (clicking Connect retries the API).

## Build & run

```bash
./build.sh          # builds NotchNest.app
./build.sh --run    # builds and launches it
```

`build.sh` runs `swift build -c release`, assembles `NotchNest.app`, and ad-hoc signs it.
To install permanently, drag `NotchNest.app` into `/Applications` (recommended before
enabling "Launch at login").

## Permissions (macOS will prompt on first use)

- **Automation** → to read/control Music & Spotify (Now Playing).
- **Calendar** → to show today's events.
- **Notifications** → for Pomodoro alerts.

If you deny one by mistake: System Settings → Privacy & Security → the relevant section.

## Dictation engine

NotchNest is **one app**: the former standalone Murmur dictation app is merged
in as `DictationEngine/` (Python + MLX Whisper, with its own 1.2 GB `.venv`
that stays outside the `.app` bundle). NotchNest owns the engine's lifecycle:

- On launch, `DictationManager` spawns `DictationEngine/.venv/bin/python -u
  main.py --headless` as a child process (no menu-bar icon, no dock, no pill
  overlay — the notch is the only UI), auto-restarts it if it dies, and
  terminates it on quit. The engine keeps a single-instance guard as a backstop.
- User state stays in `~/.murmur/` (config.yaml, history db, log).

They talk through two files in `~/.murmur`:

```
notch.json   engine -> NotchNest   {"state","text","ts","running","settings"}   (~1s heartbeat)
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
