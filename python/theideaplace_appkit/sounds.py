"""Short sounds for an app: which ones play, and playing them, on Windows, macOS and Linux.

No wx, and nothing outside the standard library. Why it plays sound this way rather than through
``winsound`` or ``wx.adv.Sound``, measured on Windows: ``PlaySound`` held a 40 ms clip for about
500 ms and can't be cut off from another thread, so it can't keep up with a run of short sounds or
honour a mute. ``waveOut``, opened per sound, took about 75 ms and stops at once. The rules this
follows are in the repo's ``sounds/README.md``.

Use one ``SoundPlayer``. ``play`` returns at once and can be called from any thread, and no audio
problem ever reaches the caller.
"""
from __future__ import annotations

import array
import collections
import io
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Callable, Dict, Optional, Tuple

#: Fine to lose: says something is going on, and means nothing if it's late.
AMBIENT = "ambient"
#: An event. Waits its turn in a short queue.
NORMAL = "normal"
#: Something the person needs to hear: goes first, and cuts off anything ambient.
URGENT = "urgent"

#: Limits on any file. Checked when one is opened, not only when it's chosen, because a settings
#: file or a file copied in by hand never passes through a chooser.
MAX_SECONDS = 30
MAX_BYTES = 5 * 1024 * 1024
#: Silence after each sound played through waveOut, which reports a buffer done while its end is
#: still on the way to the speakers, so closing at that point would clip it.
LEAD_OUT_SECONDS = 0.1
#: How long the device stays open after a sound is cut off. Closed at once, about one in seven cuts
#: put a faint click out afterwards; held this long, none in 50 (measured through a virtual sound
#: card, with nothing else playing).
CUT_OFF_HOLD_SECONDS = 0.1
#: How long a person's long sound may keep something urgent waiting. Every built-in sound is over by
#: then, so this only ever cuts off a file of their own.
URGENT_WAIT_SECONDS = 1.5
#: A sample rate outside this isn't a real recording, and a tiny file claiming one could otherwise
#: make the lead-out a huge allocation.
_RATES = (8000, 192000)

_DEVICE_NAMES = {"con", "prn", "aux", "nul", "conin$", "conout$"}
# With the superscript digits Windows also reserves, as Python's own ntpath.isreserved has them.
for _digit in "0123456789\u00b9\u00b2\u00b3":
    _DEVICE_NAMES.update((f"com{_digit}", f"lpt{_digit}"))

_log = logging.getLogger(__name__)


class SoundProblem(Exception):
    """This file can't be played, or the sound device wouldn't play it. Its text can be shown."""


def safe_file_name(name: str) -> bool:
    """Whether ``name`` is safe to keep and join to a folder: a bare file name that isn't a path,
    and isn't a Windows device name such as ``COM1.wav``, which would open the device."""
    if not name or name != os.path.basename(name) or name in (".", ".."):
        return False
    if any(c in name for c in '/\\:*?"<>|') or any(ord(c) < 32 for c in name):
        return False
    if name != name.rstrip(" ."):
        return False  # Windows would quietly open a different name
    return name.split(".", 1)[0].rstrip(" ").lower() not in _DEVICE_NAMES


def read_wav(path: str, limit_seconds: float = MAX_SECONDS) -> Tuple[object, bytes]:
    """A 16-bit PCM WAV's format and samples. Raises SoundProblem for anything else."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise SoundProblem(f"It's larger than {MAX_BYTES // (1024 * 1024)} MB.")
        with open(path, "rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise SoundProblem(f"It can't be read as a WAV file ({exc}).") from exc
    return read_wav_bytes(data, limit_seconds)


def _format_tag(data: bytes) -> Optional[int]:
    """The ``fmt `` chunk's format tag, or None. Read here rather than left to ``wave``, which
    takes WAVE_FORMAT_EXTENSIBLE from Python 3.12 on but not before: every app and every Python
    has to accept the same files (sounds/SCHEMES.md), so only plain PCM (1) is."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    at = 12
    while at + 8 <= len(data):
        size = struct.unpack_from("<I", data, at + 4)[0]
        if data[at:at + 4] == b"fmt ":
            return struct.unpack_from("<H", data, at + 8)[0] if size >= 2 and at + 10 <= len(data) else None
        at += 8 + size + (size & 1)
    return None


