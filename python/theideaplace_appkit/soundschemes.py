"""Sound schemes and scheme files, as ``sounds/SCHEMES.md`` in app-kit sets them out.

Each app has its own events, listed in ``apps.json`` (a copy of app-kit's ``sounds/apps.json``
ships in this package). A scheme is a name and a stored sound for each event; a scheme file is a
ZIP of ``scheme.json`` and the sound files it uses. Everything read from a file is checked here,
with the limits counted from what is actually read, so an app can hand this a file from anywhere:
anything wrong with it is a ``SchemeFileProblem``, never another exception.

No wx and no network, so all of it can be tested.
"""
from __future__ import annotations

import io
import json
import os
import re
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from .sounds import MAX_BYTES as MAX_SOUND_BYTES
from .sounds import SoundProblem, read_wav_bytes, safe_file_name

try:
    from lzma import LZMAError
except ImportError:  # pragma: no cover - a Python built without lzma
    class LZMAError(Exception):
        pass

FORMAT = 1
#: The packs every built-in sound comes in, which are also the built-in schemes.
PACKS: Dict[str, str] = {"glass": "Glass", "softbell": "Soft bell", "wood": "Wood"}
DEFAULT_PACK = "glass"

MAX_NAME = 60
MAX_AUTHOR = 60
MAX_LICENSE = 40
MAX_DESCRIPTION = 300
MAX_APP_NAME = 40
MAX_FILE_NAME = 100
MAX_FILE_BYTES = 80 * 1024 * 1024  # 83,886,080
MAX_ENTRIES = 64
MAX_JSON_BYTES = 64 * 1024  # 65,536
MAX_EVENTS = 100
#: Objects and arrays nested at most this deep, counting the outermost (SCHEMES.md).
MAX_DEPTH = 8
_MAX_INT32 = 2 ** 31 - 1

NONE = "none"
_BUILT_IN = "builtin:"
_CUSTOM = "custom:"
_KEY = re.compile(r"^[A-Za-z0-9]{1,40}$")
#: The same scheme always gives the same bytes from this code: the gallery names its files by
#: their hash. (Another implementation's DEFLATE needn't match byte for byte.)
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
IMPORTED_NAME = "Imported scheme"
#: What a damaged ZIP, a damaged compressed entry or an odd WAV can raise while being read.
_READ_ERRORS = (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, LZMAError, NotImplementedError,
                RuntimeError, EOFError, ValueError, OverflowError, KeyError, IndexError)


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


# -- text ----------------------------------------------------------------------------


def _is_control(c: str) -> bool:
    """A control character (category Cc: U+0000 to U+001F and U+007F to U+009F)."""
    return unicodedata.category(c) == "Cc"


def utf16_length(text: str) -> int:
    """Length as QuickMail counts it: UTF-16 code units, so an emoji is two."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


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
    """One line of text from a file: control characters and unpaired surrogates removed,
    trimmed, cut to ``limit``, and trimmed again."""
    if not isinstance(text, str):
        return ""
    kept = "".join(c for c in text if not _is_control(c) and not 0xD800 <= ord(c) <= 0xDFFF)
    return cut(kept.strip(), limit).strip()


def app_name(app_id: object) -> str:
    """An app's name to show: app-kit's name for it, else the id cleaned, else "another app"."""
    app = _apps().get(app_id) if isinstance(app_id, str) else None
    if app is not None:
        return app.name
    return clean_line(app_id, MAX_APP_NAME) or "another app"


def fold(text: str) -> str:
    """``text`` for comparing ignoring case, as SCHEMES.md defines it: each character's simple
    uppercase, one character for one (.NET's OrdinalIgnoreCase), so "straße" and "STRASSE"
    stay different in every app."""
    return "".join(u if len(u := c.upper()) == 1 else c for c in text)


def name_problem(name: str, taken: Iterable[str] = ()) -> Optional[str]:
    """Why ``name`` can't be a scheme's name, in words to show, or None."""
    name = name.strip()
    if not name:
        return "Type a name for the scheme."
    if any(_is_control(c) or 0xD800 <= ord(c) <= 0xDFFF for c in name):
        return "A scheme's name can't have control characters."
    if utf16_length(name) > MAX_NAME:
        return f"A scheme's name can be at most {MAX_NAME} characters."
    if fold(name) in {fold(t) for t in taken}:
        return f"There's already a scheme called {name}."
    return None


def unique_name(name: str, taken: Iterable[str]) -> str:
    """``name``, or ``name 2``, ``name 3`` and so on, whichever isn't taken, within 60 characters."""
    folded = {fold(t) for t in taken}
    if fold(name) not in folded:
        return name
    number = 2
    while True:
        suffix = f" {number}"
        candidate = cut(name, MAX_NAME - len(suffix)).rstrip() + suffix
        if fold(candidate) not in folded:
            return candidate
        number += 1


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


def custom_name_ok(name: object, allow_mp3: bool = False) -> bool:
    """A file name that's safe to keep and join to the sounds folder, and one an app plays: no
    path, device name, control or formatting character (such as a right-to-left override, which
    could make ``x.wav`` read as something else), at most 100 UTF-16 units, ending .wav."""
    if not isinstance(name, str) or not safe_file_name(name) or name != name.strip():
        return False
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in name):
        return False
    if utf16_length(name) > MAX_FILE_NAME:
        return False
    return name.lower().endswith((".wav", ".mp3") if allow_mp3 else ".wav")


