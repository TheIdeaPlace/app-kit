"""Sound schemes and scheme files, as ``sounds/SCHEMES.md`` in app-kit sets them out.

Each app has its own events, listed in ``apps.json`` (a copy of app-kit's ``sounds/apps.json``
ships in this package). A scheme is a name and a stored sound for each event; a scheme file is a
ZIP of ``scheme.json`` and the sound files it uses. Everything read from a file is checked here,
with the limits counted from what is actually read, so an app can hand this a file from anywhere.

No wx and no network, so all of it can be tested.
"""
from __future__ import annotations

import io
import json
import os
import re
import unicodedata
import wave
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from .sounds import MAX_BYTES as MAX_SOUND_BYTES
from .sounds import MAX_SECONDS as MAX_SOUND_SECONDS
from .sounds import safe_file_name

FORMAT = 1
#: The packs every built-in sound comes in, which are also the built-in schemes.
PACKS: Dict[str, str] = {"glass": "Glass", "softbell": "Soft bell", "wood": "Wood"}
DEFAULT_PACK = "glass"

MAX_NAME = 60
MAX_AUTHOR = 60
MAX_LICENSE = 40
MAX_DESCRIPTION = 300
MAX_FILE_NAME = 100
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_ENTRIES = 64
MAX_JSON_BYTES = 64 * 1024
MAX_EVENTS = 100

NONE = "none"
_BUILT_IN = "builtin:"
_CUSTOM = "custom:"
_KEY = re.compile(r"^[A-Za-z0-9]{1,40}$")
#: The same scheme always gives the same bytes: the gallery names its files by their hash.
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
IMPORTED_NAME = "Imported scheme"


class SchemeFileProblem(Exception):
    """A scheme file that can't be used. Its text can be shown as it is."""


# -- each app's events ---------------------------------------------------------------


@dataclass(frozen=True)
class AppEvent:
    key: str
    label: str
    sound: str


@dataclass(frozen=True)
class AppCatalog:
    id: str
    name: str
    extension: str
    events: Tuple[AppEvent, ...]

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(e.key for e in self.events)

    @property
    def built_in_sounds(self) -> Tuple[str, ...]:
        """The built-in sounds this app ships: one per event, in event order."""
        return tuple(dict.fromkeys(e.sound for e in self.events))

    def event(self, key: str) -> Optional[AppEvent]:
        return next((e for e in self.events if e.key == key), None)


@lru_cache(maxsize=None)
def _apps() -> Mapping[str, AppCatalog]:
    raw = json.loads((Path(__file__).with_name("apps.json")).read_text(encoding="utf-8"))
    return {app_id: AppCatalog(app_id, app["name"], app["extension"],
                               tuple(AppEvent(e["key"], e["label"], e["sound"]) for e in app["events"]))
            for app_id, app in raw["apps"].items()}


def catalog(app_id: str) -> AppCatalog:
    """The app's events and sounds. Raises KeyError for an app app-kit doesn't know."""
    return _apps()[app_id]


def app_name(app_id: str) -> str:
    """An app's name to show, or its id when app-kit doesn't know it."""
    app = _apps().get(app_id)
    return app.name if app else app_id


# -- a stored sound ------------------------------------------------------------------


@dataclass(frozen=True)
class SoundRef:
    """One event's sound: ``none``, a built-in sound (in a pack, or in the scheme's), or a file."""
    kind: str  # "none", "builtin" or "custom"
    name: str = ""
    pack: str = ""  # "" for a built-in sound that plays in the scheme's pack

    def __str__(self) -> str:
        if self.kind == "builtin":
            return f"{_BUILT_IN}{self.pack}/{self.name}" if self.pack else _BUILT_IN + self.name
        if self.kind == "custom":
            return _CUSTOM + self.name
        return NONE


SILENT = SoundRef("none")


def custom_name_ok(name: str, allow_mp3: bool = False) -> bool:
    """A file name that's safe to keep and join to the sounds folder, and one an app plays."""
    if not isinstance(name, str) or not safe_file_name(name) or len(name) > MAX_FILE_NAME:
        return False
    if name != name.strip():
        return False
    return name.lower().endswith((".wav", ".mp3") if allow_mp3 else ".wav")


