# Live Dictation

Push-to-talk dictation for macOS: **hold Right Option, speak, release**, and the words are pasted into
whatever app has focus. A quick Option tap, or Option used with another key, is ignored, so shortcuts keep
working. Your clipboard text is restored after the paste.

- **Indicator:** a small click-through pill just above the Dock shows white voice-level bars while you
  speak and a purple pulse while it transcribes. Sounds: Tink = listening, Pop = transcribing, Basso = error.
- **Backends** (`settings.json` `{"backend": ...}`, else `DICTATION_BACKEND` in `.env`):
  - `ps5`: a whisper.cpp `whisper-server` (ggml-small, Vulkan) on a Linux box, reached through an SSH tunnel
    (`ps5_backend.py`; in the reference setup a PS5 running Linux on a direct Ethernet link). A 3-second
    phrase transcribes in ~0.2 s, ~0.8 s end to end. No audio leaves the two machines.
  - `openai`: OpenAI `whisper-1` (needs `OPENAI_API_KEY`).
- **Log:** `dictation.log` (one line per dictation: audio length, characters, time; permission status at start).

## Setup

```sh
conda create -n live_dictation python=3.12 -y
conda run -n live_dictation pip install -r requirements.txt
cp .env.example .env          # OPENAI_API_KEY and/or PS5_* SSH settings
./build_app.sh                # builds ~/Applications/Live Dictation.app
open ~/Applications/"Live Dictation.app"
```

Add the app to **System Settings > General > Login Items** (or use the `osascript` line `build_app.sh`
prints) so it starts at login. Grant it **Accessibility** (to paste), **Input Monitoring** (to see the key)
and **Microphone**; reopen it afterwards. Rebuilding changes the ad-hoc signature, so macOS may ask again. On macOS 27 Accessibility is listed
as **Device Control and Data Access**. If the app won't appear there after **+**, an older install with a
different bundle id may still hold the entry: rebuild with `BUNDLE_ID=<old id> ./build_app.sh`, or run
`tccutil reset Accessibility <old id>` while that id is installed.

### Why a compiled launcher

The app's executable (`macos-app/launcher.swift`) starts `dictation.py` as a *child* process. If the bundle
executable is a shell script that execs Python, macOS attributes the permissions to `python3.12` instead of
the app. The launcher is built with `-target <arch>-apple-macos13.0` because a newer SDK can default to a
minimum macOS above the installed one, and the app then refuses to open (LaunchServices error -10825).
It restarts the script after a crash and gives up after three quick failures.

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DICTATION_KEY` | `right_option` | also `left_option`, `right_command`, `right_control`, `fn` |
| `DICTATION_LANGUAGE` | `CAPTION_LANGUAGE` or `en` | language code, or `auto` |
| `DICTATION_BACKEND` | `openai` | `ps5` or `openai` (settings.json wins) |
| `PS5_HOST`, `PS5_SSH_USER`, `PS5_SSH_KEY`, `PS5_KNOWN_HOSTS` | `192.168.77.2`, `root`, ssh defaults | remote GPU server over SSH |

The remote server must run with `-nc`, and every request sends `no_context=true` plus an explicit `prompt`:
whisper-server otherwise carries text and parameters between requests.

Companion project: [live-captions](https://github.com/LEE-CHENYU/live-captions) (live captions + transcripts).