def parse_ref(value: object, app: AppCatalog, allow_mp3: bool = False) -> Optional[SoundRef]:
    """The stored sound, or None for a value that isn't one this app can play. The prefix, pack
    and built-in name match whatever their case, and come back lowercase; a file name keeps its
    case."""
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


def sound_path(ref: SoundRef, scheme_pack: Optional[str], built_in_dir: Union[str, os.PathLike],
               custom_dir: Union[str, os.PathLike]) -> Optional[Path]:
    """The file a stored sound plays: ``<built_in_dir>/<pack>/<name>.wav`` (the pack as
    ``pack_for`` says), ``<custom_dir>/<file>``, or None for ``none``."""
    if ref.kind == "builtin":
        return Path(built_in_dir) / pack_for(ref, scheme_pack) / f"{ref.name}.wav"
    if ref.kind == "custom" and custom_name_ok(ref.name, allow_mp3=True):
        return Path(custom_dir) / ref.name
    return None


def portable(app: AppCatalog, stored: Mapping[str, str], built_in: bool,
             scheme_pack: Optional[str], allow_mp3: bool = False) -> Dict[str, str]:
    """Every event's sound as it should travel: a built-in sound with no pack of its own gets
    the one it played in, so it sounds the same in whatever scheme it lands in."""
    out: Dict[str, str] = {}
    for key in app.keys:
        ref = resolve(app, key, stored, built_in, allow_mp3)
        if ref.kind == "builtin" and not ref.pack:
            ref = SoundRef("builtin", ref.name, pack_for(ref, scheme_pack))
        out[key] = str(ref)
    return out


def files_used(app: AppCatalog, stored: Mapping[str, str], allow_mp3: bool = False) -> List[str]:
    """The ``custom:`` files a scheme's events play, each once, in event order."""
    out: List[str] = []
    for key in app.keys:
        ref = parse_ref(stored.get(key), app, allow_mp3)
        if ref is not None and ref.kind == "custom" and ref.name not in out:
            out.append(ref.name)
    return out


# -- sound files ---------------------------------------------------------------------


def wav_problem(data: bytes) -> Optional[str]:
    """Why these bytes aren't a sound every app can play (a 16-bit PCM WAV, mono or stereo,
    8 to 192 kHz, at most 30 seconds and 5 MB), or None. Never raises."""
    try:
        read_wav_bytes(data, strict=True)
    except SoundProblem as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - anything else about a hostile file is a problem too
        return f"It can't be read as a WAV file ({exc})."
    return None


# -- JSON ----------------------------------------------------------------------------


def _strict_constant(name: str):
    raise ValueError(f"{name} isn't JSON")


def load_json(raw: bytes, max_depth: int = MAX_DEPTH, bom: bool = True):
    """Strict JSON (RFC 8259): no NaN or Infinity, at most ``max_depth`` levels of objects and
    arrays. A repeated key: the last counts. Raises ValueError."""
    text = raw.decode("utf-8-sig" if bom else "utf-8")
    try:
        value = json.loads(text, parse_constant=_strict_constant)
    except RecursionError as exc:
        raise ValueError("too deep") from exc
    if not _within(value, max_depth):
        raise ValueError("too deep")
    return value