def parse_ref(value: object, app: AppCatalog, allow_mp3: bool = False) -> Optional[SoundRef]:
    """The stored sound, or None for a value that isn't one this app can play."""
    if not isinstance(value, str):
        return None
    lower = value.lower()
    if lower == NONE:
        return SILENT
    if lower.startswith(_BUILT_IN):
        rest = value[len(_BUILT_IN):]
        pack, slash, name = rest.rpartition("/")
        if slash and pack.lower() not in PACKS:
            return None
        if name.lower() not in app.built_in_sounds:
            return None
        return SoundRef("builtin", name.lower(), pack.lower() if slash else "")
    if lower.startswith(_CUSTOM):
        name = value[len(_CUSTOM):]
        return SoundRef("custom", name) if custom_name_ok(name, allow_mp3) else None
    return None


def default_ref(app: AppCatalog, key: str) -> SoundRef:
    event = app.event(key)
    return SoundRef("builtin", event.sound) if event else SILENT


def resolve(app: AppCatalog, key: str, stored: Mapping[str, str], built_in: bool,
            allow_mp3: bool = False) -> SoundRef:
    """What an event plays in a scheme: what's stored if it parses, else the event's own
    built-in sound in a built-in scheme, and nothing in a scheme of your own."""
    ref = parse_ref(stored.get(key), app, allow_mp3)
    if ref is not None:
        return ref
    return default_ref(app, key) if built_in else SILENT


def pack_for(ref: SoundRef, scheme_pack: Optional[str]) -> str:
    """The pack a built-in sound plays in: its own, the scheme's, or Glass."""
    if ref.pack:
        return ref.pack
    return scheme_pack if scheme_pack in PACKS else DEFAULT_PACK


def portable(app: AppCatalog, stored: Mapping[str, str], built_in: bool,
             scheme_pack: Optional[str]) -> Dict[str, str]:
    """Every event's sound as it should travel: a built-in sound with no pack of its own gets
    the one it played in, so it sounds the same in whatever scheme it lands in."""
    out: Dict[str, str] = {}
    for key in app.keys:
        ref = resolve(app, key, stored, built_in)
        if ref.kind == "builtin" and not ref.pack:
            ref = SoundRef("builtin", ref.name, pack_for(ref, scheme_pack))
        out[key] = str(ref)
    return out


# -- names ---------------------------------------------------------------------------


def utf16_length(text: str) -> int:
    """Length as QuickMail counts it: UTF-16 code units, so an emoji is two."""
    return len(text.encode("utf-16-le")) // 2


def _is_control(c: str) -> bool:
    return unicodedata.category(c) == "Cc"