def read_wav_bytes(data: bytes, limit_seconds: float = MAX_SECONDS, strict: bool = False) -> Tuple[object, bytes]:
    """``read_wav`` for bytes already read. ``strict``: only plain PCM (format tag 1), for a
    sound in a scheme file, which every app on every Python has to read alike. A person's own
    file isn't held to that, so a WAVE_FORMAT_EXTENSIBLE file that plays keeps playing."""
    if len(data) > MAX_BYTES:
        raise SoundProblem(f"It's larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if strict and _format_tag(data) != 1:
        raise SoundProblem("It has to be a 16-bit PCM WAV file.")
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            params = reader.getparams()
            if params.sampwidth != 2 or params.nchannels not in (1, 2):
                raise SoundProblem("It has to be a 16-bit WAV file, mono or stereo.")
            if not _RATES[0] <= params.framerate <= _RATES[1]:
                raise SoundProblem("Its sample rate isn't one that can be played.")
            if params.nframes / params.framerate > limit_seconds:
                raise SoundProblem(f"It's longer than {limit_seconds:g} seconds.")
            frames = reader.readframes(params.nframes)
    except SoundProblem:
        raise
    # RuntimeError and struct.error: what wave's chunk reader raises for a bad chunk size.
    except (OSError, EOFError, wave.Error, ValueError, RuntimeError, struct.error) as exc:
        raise SoundProblem(f"It can't be read as a WAV file ({exc}).") from exc
    # A file cut off partway through a frame: the device takes whole frames only.
    frames = frames[: len(frames) // (2 * params.nchannels) * (2 * params.nchannels)]
    if not frames:
        raise SoundProblem("It has no sound in it.")
    return params, frames


def scaled(frames: bytes, volume: float) -> bytes:
    """16-bit samples at ``volume`` (0 to 1). Done to the samples rather than the device, whose
    volume is the app's in the system mixer, and which the system would remember."""
    volume = max(0.0, min(1.0, volume))
    if volume >= 1.0:
        return frames
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    result = array.array("h", [int(s * volume) for s in samples])
    if sys.byteorder == "big":
        result.byteswap()
    return result.tobytes()


class SoundPolicy:
    """What's let through, so a busy app doesn't turn into a wall of noise. Pure, with its clock
    passed in, so every rule can be tested."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, same_gap: float = 1.0,
                 ambient_gap: float = 0.2, max_waiting: int = 3) -> None:
        self._clock = clock
        self.same_gap = same_gap
        self.ambient_gap = ambient_gap
        self.max_waiting = max_waiting
        self._last: Dict[str, float] = {}
        self._last_ambient: Optional[float] = None

    def admit(self, key: str, priority: str, busy: bool, waiting: int) -> bool:
        """Whether this sound plays. ``busy`` is whether something's playing, and ``waiting`` how
        many are queued. Records it if so."""
        now = self._clock()
        if priority == AMBIENT:
            if busy or waiting:
                return False  # a late tap says nothing
            if self._last_ambient is not None and now - self._last_ambient < self.ambient_gap:
                return False
            self._last_ambient = now
            return True
        last = self._last.get(key)
        if last is not None and now - last < self.same_gap:
            return False
        if priority != URGENT and waiting >= self.max_waiting:
            return False
        self._last[key] = now
        return True


class _Item:
    __slots__ = ("path", "key", "priority", "volume", "fallback", "preview", "cancel")

    def __init__(self, path, key, priority, volume, fallback, preview):
        self.path, self.key, self.priority = path, key, priority
        self.volume, self.fallback, self.preview = volume, fallback, preview
        self.cancel = threading.Event()


class SoundPlayer:
    """One sound at a time from a short queue, on one background thread."""

    def __init__(self, output=None, policy: Optional[SoundPolicy] = None) -> None:
        self._output = output
        self.policy = policy or SoundPolicy()
        self._lock = threading.Condition()
        self._queue: "collections.deque[_Item]" = collections.deque()
        self._current: Optional[_Item] = None
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        self._logged = set()

    def play(self, path: str, *, key: Optional[str] = None, priority: str = NORMAL,
             volume: float = 1.0, fallback: Optional[str] = None) -> bool:
        """Queue a sound. ``key`` is what counts as "the same sound" (the path if not given), and
        ``fallback`` is played instead if ``path`` can't be. True if it was let through."""
        item = _Item(str(path), key or str(path), priority, volume, fallback, False)
        with self._lock:
            if self._closed or not self.policy.admit(
                    item.key, priority, self._current is not None, len(self._queue)):
                return False
            if priority == URGENT:
                self._add_urgent(item)
            else:
                self._queue.append(item)
            return self._wake()

    def _add_urgent(self, item: _Item) -> None:
        """Ahead of anything that isn't urgent, and behind what's urgent already, so what must be
        heard is heard in order. A tap waiting now says nothing later, and something else gives
        way if the queue is over its limit. Never another urgent sound: this one was promised."""
        kept = [i for i in self._queue if i.priority != AMBIENT]
        urgent = sum(1 for i in kept if i.priority == URGENT)
        kept.insert(urgent, item)
        # From the end, never a preview: someone is waiting to hear that.
        for index in range(len(kept) - 1, urgent, -1):
            if len(kept) > self.policy.max_waiting and not kept[index].preview:
                kept.pop(index)
        self._queue = collections.deque(kept)
        current = self._current
        if current is None:
            return
        if current.priority == AMBIENT:
            current.cancel.set()
            return
        # Built-in sounds finish first. A person's own 25-second file, for any event, doesn't get
        # to make an alert 25 seconds late. Setting this on a sound that has ended does nothing.
        timer = threading.Timer(URGENT_WAIT_SECONDS, current.cancel.set)
        timer.daemon = True
        try:
            timer.start()
        except RuntimeError:
            current.cancel.set()  # no thread to wait with, so the alert doesn't wait

    def preview(self, path: str, *, volume: float = 1.0, fallback: Optional[str] = None) -> None:
        """Play a sound someone asked to hear, now: it ends what's playing unless that's urgent,
        and an earlier preview, and nothing that applies to ``play`` applies to it. Sounds waiting
        stay waiting: an alert isn't lost because someone was choosing a sound. ``fallback`` as in
        ``play``, so a preview is what would really be heard."""
        item = _Item(str(path), str(path), NORMAL, volume, fallback, True)
        with self._lock:
            if self._closed:
                return
            self._queue = collections.deque(i for i in self._queue if not i.preview)
            if self._current is not None and self._current.priority != URGENT:
                self._current.cancel.set()
            urgent = sum(1 for i in self._queue if i.priority == URGENT)
            self._queue.insert(urgent, item)
            self._wake()

    def stop(self) -> None:
        """Mute: cut off what's playing and forget what's waiting."""
        with self._lock:
            self._cancel_all()
            self._lock.notify_all()

    def stop_preview(self) -> None:
        """A preview's screen is closing: end its sound, and leave anything else alone."""
        with self._lock:
            self._queue = collections.deque(i for i in self._queue if not i.preview)
            if self._current is not None and self._current.preview:
                self._current.cancel.set()

    def close(self, wait: float = 1.0) -> None:
        """Stop for good. Waits briefly for the thread, which is a daemon if it doesn't end."""
        with self._lock:
            self._closed = True
            self._cancel_all()
            self._lock.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(wait)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._current is not None or bool(self._queue)

    def _cancel_all(self) -> None:
        self._queue.clear()
        if self._current is not None:
            self._current.cancel.set()

    def _wake(self) -> bool:
        if self._thread is None:
            thread = threading.Thread(target=self._run, name="sounds", daemon=True)
            try:
                thread.start()
            except RuntimeError as exc:
                # The interpreter is shutting down, or no thread can be had. Nothing can play.
                self._queue.clear()
                _log.warning("Couldn't start the sound thread: %s", exc)
                return False
            self._thread = thread
        self._lock.notify_all()
        return True

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._closed:
                    self._lock.wait()
                if self._closed:
                    return
                item = self._current = self._queue.popleft()
            try:
                self._play(item)
            finally:
                with self._lock:
                    self._current = None

    def _play(self, item: _Item) -> None:
        for path in (item.path, item.fallback):
            if not path or item.cancel.is_set():
                continue
            try:
                if self._output is None:
                    self._output = default_output()
                self._output.play(path, item.volume, item.cancel)
                return
            except Exception as exc:  # noqa: BLE001 - no audio problem may reach anything else
                # Once each, as with no sound device this happens every time.
                if path not in self._logged:
                    self._logged.add(path)
                    _log.warning("Couldn't play %s: %s", path, exc)

class NoOutput:
    """A platform with nothing to play sounds with. Having none is normal."""

    def play(self, path: str, volume: float, cancel: threading.Event) -> None:
        pass


class WaveOutOutput:
    """Windows' waveOut through ctypes. The device is opened per sound, so it follows the default
    device and doesn't hold it awake, and samples are kept in memory after the first play."""

    _MAPPER = 0xFFFFFFFF
    _EVENT = 0x50000  # CALLBACK_EVENT
    _DONE = 1  # WHDR_DONE
    _CACHE_BYTES = 16 * 1024 * 1024

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w
        self._c = ctypes
        self._winmm = ctypes.WinDLL("winmm")
        self._kernel = ctypes.WinDLL("kernel32")
        handle = ctypes.c_void_p

        class Format(ctypes.Structure):
            _fields_ = [("wFormatTag", w.WORD), ("nChannels", w.WORD),
                        ("nSamplesPerSec", w.DWORD), ("nAvgBytesPerSec", w.DWORD),
                        ("nBlockAlign", w.WORD), ("wBitsPerSample", w.WORD), ("cbSize", w.WORD)]

        class Header(ctypes.Structure):
            _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", w.DWORD),
                        ("dwBytesRecorded", w.DWORD), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", w.DWORD), ("dwLoops", w.DWORD),
                        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]

        self._Format, self._Header = Format, Header
        header = ctypes.POINTER(Header)
        for name, args in (
                ("waveOutOpen", [ctypes.POINTER(handle), w.UINT, ctypes.POINTER(Format),
                                 ctypes.c_size_t, ctypes.c_size_t, w.DWORD]),
                ("waveOutPrepareHeader", [handle, header, w.UINT]),
                ("waveOutUnprepareHeader", [handle, header, w.UINT]),
                ("waveOutWrite", [handle, header, w.UINT]),
                ("waveOutReset", [handle]),
                ("waveOutClose", [handle])):
            function = getattr(self._winmm, name)
            function.argtypes, function.restype = args, w.UINT
        self._kernel.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, ctypes.c_wchar_p]
        self._kernel.CreateEventW.restype = w.HANDLE
        self._kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self._kernel.WaitForSingleObject.restype = w.DWORD
        self._kernel.CloseHandle.argtypes = [w.HANDLE]
        self._cache: "collections.OrderedDict[tuple, tuple]" = collections.OrderedDict()
        #: Buffers a driver wouldn't give back. Kept for good: it may still write to them.
        self._stuck: list = []

    def _load(self, path: str, volume: float):
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size, round(volume, 3))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        params, frames = read_wav(path)
        silence = int(params.framerate * LEAD_OUT_SECONDS) * params.nchannels * 2
        loaded = (params, scaled(frames, volume) + bytes(silence))
        self._cache[key] = loaded
        # Plenty for every built-in sound. One long file of someone's own doesn't stay at many volumes.
        while (len(self._cache) > 1
               and sum(len(entry[1]) for entry in self._cache.values()) > self._CACHE_BYTES):
            self._cache.popitem(last=False)
        return loaded

    def play(self, path: str, volume: float, cancel: threading.Event) -> None:
        c = self._c
        params, data = self._load(path, volume)
        block = params.nchannels * 2
        fmt = self._Format(1, params.nchannels, params.framerate, params.framerate * block,
                           block, 16, 0)
        event = self._kernel.CreateEventW(None, False, False, None)
        if not event:
            raise SoundProblem("Windows wouldn't make an event for the sound device.")
        device = c.c_void_p()
        try:
            result = self._winmm.waveOutOpen(c.byref(device), self._MAPPER, c.byref(fmt), event,
                                             0, self._EVENT)
            if result:
                raise SoundProblem(f"No sound device (waveOutOpen {result}).")
            try:
                hung = False
                buffer = c.create_string_buffer(data, len(data))
                header = self._Header()
                header.lpData = c.cast(buffer, c.c_void_p)
                header.dwBufferLength = len(data)
                size = c.sizeof(self._Header)
                result = self._winmm.waveOutPrepareHeader(device, c.byref(header), size)
                if result:
                    raise SoundProblem(f"The sound device refused it (prepare {result}).")
                try:
                    result = self._winmm.waveOutWrite(device, c.byref(header), size)
                    if result:
                        raise SoundProblem(f"The sound device refused it (write {result}).")
                    seconds = len(data) / fmt.nAvgBytesPerSec
                    # Longer than the sound can be, so a driver that never reports the end can't
                    # hold the queue for good.
                    deadline = time.monotonic() + seconds + 5
                    while not header.dwFlags & self._DONE:
                        if cancel.is_set():
                            break
                        if time.monotonic() > deadline:
                            hung = True
                            break
                        self._kernel.WaitForSingleObject(event, 20)
                finally:
                    # Reset also returns the buffer, which Unprepare insists on.
                    self._winmm.waveOutReset(device)
                    if self._winmm.waveOutUnprepareHeader(device, c.byref(header), size):
                        self._stuck.append((buffer, header))
                    if cancel.is_set():
                        time.sleep(CUT_OFF_HOLD_SECONDS)
            finally:
                self._winmm.waveOutClose(device)
            if hung:
                raise SoundProblem("The sound device didn't finish the sound.")
        finally:
            self._kernel.CloseHandle(event)