def _within(value, levels: int) -> bool:
    if isinstance(value, (dict, list)):
        if levels <= 0:
            return False
        children = value.values() if isinstance(value, dict) else value
        return all(_within(v, levels - 1) for v in children)
    return True


def _whole_number(value: object) -> Optional[int]:
    """A JSON whole number that fits in 32 bits, or None (a bool isn't one)."""
    return value if type(value) is int and -_MAX_INT32 - 1 <= value <= _MAX_INT32 else None


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
    #: Events whose ``custom:`` file parsed but couldn't be used (missing from the file and not
    #: already in the app's folder, over 5 MB, damaged, or not one the app plays): now ``none``.
    dropped: int = 0


def _readable(info: zipfile.ZipInfo) -> bool:
    """Stored or DEFLATE, and not encrypted: what every app can read."""
    return info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) and not info.flag_bits & 1


def _read_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int, budget: List[int]) -> Optional[bytes]:
    """An entry's bytes, counted as they're read, not taken from what the archive says. None if
    the entry alone is over ``limit``; SchemeFileProblem once the whole file is over 80 MB."""
    out = bytearray()
    with zf.open(info) as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return bytes(out)
            out += chunk
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise SchemeFileProblem("This sound scheme is too large to use.")
            if len(out) > limit:
                return None