def cut(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` UTF-16 units without splitting a character."""
    out, used = [], 0
    for c in text:
        size = 2 if ord(c) > 0xFFFF else 1
        if used + size > limit:
            break
        out.append(c)
        used += size
    return "".join(out)


def clean_line(text: object, limit: int) -> str:
    """One line of text from a file: control characters removed, trimmed, cut to ``limit``."""
    if not isinstance(text, str):
        return ""
    return cut("".join(c for c in text if not _is_control(c)).strip(), limit).strip()


def name_problem(name: str, taken: Iterable[str] = ()) -> Optional[str]:
    """Why ``name`` can't be a scheme's name, in words to show, or None."""
    name = name.strip()
    if not name:
        return "Type a name for the scheme."
    if any(_is_control(c) for c in name):
        return "A scheme's name can't have control characters."
    if utf16_length(name) > MAX_NAME:
        return f"A scheme's name can be at most {MAX_NAME} characters."
    if name.casefold() in {t.casefold() for t in taken}:
        return f"There's already a scheme called {name}."
    return None


def unique_name(name: str, taken: Iterable[str]) -> str:
    """``name``, or ``name 2``, ``name 3`` and so on, whichever isn't taken, within 60 characters."""
    folded = {t.casefold() for t in taken}
    if name.casefold() not in folded:
        return name
    number = 2
    while True:
        suffix = f" {number}"
        candidate = cut(name, MAX_NAME - len(suffix)).rstrip() + suffix
        if candidate.casefold() not in folded:
            return candidate
        number += 1


# -- sound files ---------------------------------------------------------------------


def wav_problem(data: bytes) -> Optional[str]:
    """Why these bytes aren't a sound every app can play (a 16-bit PCM WAV, mono or stereo,
    8 to 192 kHz, at most 30 seconds and 5 MB), or None."""
    if len(data) > MAX_SOUND_BYTES:
        return "It's larger than 5 MB."
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            params = reader.getparams()
            if params.sampwidth != 2 or params.nchannels not in (1, 2):
                return "It has to be a 16-bit WAV file, mono or stereo."
            if not 8000 <= params.framerate <= 192000:
                return "Its sample rate isn't one that can be played."
            if params.nframes <= 0:
                return "It has no sound in it."
            if params.nframes / params.framerate > MAX_SOUND_SECONDS:
                return f"It's longer than {MAX_SOUND_SECONDS:g} seconds."
            frames = reader.readframes(params.nframes)
    except (EOFError, wave.Error, ValueError, OSError) as exc:
        return f"It can't be read as a WAV file ({exc})."
    if len(frames) < 2 * params.nchannels:
        return "It has no sound in it."
    return None


# -- scheme files --------------------------------------------------------------------


@dataclass
class SchemeFile:
    """What a scheme file holds, once checked. ``events`` has only this app's keys and values
    that parse; ``sounds`` has only files the events use that passed ``playable``."""
    app: str
    name: str
    events: Dict[str, str]
    author: Optional[str] = None
    license: Optional[str] = None
    description: Optional[str] = None
    sounds: Dict[str, bytes] = field(default_factory=dict)
    #: Events whose file was missing from the ZIP or couldn't be played, now silent.
    dropped: int = 0


def _read_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int, budget: List[int]) -> bytes:
    """An entry's bytes, counted as they're read, not taken from what the archive says."""
    out = bytearray()
    with zf.open(info) as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return bytes(out)
            out += chunk
            budget[0] -= len(chunk)
            if len(out) > limit or budget[0] < 0:
                raise SchemeFileProblem("This sound scheme is too large to use.")


def read_scheme_file(source: Union[str, os.PathLike, bytes], app: AppCatalog, *,
                     playable: Callable[[str, bytes], bool] = lambda name, data: wav_problem(data) is None,
                     allow_mp3: bool = False,
                     have_file: Callable[[str], bool] = lambda name: False) -> SchemeFile:
    """Read and check a scheme file for ``app``. Raises SchemeFileProblem with words to show.

    ``have_file(name)`` says a ``custom:`` file missing from the ZIP is already in the app's sounds
    folder, so the event keeps it. ``playable`` says whether the app can play a sound's bytes."""
    unreadable = f"This file could not be read as a sound scheme for {app.name}."
    if isinstance(source, (bytes, bytearray)):
        data = io.BytesIO(bytes(source))
        size = len(source)
    else:
        try:
            size = os.path.getsize(source)
        except OSError as exc:
            raise SchemeFileProblem(f"The file could not be opened ({exc.strerror or exc}).") from exc
        data = source
    if size > MAX_FILE_BYTES:
        raise SchemeFileProblem("This sound scheme is too large to use.")
    try:
        with zipfile.ZipFile(data) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                raise SchemeFileProblem(unreadable)
            budget = [MAX_FILE_BYTES]
            meta_bytes: Optional[bytes] = None
            entries: Dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name == "scheme.json" and meta_bytes is None:
                    meta_bytes = _read_capped(zf, info, MAX_JSON_BYTES, budget)
                elif name.startswith("sounds/") and custom_name_ok(name[len("sounds/"):], allow_mp3):
                    entries.setdefault(name[len("sounds/"):], info)
            if meta_bytes is None:
                raise SchemeFileProblem(unreadable)
            meta = _json_object(meta_bytes, unreadable)
            scheme = _scheme_from_json(meta, app, unreadable, allow_mp3)
            used = {ref.name for ref in (parse_ref(v, app, allow_mp3) for v in scheme.events.values())
                    if ref is not None and ref.kind == "custom"}
            dropped_files = set()
            for name in sorted(used):
                info = entries.get(name)
                if info is None:
                    if not have_file(name):
                        dropped_files.add(name)
                    continue
                try:
                    sound = _read_capped(zf, info, MAX_SOUND_BYTES, budget)
                except SchemeFileProblem:
                    if budget[0] < 0:
                        raise
                    dropped_files.add(name)
                    continue
                if playable(name, sound):
                    scheme.sounds[name] = sound
                else:
                    dropped_files.add(name)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, EOFError) as exc:
        raise SchemeFileProblem(unreadable) from exc
    except OSError as exc:
        raise SchemeFileProblem(f"The file could not be read ({exc.strerror or exc}).") from exc
    for key, value in list(scheme.events.items()):
        ref = parse_ref(value, app, allow_mp3)
        if ref is not None and ref.kind == "custom" and ref.name in dropped_files:
            scheme.events[key] = NONE
            scheme.dropped += 1
    return scheme


def _json_object(raw: bytes, unreadable: str) -> dict:
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise SchemeFileProblem(unreadable) from exc
    if not isinstance(value, dict):
        raise SchemeFileProblem(unreadable)
    return value


def _scheme_from_json(meta: dict, app: AppCatalog, unreadable: str, allow_mp3: bool) -> SchemeFile:
    fmt = meta.get("format")
    if type(fmt) is not int or fmt < 1:
        raise SchemeFileProblem(unreadable)
    if fmt > FORMAT:
        raise SchemeFileProblem(f"This sound scheme was made by a newer version of {app.name}. "
                                f"Update {app.name} and try again.")
    owner = meta.get("app", "quickmail")
    if owner != app.id:
        other = app_name(owner) if isinstance(owner, str) and owner else "another app"
        raise SchemeFileProblem(f"This is a sound scheme for {other}, so {app.name} can't use it.")
    events = meta.get("events")
    if not isinstance(events, dict):
        raise SchemeFileProblem(unreadable)
    kept: Dict[str, str] = {}
    for count, (key, value) in enumerate(events.items()):
        if count >= MAX_EVENTS:
            break
        if not isinstance(key, str) or not _KEY.match(key) or key not in app.keys or key in kept:
            continue
        ref = parse_ref(value, app, allow_mp3)
        kept[key] = str(ref) if ref is not None else NONE
    return SchemeFile(
        app=app.id,
        name=clean_line(meta.get("name"), MAX_NAME) or IMPORTED_NAME,
        events=kept,
        author=clean_line(meta.get("author"), MAX_AUTHOR) or None,
        license=clean_line(meta.get("license"), MAX_LICENSE) or None,
        description=clean_line(meta.get("description"), MAX_DESCRIPTION) or None)


def write_scheme_file(scheme: SchemeFile, app: AppCatalog,
                      read_sound: Callable[[str], Optional[bytes]] = lambda name: None) -> bytes:
    """A scheme file's bytes, the same for the same scheme every time.

    ``scheme.events`` should already be ``portable``. A ``custom:`` file's bytes come from
    ``scheme.sounds`` or ``read_sound(name)``; one that's missing, over 5 MB, or would take the
    file past 80 MB is left out, and its events are written as ``none``."""
    events: Dict[str, str] = {}
    files: Dict[str, bytes] = {}
    total = 0
    for key in app.keys:
        ref = parse_ref(scheme.events.get(key, NONE), app, allow_mp3=True) or SILENT
        if ref.kind == "custom" and ref.name not in files:
            data = scheme.sounds.get(ref.name)
            if data is None:
                data = read_sound(ref.name)
            if data is None or len(data) > MAX_SOUND_BYTES or total + len(data) > MAX_FILE_BYTES - MAX_JSON_BYTES:
                ref = SILENT
            else:
                files[ref.name] = data
                total += len(data)
        events[key] = str(ref)
    body = {"format": FORMAT, "app": app.id, "name": scheme.name, "author": scheme.author,
            "license": scheme.license}
    if scheme.description:
        body["description"] = scheme.description
    body["events"] = events
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
        add("scheme.json", json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8"))
        for name in sorted(files):
            add("sounds/" + name, files[name])
    return buffer.getvalue()


# -- putting an imported scheme's sounds in place ------------------------------------


@dataclass
class Installed:
    """An imported scheme's events once its sounds are in the app's folder."""
    events: Dict[str, str]
    #: Files written, by the name they have now.
    added: List[str]


def install_sounds(scheme: SchemeFile, folder: Union[str, os.PathLike]) -> Installed:
    """Write a scheme file's sounds into ``folder``. A file whose name is taken by different
    bytes is written as ``<name> 2.wav`` and so on, and the events that use it follow; the same
    bytes under the same name are used as they are."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    renamed: Dict[str, str] = {}
    added: List[str] = []
    for name in sorted(scheme.sounds):
        data = scheme.sounds[name]
        stem, ext = os.path.splitext(name)
        candidate, number = name, 2
        while True:
            path = folder / candidate
            try:
                if path.read_bytes() == data:
                    break
            except FileNotFoundError:
                path.write_bytes(data)
                added.append(candidate)
                break
            suffix = f" {number}"
            candidate = stem[: MAX_FILE_NAME - len(ext) - len(suffix)] + suffix + ext
            number += 1
        renamed[name] = candidate
    events = {}
    for key, value in scheme.events.items():
        if value.lower().startswith(_CUSTOM):
            name = value[len(_CUSTOM):]
            value = _CUSTOM + renamed.get(name, name)
        events[key] = value
    return Installed(events, added)
