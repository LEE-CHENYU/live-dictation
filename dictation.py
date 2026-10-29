"""Everywhere dictation: hold the hotkey (default Right Option), speak, release, and the words are typed
into whatever app has focus.

Backend (settings.json, else DICTATION_BACKEND in .env): "ps5" = a whisper.cpp GPU server on a Linux box
reached over SSH (see ps5_backend.py), or "openai" = OpenAI whisper-1. Runs in the background as
"Live Dictation.app" (build_app.sh; add it to Login Items to start at login).

macOS permissions it needs once: Microphone, Input Monitoring (to see the hotkey) and Accessibility (to
paste into the focused app).
"""

from __future__ import annotations

import fcntl
import io
import os
import queue
import sys
import threading
import json
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

import math

import objc
import Quartz
from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered, NSBezierPath, NSColor,
    NSMakeRect, NSPanel, NSPasteboard, NSPasteboardTypeString, NSScreen, NSSound, NSStatusWindowLevel, NSTimer,
    NSView, NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
)
from PyObjCTools import AppHelper
from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

import ps5_backend

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
LOG = ROOT / "dictation.log"
RATE = 16_000
MIN_HOLD = 0.35  # shorter presses are ignored (a normal Option tap)
MAX_SECONDS = 120
KEYS = {"right_option": 61, "left_option": 58, "right_command": 54, "right_control": 62, "fn": 63}
KEY_NAME = os.getenv("DICTATION_KEY", "right_option").strip().lower()
KEYCODE = KEYS.get(KEY_NAME, 61)
FLAG = {61: Quartz.kCGEventFlagMaskAlternate, 58: Quartz.kCGEventFlagMaskAlternate,
        54: Quartz.kCGEventFlagMaskCommand, 62: Quartz.kCGEventFlagMaskControl,
        63: Quartz.kCGEventFlagMaskSecondaryFn}[KEYCODE]
V_KEY = 9


def log(message: str) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def backend() -> str:
    """settings.json {"backend": ...}, else DICTATION_BACKEND in .env, else openai."""
    try:
        choice = json.loads((ROOT / "settings.json").read_text(encoding="utf-8")).get("backend")
    except (OSError, ValueError):
        choice = None
    choice = choice or os.getenv("DICTATION_BACKEND", "openai").strip().lower()
    return choice if choice in ("openai", "ps5") else "openai"


def sound(name: str) -> None:
    s = NSSound.soundNamed_(name)
    if s is not None:
        s.play()


def transcribe(audio: np.ndarray) -> str:
    pcm = ps5_backend.to_whisper_pcm(audio, RATE)
    language = os.getenv("DICTATION_LANGUAGE", ps5_backend.language())
    if backend() == "ps5":
        ps5_backend.ensure_server()
        fields = {"response_format": "json", "language": language, "temperature": "0.0",
                  "no_context": "true", "prompt": ""}
        return ps5_backend.clean_text(str(ps5_backend.inference(ps5_backend.wav_bytes(pcm), timeout=60, **fields).get("text", "")))
    from openai import OpenAI
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing from .env")
    buffer = io.BytesIO(ps5_backend.wav_bytes(pcm))
    buffer.name = "dictation.wav"
    kwargs = {} if language == "auto" else {"language": language}
    result = OpenAI(api_key=key, timeout=60).audio.transcriptions.create(model="whisper-1", file=buffer, **kwargs)
    return ps5_backend.clean_text(result.text)


def paste(text: str) -> None:
    """Put text on the clipboard, press Cmd+V in the focused app, then restore the previous clipboard text."""
    board = NSPasteboard.generalPasteboard()
    previous = board.stringForType_(NSPasteboardTypeString)
    board.clearContents()
    board.setString_forType_(text, NSPasteboardTypeString)
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, V_KEY, down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.6)  # let the target app read the clipboard before restoring it
    if previous is not None and board.stringForType_(NSPasteboardTypeString) == text:
        board.clearContents()
        board.setString_forType_(previous, NSPasteboardTypeString)


class BarsView(NSView):
    """A small dark pill with 9 rounded bars: live voice level while listening, a soft pulse while working."""

    def initWithFrame_(self, frame):
        self = objc.super(BarsView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.levels = [0.0] * 9
        self.working = False
        return self

    def drawRect_(self, _rect):
        b = self.bounds()
        h = b.size.height
        NSColor.colorWithCalibratedWhite_alpha_(0.07, 0.94).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, h / 2, h / 2).fill()
        if self.working:
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.56, 1.0, 1.0).setFill()
        else:
            NSColor.whiteColor().setFill()
        width, gap = 3.0, 3.0
        x0 = (b.size.width - (len(self.levels) * (width + gap) - gap)) / 2
        for i, level in enumerate(self.levels):
            bar = max(3.0, min(h - 10.0, 3.0 + level * (h - 10.0)))
            rect = NSMakeRect(x0 + i * (width + gap), (h - bar) / 2, width, bar)
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, width / 2, width / 2).fill()