def read_scheme_file(source: Union[str, os.PathLike, bytes], app: AppCatalog, *,
                     playable: Callable[[str, bytes], bool] = lambda name, data: wav_problem(data) is None,
                     allow_mp3: bool = False,
                     have_file: Callable[[str], bool] = lambda name: False) -> SchemeFile:
    """Read and check a scheme file for ``app``. Raises SchemeFileProblem with words to show,
    and nothing else.

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
            meta_info: Optional[zipfile.ZipInfo] = None
            entries: Dict[str, zipfile.ZipInfo] = {}
            for info in infos:  # the first of any repeated name counts
                name = info.filename.replace("\\", "/")
                if name == "scheme.json":
                    meta_info = meta_info or info
                elif name.startswith("sounds/") and custom_name_ok(name[len("sounds/"):], allow_mp3):
                    entries.setdefault(name[len("sounds/"):], info)
            if meta_info is None or not _readable(meta_info):
                raise SchemeFileProblem(unreadable)
            meta_bytes = _read_capped(zf, meta_info, MAX_JSON_BYTES, budget)
            if meta_bytes is None:
                raise SchemeFileProblem(unreadable)
            try:
                meta = load_json(meta_bytes)
            except (UnicodeDecodeError, ValueError) as exc:
                raise SchemeFileProblem(unreadable) from exc
            if not isinstance(meta, dict):
                raise SchemeFileProblem(unreadable)
            scheme = _scheme_from_json(meta, app, unreadable, allow_mp3)
            dropped_files: Set[str] = set()
            folded: Set[str] = set()
            for name in files_used(app, scheme.events, allow_mp3):
                # Names that differ only in case are one file on Windows: the first counts.
                if fold(name) in folded:
                    dropped_files.add(name)
                    continue
                folded.add(fold(name))
                info = entries.get(name)
                if info is None:
                    if not have_file(name):
                        dropped_files.add(name)
                    continue
                sound = None
                if _readable(info):
                    try:
                        sound = _read_capped(zf, info, MAX_SOUND_BYTES, budget)
                        if sound is not None and not playable(name, sound):
                            sound = None
                    except SchemeFileProblem:
                        raise
                    except _READ_ERRORS + (OSError,):
                        sound = None
                if sound is None:
                    dropped_files.add(name)
                else:
                    scheme.sounds[name] = sound
    except SchemeFileProblem:
        raise
    except OSError as exc:
        raise SchemeFileProblem(f"The file could not be read ({exc.strerror or exc}).") from exc
    except _READ_ERRORS as exc:
        raise SchemeFileProblem(unreadable) from exc
    for key, value in list(scheme.events.items()):
        ref = parse_ref(value, app, allow_mp3)
        if ref is not None and ref.kind == "custom" and ref.name in dropped_files:
            scheme.events[key] = NONE
            scheme.dropped += 1
    return scheme


def _scheme_from_json(meta: dict, app: AppCatalog, unreadable: str, allow_mp3: bool) -> SchemeFile:
    fmt = _whole_number(meta.get("format"))
    if fmt is None or fmt < 1:
        raise SchemeFileProblem(unreadable)
    if fmt > FORMAT:
        raise SchemeFileProblem(f"This sound scheme was made by a newer version of {app.name}. "
                                f"Update {app.name} and try again.")
    owner = meta.get("app")
    owner = "quickmail" if owner is None else owner  # QuickMail 0.8.69 wrote no app
    if owner != app.id:
        raise SchemeFileProblem(f"This is a sound scheme for {app_name(owner)}, so {app.name} can't use it.")
    events = meta.get("events")
    if not isinstance(events, dict):
        raise SchemeFileProblem(unreadable)
    kept: Dict[str, str] = {}
    for count, (key, value) in enumerate(events.items()):
        if count >= MAX_EVENTS:
            break
        if not _KEY.match(key) or key not in app.keys:
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
                      read_sound: Callable[[str], Optional[bytes]] = lambda name: None, *,
                      built_in: bool, scheme_pack: Optional[str],
                      allow_mp3: bool = False) -> bytes:
    """A scheme file's bytes, the same for the same scheme every time.

    ``scheme.events`` are written ``portable``: every event, a built-in sound with no pack pinned
    to the one it plays in (``built_in`` and ``scheme_pack`` say how the scheme plays). A
    ``custom:`` file's bytes come from ``scheme.sounds`` or ``read_sound(name)``; one that's
    missing, over 5 MB, or would take the file past 80 MB is left out, and its events are
    written as ``none``."""
    events: Dict[str, str] = {}
    files: Dict[str, bytes] = {}
    total = 0
    for key, value in portable(app, scheme.events, built_in, scheme_pack, allow_mp3).items():
        ref = parse_ref(value, app, allow_mp3) or SILENT
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
    body = {"format": FORMAT, "app": app.id, "name": clean_line(scheme.name, MAX_NAME) or IMPORTED_NAME,
            "author": clean_line(scheme.author, MAX_AUTHOR) or None,
            "license": clean_line(scheme.license, MAX_LICENSE) or None}
    description = clean_line(scheme.description, MAX_DESCRIPTION)
    if description:
        body["description"] = description
    body["events"] = events
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(name: str, data: bytes) -> None:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0  # otherwise Windows and other systems write different bytes
            info.external_attr = 0
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


def _same_file(path: Path, data: bytes) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size == len(data) \
            and path.read_bytes() == data
    except OSError:
        return False


def install_sounds(scheme: SchemeFile, folder: Union[str, os.PathLike]) -> Installed:
    """Write a scheme file's sounds into ``folder``. A file whose name is taken by different
    bytes (or by anything that isn't a plain file) is written as ``<name> 2.wav`` and so on, and
    the events that use it follow; the same bytes under the same name are used as they are.
    A file is never overwritten, and one that fails partway is removed. Raises OSError if
    ``folder`` can't be written."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    renamed: Dict[str, str] = {}
    added: List[str] = []
    for name in sorted(scheme.sounds):
        if not custom_name_ok(name, allow_mp3=True):
            continue
        data = scheme.sounds[name]
        stem, ext = os.path.splitext(name)
        for number in range(1, 1000):
            candidate = name if number == 1 else cut(stem, MAX_FILE_NAME - len(ext) - len(f" {number}")) + f" {number}{ext}"
            path = folder / candidate
            if _same_file(path, data):
                renamed[name] = candidate
                break
            try:
                # Unbuffered, so a failed write fails here and the file is removed.
                with open(path, "xb", buffering=0) as handle:
                    try:
                        handle.write(data)
                    except BaseException:
                        handle.close()
                        path.unlink()
                        raise
            except FileExistsError:
                continue
            except IsADirectoryError:
                continue
            except PermissionError:
                if path.exists():
                    continue  # taken by something we can't read
                raise
            renamed[name] = candidate
            added.append(candidate)
            break
    events = {}
    for key, value in scheme.events.items():
        if value.lower().startswith(_CUSTOM):
            name = value[len(_CUSTOM):]
            value = _CUSTOM + renamed[name] if name in renamed else (NONE if name in scheme.sounds else value)
        events[key] = value
    return Installed(events, added)
