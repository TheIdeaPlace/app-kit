"""The sound gallery (``sounds/SCHEMES.md``): its signed list of schemes, and checked downloads.

Nothing in the list is read until its signature verifies, and the list has to be the asking app's.
A download has to be exactly the size the list says, with the SHA-256 it says, before it's handed
on; it's then read with ``soundschemes.read_scheme_file``, which checks everything again.

Verifying needs ``pycryptodomex`` (``pip install theideaplace-appkit[gallery]``), or failing that
``cryptography``. The network is reached only through ``fetch``, which tests replace.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .soundschemes import (MAX_AUTHOR, MAX_DESCRIPTION, MAX_FILE_BYTES, MAX_LICENSE, MAX_NAME,
                           AppCatalog, clean_line)

#: The gallery's key. Its private half signs from TheIdeaPlace/soundgallery's protected workflow.
PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEQwiXJpzslRlBsXFp1i5SA+mq+RjX
omSYj0/L6q6wMmhKLQjZvF5NUTIHNZVBJG4SkaZPgEH0Dfl/BQpvo8otKQ==
-----END PUBLIC KEY-----
"""
BASE = "https://theideaplace.github.io/soundgallery/{app}/"
INDEX = "index.signed"
MAX_INDEX_BYTES = 256 * 1024
MAX_SIGNATURE_LINE = 512
MAX_SCHEMES = 200
MAX_DEPTH = 8
TIMEOUT = 20.0
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")

UNREACHABLE = "The gallery could not be reached. Check your connection, then choose Reload."
UNTRUSTED = "The gallery's list could not be checked, so it was not used. Try again later."


class GalleryProblem(Exception):
    """Something went wrong with the gallery. Its text can be shown as it is."""


class Unreachable(GalleryProblem):
    def __init__(self, detail: str = ""):
        super().__init__(UNREACHABLE)
        self.detail = detail


class Untrusted(GalleryProblem):
    def __init__(self, detail: str = ""):
        super().__init__(UNTRUSTED)
        self.detail = detail


class NeedsUpdate(GalleryProblem):
    def __init__(self, app: AppCatalog):
        super().__init__(f"The gallery needs a newer version of {app.name}. Update {app.name}, "
                         "then choose Reload.")


class NotTheListedFile(GalleryProblem):
    def __init__(self, name: str):
        super().__init__(f"The download of {name} was not the file the gallery lists, so it was "
                         "not used. Try again later.")


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    author: str
    license: str
    description: str
    file: str
    size: int
    sha256: str
    #: How many of the app's events have a sound, or None when the list doesn't say.
    events_with_sound: Optional[int] = None


# -- the signature -------------------------------------------------------------------


def verify(signature_der: bytes, data: bytes, public_key_pem: bytes = PUBLIC_KEY_PEM) -> bool:
    """Whether ``signature_der`` is the key's ECDSA P-256 SHA-256 signature of ``data``."""
    try:
        from Cryptodome.Hash import SHA256
        from Cryptodome.PublicKey import ECC
        from Cryptodome.Signature import DSS
    except ImportError:
        return _verify_cryptography(signature_der, data, public_key_pem)
    try:
        key = ECC.import_key(public_key_pem)
        if key.curve not in ("NIST P-256", "p256", "P-256", "secp256r1", "prime256v1"):
            return False
        DSS.new(key, "fips-186-3", encoding="der").verify(SHA256.new(data), signature_der)
        return True
    except (ValueError, TypeError, IndexError):
        return False