class Overlay:
    """Tiny always-on-top, click-through indicator just above the Dock (main thread only)."""

    W, H = 92.0, 26.0

    def __init__(self) -> None:
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.W, self.H),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        self.panel.setLevel_(NSStatusWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setHasShadow_(True)
        self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | NSWindowCollectionBehaviorFullScreenAuxiliary
                                          | NSWindowCollectionBehaviorStationary)
        self.view = BarsView.alloc().initWithFrame_(NSMakeRect(0, 0, self.W, self.H))
        self.panel.setContentView_(self.view)
        self.level = 0.0  # written by the audio thread
        self.phase = 0.0
        self.timer = None

    def show(self, working: bool) -> None:
        frame = NSScreen.mainScreen().visibleFrame()  # above the Dock, on the active screen
        self.panel.setFrameOrigin_((frame.origin.x + (frame.size.width - self.W) / 2, frame.origin.y + 18))
        self.view.working = working
        if not working:
            self.view.levels = [0.0] * len(self.view.levels)
        self.panel.orderFrontRegardless()
        if self.timer is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1 / 30, True, lambda _t: self.tick())

    def hide(self) -> None:
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None
        self.panel.orderOut_(None)

    def tick(self) -> None:
        levels = self.view.levels
        if self.view.working:
            self.phase += 0.18
            self.view.levels = [0.22 + 0.18 * (1 + math.sin(self.phase - i * 0.7)) for i in range(len(levels))]
        else:  # scroll the voice level in from the right; speech RMS ~0.01-0.2 -> 0..1
            self.view.levels = levels[1:] + [min(1.0, math.sqrt(self.level) * 2.6)]
        self.view.setNeedsDisplay_(True)


class Dictation:
    def __init__(self) -> None:
        self.overlay = Overlay()
        self.stream: sd.InputStream | None = None
        self.blocks: list[np.ndarray] = []
        self.pressed_at = 0.0
        self.recording = False
        self.cancelled = False
        self.jobs: queue.Queue[np.ndarray] = queue.Queue()
        threading.Thread(target=self.worker, daemon=True).start()
        if backend() == "ps5":  # warm the tunnel so the first dictation is quick
            threading.Thread(target=self.warm, daemon=True).start()

    def warm(self) -> None:
        try:
            ps5_backend.ensure_server()
        except Exception as error:
            log(f"PS5 warm-up: {error}")

    def start(self) -> None:
        self.blocks, self.cancelled, self.recording = [], False, True
        self.pressed_at = time.monotonic()
        try:
            self.stream = sd.InputStream(channels=1, samplerate=RATE, dtype="float32", blocksize=RATE // 30,
                                         callback=self.on_audio)
            self.stream.start()
        except Exception as error:
            self.recording = False
            log(f"microphone: {error}")
            sound("Basso")
            return
        AppHelper.callLater(MIN_HOLD, self.confirm_listening)

    def on_audio(self, data, *_):
        block = data[:, 0].copy()
        self.blocks.append(block)
        self.overlay.level = float(np.sqrt(np.mean(block * block)))

    def confirm_listening(self) -> None:
        if self.recording and not self.cancelled:  # still held: a real dictation, not an Option tap
            sound("Tink")
            self.overlay.show(working=False)

    def stop(self) -> None:
        if not self.recording:
            return
        self.recording = False
        held = time.monotonic() - self.pressed_at
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.cancelled or held < MIN_HOLD or not self.blocks:
            self.overlay.hide()
            return
        sound("Pop")
        self.overlay.show(working=True)
        self.jobs.put(np.concatenate(self.blocks)[: RATE * MAX_SECONDS])

    def worker(self) -> None:
        while True:
            audio = self.jobs.get()
            started = time.monotonic()
            try:
                text = transcribe(audio)
                if text:
                    paste(text + " ")
                log(f"{backend()}: {len(audio) / RATE:.1f}s audio -> {len(text)} chars in {time.monotonic() - started:.2f}s")
            except Exception as error:
                log(f"failed: {error}")
                sound("Basso")
            finally:
                if self.jobs.empty():
                    AppHelper.callAfter(self.overlay.hide)

    def on_event(self, _proxy, kind, event, _refcon):
        if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)
            return event
        if kind == Quartz.kCGEventKeyDown and self.recording:
            self.cancelled = True  # Option+key is a shortcut, not dictation
        elif kind == Quartz.kCGEventFlagsChanged:
            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if code == KEYCODE:
                down = bool(Quartz.CGEventGetFlags(event) & FLAG)
                if down and not self.recording:
                    self.start()
                elif not down:
                    self.stop()
            elif self.recording:
                self.cancelled = True  # another modifier joined in: a shortcut
        return event

    def run(self) -> None:
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                           Quartz.kCGEventTapOptionListenOnly, mask, self.on_event, None)
        if self.tap is None:
            log("no event tap: grant Input Monitoring to Live Dictation, then reopen it")
            Quartz.CGRequestListenEventAccess()
            sys.exit(1)
        source = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self.tap, True)
        log(f"ready: hold {KEY_NAME} to dictate; backend {backend()}")
        AppHelper.runEventLoop()


def main() -> None:
    lock = open(ROOT / ".dictation.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("Live Dictation is already running")
    accessibility = bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    listen = bool(Quartz.CGPreflightListenEventAccess()) or bool(Quartz.CGRequestListenEventAccess())
    try:  # open the microphone for a moment so macOS asks once and lists Live Dictation under Microphone
        audio, _ = sd.rec(int(RATE * 0.4), samplerate=RATE, channels=1, dtype="float32"), sd.wait()
        peak = float(np.max(np.abs(audio)))
        microphone = f"ok (peak {peak:.4f})" if peak > 0 else "silent: Microphone access denied?"
    except Exception as error:
        microphone = f"failed: {error}"
    log(f"permissions: accessibility={accessibility} input_monitoring={listen} microphone={microphone}")
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    Dictation().run()


if __name__ == "__main__":
    main()