class _CommandOutput:
    """A system player run per sound: killed to cut it off, and any failure reported."""

    def __init__(self, popen=subprocess.Popen) -> None:
        self._popen = popen
        self._checked: Dict[tuple, bool] = {}

    def _check(self, path: str) -> float:
        """That the file can be played, and how long it lasts."""
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
        if key not in self._checked:
            params, _frames = read_wav(path)
            self._checked[key] = params.nframes / params.framerate
        return self._checked[key]

    def _run(self, command, cancel: threading.Event, seconds: float) -> None:
        """``seconds`` is the sound's length: a player still going well after that has hung, as one
        waiting for a sound server that isn't there does, and mustn't hold the queue."""
        process = self._popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + seconds + 5
        while process.poll() is None:
            hung = time.monotonic() > deadline
            if cancel.is_set() or hung:
                process.terminate()
                try:
                    process.wait(1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if hung:
                    raise SoundProblem(f"{command[0]} didn't finish.")
                return
            time.sleep(0.01)
        if process.returncode:
            raise SoundProblem(f"{command[0]} couldn't play it (exit {process.returncode}).")


class AfplayOutput(_CommandOutput):
    """macOS's afplay: on every Mac, and it follows the default device."""

    def play(self, path: str, volume: float, cancel: threading.Event) -> None:
        seconds = self._check(path)
        self._run(["/usr/bin/afplay", "-v", f"{max(0.0, min(1.0, volume)):.2f}", path], cancel,
                  seconds)


class LinuxOutput(_CommandOutput):
    """PipeWire's, PulseAudio's or ALSA's player, whichever the system has."""

    def __init__(self, popen=subprocess.Popen, which=shutil.which) -> None:
        super().__init__(popen)
        self._commands = [name for name in ("pw-play", "paplay", "aplay") if which(name)]

    def play(self, path: str, volume: float, cancel: threading.Event) -> None:
        if not self._commands:
            return  # no player is the same as no sound device
        seconds = self._check(path)
        volume = max(0.0, min(1.0, volume))
        # Having a player doesn't mean its sound server is running: pipewire-bin can be installed
        # on a PulseAudio desktop. So each is tried in turn, and whichever works goes first from
        # then on. None is dropped for good: a sound server restarting, or one odd file, shouldn't
        # cost a player for the rest of the session.
        for command in list(self._commands):
            try:
                self._play_with(command, path, volume, cancel, seconds)
            except (SoundProblem, OSError):
                if cancel.is_set() or command == self._commands[-1]:
                    raise
                continue
            self._commands.remove(command)
            self._commands.insert(0, command)
            return

    def _play_with(self, command: str, path: str, volume: float, cancel: threading.Event,
                   seconds: float) -> None:
        if command == "pw-play":
            self._run(["pw-play", f"--volume={volume:.2f}", path], cancel, seconds)
        elif command == "paplay":
            # PulseAudio's scale is cubic: this value is the linear volume everything else uses.
            self._run(["paplay", f"--volume={int(volume ** (1 / 3) * 65536)}", path], cancel, seconds)
        else:
            self._aplay(path, volume, cancel, seconds)

    def _aplay(self, path: str, volume: float, cancel: threading.Event, seconds: float) -> None:
        # aplay has no volume, so it gets a scaled copy, unless nothing needs scaling.
        if volume >= 1.0:
            self._run(["aplay", "-q", path], cancel, seconds)
            return
        params, frames = read_wav(path)
        handle, copy = tempfile.mkstemp(suffix=".wav", prefix="sound-")
        os.close(handle)
        try:
            with wave.open(copy, "wb") as writer:
                writer.setparams(params)
                writer.writeframes(scaled(frames, volume))
            self._run(["aplay", "-q", copy], cancel, seconds)
        finally:
            try:
                os.unlink(copy)
            except OSError:
                pass


def default_output():
    """The output for this platform."""
    if sys.platform == "win32":
        return WaveOutOutput()
    if sys.platform == "darwin":
        return AfplayOutput()
    if sys.platform.startswith("linux"):
        return LinuxOutput()
    return NoOutput()


def quiet_now() -> bool:
    """Whether sounds that happen on their own should hold back: a presentation or a full-screen
    program on Windows. This doesn't report Do Not Disturb, and Microsoft documents nothing that
    does (measured); say so in the app's guide. False elsewhere, and whenever Windows won't say."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        state = ctypes.c_int()
        result = ctypes.WinDLL("shell32").SHQueryUserNotificationState(ctypes.byref(state))
    except (OSError, AttributeError):
        return False
    # QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE.
    return result == 0 and state.value in (2, 3, 4)