def _verify_cryptography(signature_der: bytes, data: bytes, public_key_pem: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
    except ImportError as exc:  # pragma: no cover - an app ships one or the other
        raise RuntimeError("Checking the gallery needs pycryptodomex or cryptography.") from exc
    try:
        key = load_pem_public_key(public_key_pem)
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            return False
        key.verify(signature_der, data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# -- the list ------------------------------------------------------------------------


def _depth(value, limit: int) -> bool:
    """Whether ``value`` nests at most ``limit`` levels."""
    if limit < 0:
        return False
    if isinstance(value, dict):
        return all(_depth(v, limit - 1) for v in value.values())
    if isinstance(value, list):
        return all(_depth(v, limit - 1) for v in value)
    return True


def read_index(signed: bytes, app: AppCatalog, public_key_pem: bytes = PUBLIC_KEY_PEM) -> List[Entry]:
    """The schemes ``index.signed`` lists for ``app``. Raises Untrusted or NeedsUpdate."""
    newline = signed.find(b"\n", 0, MAX_SIGNATURE_LINE + 1)
    if newline < 0:
        raise Untrusted("no signature line")
    index = signed[newline + 1:]
    if not 1 <= len(index) <= MAX_INDEX_BYTES:
        raise Untrusted("index size")
    try:
        der = base64.b64decode(signed[:newline].strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise Untrusted("signature isn't base64") from exc
    if not der or not verify(der, index, public_key_pem):
        raise Untrusted("signature")
    try:
        value = json.loads(index.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise Untrusted("not JSON") from exc
    if not isinstance(value, dict) or not _depth(value, MAX_DEPTH):
        raise Untrusted("shape")
    fmt = value.get("format")
    if type(fmt) is not int or fmt < 1:
        raise Untrusted("format")
    if fmt > 1:
        raise NeedsUpdate(app)
    if value.get("app") != app.id:
        raise Untrusted("another app's list")
    schemes = value.get("schemes")
    if not isinstance(schemes, list):
        raise Untrusted("schemes")
    entries: List[Entry] = []
    seen = set()
    for raw in schemes[:MAX_SCHEMES]:
        entry = _entry(raw, app)
        if entry is not None and entry.id not in seen:
            seen.add(entry.id)
            entries.append(entry)
    return entries


def _entry(raw: object, app: AppCatalog) -> Optional[Entry]:
    """One listed scheme, or None if anything about it is wrong: it's never used in part."""
    if not isinstance(raw, dict):
        return None
    sid, sha, file, size = raw.get("id"), raw.get("sha256"), raw.get("file"), raw.get("size")
    if not isinstance(sid, str) or not _ID.match(sid):
        return None
    if not isinstance(sha, str) or not _SHA.match(sha):
        return None
    if file != f"{sid}-{sha[:16]}{app.extension}":
        return None
    if type(size) is not int or not 1 <= size <= MAX_FILE_BYTES:
        return None
    name = clean_line(raw.get("name"), MAX_NAME)
    author = clean_line(raw.get("author"), MAX_AUTHOR)
    licence = clean_line(raw.get("license"), MAX_LICENSE)
    if not (name and author and licence):
        return None
    count = raw.get("eventsWithSound")
    events = max(0, min(len(app.events), count)) if type(count) is int else None
    return Entry(sid, name, author, licence, clean_line(raw.get("description"), MAX_DESCRIPTION),
                 file, size, sha, events)


def check_download(entry: Entry, data: bytes) -> bytes:
    """``data`` if it's exactly the file the list names, else NotTheListedFile."""
    if len(data) != entry.size:
        raise NotTheListedFile(entry.name)
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), entry.sha256):
        raise NotTheListedFile(entry.name)
    return data


# -- fetching ------------------------------------------------------------------------


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is an answer other than 200: unreachable


def base_ok(base: str) -> bool:
    """A gallery address an app may use: https, a folder, or http on this computer only."""
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    if parsed.scheme == "http":
        host = parsed.hostname or ""
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False
    return parsed.scheme in ("", "file") or len(parsed.scheme) == 1  # a folder (C:\ has scheme "c")


def fetch(url: str, limit: int, timeout: float = TIMEOUT,
          context: Optional[ssl.SSLContext] = None, user_agent: str = "SoundGallery/1") -> bytes:
    """At most ``limit`` bytes from ``url``. Raises Unreachable for anything but a 200 answer,
    and Untrusted for a body over ``limit``."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        path = Path(urllib.request.url2pathname(parsed.path) if parsed.scheme == "file" else url)
        try:
            with open(path, "rb") as handle:
                data = handle.read(limit + 1)
        except OSError as exc:
            raise Unreachable(str(exc)) from exc
        if len(data) > limit:
            raise Untrusted("too large")
        return data
    opener = urllib.request.build_opener(_NoRedirects, urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    deadline = time.monotonic() + timeout
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise Unreachable(f"HTTP {response.status}")
            length = response.headers.get("Content-Length")
            if length is not None and length.isdigit() and int(length) > limit:
                raise Untrusted("too large")
            out = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise Unreachable("timed out")
                chunk = response.read(64 * 1024)
                if not chunk:
                    return bytes(out)
                out += chunk
                if len(out) > limit:
                    raise Untrusted("too large")
    except GalleryProblem:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Unreachable(str(exc)) from exc


class Gallery:
    """One app's view of the gallery."""

    def __init__(self, app: AppCatalog, base: Optional[str] = None, *,
                 context: Optional[ssl.SSLContext] = None, public_key_pem: bytes = PUBLIC_KEY_PEM,
                 user_agent: Optional[str] = None,
                 fetcher: Callable[..., bytes] = fetch):
        self.app = app
        base = base or BASE.format(app=app.id)
        if not base_ok(base):
            raise ValueError("The gallery's address has to be https, a folder, or http on this computer.")
        self.base = base if base.endswith(("/", "\\")) else base + "/"
        self._context = context
        self._key = public_key_pem
        self._agent = user_agent or f"{app.name.replace(' ', '')}-SoundGallery/1"
        self._fetch = fetcher

    def _url(self, name: str) -> str:
        if urllib.parse.urlparse(self.base).scheme in ("http", "https"):
            return urllib.parse.urljoin(self.base, name)
        return str(Path(self.base) / name)

    def schemes(self) -> List[Entry]:
        """What the gallery lists, checked. Raises a GalleryProblem."""
        signed = self._fetch(self._url(INDEX), MAX_INDEX_BYTES + MAX_SIGNATURE_LINE,
                             context=self._context, user_agent=self._agent)
        return read_index(signed, self.app, self._key)

    def download(self, entry: Entry) -> bytes:
        """The listed scheme file's bytes, checked against the list. Raises a GalleryProblem."""
        try:
            data = self._fetch(self._url(entry.file), entry.size, context=self._context,
                               user_agent=self._agent)
        except Untrusted as exc:  # larger than listed
            raise NotTheListedFile(entry.name) from exc
        return check_download(entry, data)
