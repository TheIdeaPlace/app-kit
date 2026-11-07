"""The sound gallery's signed list and downloads (sounds/SCHEMES.md), against the shared test files."""
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from theideaplace_appkit import soundgallery as sg
from theideaplace_appkit import soundschemes as ss

TESTDATA = Path(__file__).resolve().parents[2] / "sounds" / "testdata"
EXPECTED = json.loads((TESTDATA / "expected.json").read_text(encoding="utf-8"))
KEY = (TESTDATA / "test-key.pub.pem").read_bytes()
TCP = ss.catalog("thechatplace")

CASES = [(name, app, want) for name, apps in EXPECTED["indexes"].items() for app, want in apps.items()]


@pytest.mark.parametrize("name,app,want", CASES, ids=[f"{n}-{a}" for n, a, _ in CASES])
def test_each_shared_index_gives_what_expected_json_says(name, app, want):
    signed = (TESTDATA / name).read_bytes()
    catalog = ss.catalog(app)
    if want == "untrusted":
        with pytest.raises(sg.Untrusted) as caught:
            sg.read_index(signed, catalog, KEY)
        assert str(caught.value) == sg.UNTRUSTED
        return
    if want == "needsUpdate":
        with pytest.raises(sg.NeedsUpdate, match="newer version of The Chat Place"):
            sg.read_index(signed, catalog, KEY)
        return
    entries = sg.read_index(signed, catalog, KEY)
    assert [e.id for e in entries] == want["ids"]
    if "eventsWithSound" in want:
        assert [e.events_with_sound for e in entries] == want["eventsWithSound"]


def test_the_real_key_refuses_a_list_signed_with_the_test_key():
    with pytest.raises(sg.Untrusted):
        sg.read_index((TESTDATA / "index-good.signed").read_bytes(), TCP)


def test_an_entry_is_cleaned():
    entry = sg.read_index((TESTDATA / "index-good.signed").read_bytes(), TCP, KEY)[0]
    assert (entry.name, entry.author, entry.license, entry.description) == (
        "Rain at Night", "Test Author", "CC0-1.0", "Soft sounds.")
    assert entry.file == "rain-at-night-" + "a" * 16 + ".tcpsounds" and entry.size == 1234


def test_both_verifiers_agree():
    signed = (TESTDATA / "index-good.signed").read_bytes()
    line, _, index = signed.partition(b"\n")
    import base64
    der = base64.b64decode(line)
    assert sg.verify(der, index, KEY)
    assert sg._verify_cryptography(der, index, KEY)
    assert not sg._verify_cryptography(der, index + b" ", KEY)
    assert not sg.verify(der, index + b" ", KEY)
    assert not sg.verify(b"\x30\x00", index, KEY)


def _entry(data: bytes, **changes) -> sg.Entry:
    sha = hashlib.sha256(data).hexdigest()
    values = dict(id="x", name="X", author="A", license="CC0-1.0", description="", file=f"x-{sha[:16]}.tcpsounds",
                  size=len(data), sha256=sha)
    values.update(changes)
    return sg.Entry(**values)


def test_a_download_has_to_be_the_listed_file():
    data = (TESTDATA / "tcp-good.tcpsounds").read_bytes()
    assert sg.check_download(_entry(data), data) == data
    with pytest.raises(sg.NotTheListedFile, match="The download of X was not the file"):
        sg.check_download(_entry(data), data[:-1] + b"!")
    with pytest.raises(sg.NotTheListedFile):
        sg.check_download(_entry(data, size=len(data) + 1), data)


def test_a_gallery_in_a_folder(tmp_path):
    data = (TESTDATA / "tcp-good.tcpsounds").read_bytes()
    (tmp_path / "index.signed").write_bytes((TESTDATA / "index-good.signed").read_bytes())
    gallery = sg.Gallery(TCP, str(tmp_path), public_key_pem=KEY)
    assert [e.id for e in gallery.schemes()] == ["rain-at-night", "bells"]
    entry = _entry(data)
    (tmp_path / entry.file).write_bytes(data)
    assert gallery.download(entry) == data
    (tmp_path / entry.file).write_bytes(data + b"more")
    with pytest.raises(sg.NotTheListedFile):
        gallery.download(entry)
    with pytest.raises(sg.Unreachable, match="could not be reached"):
        sg.Gallery(TCP, str(tmp_path / "nowhere"), public_key_pem=KEY).schemes()


@pytest.mark.parametrize("base,ok", [
    ("https://theideaplace.github.io/soundgallery/thechatplace/", True),
    ("http://127.0.0.1:8000/", True), ("http://localhost/x/", True), ("http://[::1]/", True),
    ("http://example.com/", False), ("http://10.0.0.5/", False), ("ftp://x/", False),
    ("C:\\gallery", True), ("/tmp/gallery", True), ("file:///tmp/gallery", True),
])
def test_addresses(base, ok):
    assert sg.base_ok(base) is ok


def test_the_default_address_is_the_apps_folder():
    assert sg.Gallery(TCP).base == "https://theideaplace.github.io/soundgallery/thechatplace/"
    with pytest.raises(ValueError):
        sg.Gallery(TCP, "http://example.com/")


class _Server:
    """A gallery on this computer, answering each path as ``routes`` says."""

    def __init__(self, routes):
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.agents.append(self.headers.get("User-Agent"))
                status, headers, body = routes.get(self.path, (404, {}, b""))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.agents = []
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/g/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def server():
    made = []

    def make(routes):
        made.append(_Server(routes))
        return made[-1]
    yield make
    for s in made:
        s.close()


def test_over_http_on_this_computer(server):
    signed = (TESTDATA / "index-good.signed").read_bytes()
    s = server({"/g/index.signed": (200, {}, signed)})
    gallery = sg.Gallery(TCP, s.base, public_key_pem=KEY)
    assert [e.id for e in gallery.schemes()] == ["rain-at-night", "bells"]
    assert s.agents == ["TheChatPlace-SoundGallery/1"]


@pytest.mark.parametrize("status", [301, 302, 404, 500])
def test_anything_but_200_is_unreachable(server, status):
    s = server({"/g/index.signed": (status, {"Location": "https://example.com/"}, b"")})
    with pytest.raises(sg.Unreachable):
        sg.Gallery(TCP, s.base, public_key_pem=KEY).schemes()


def test_an_index_too_large_is_untrusted(server):
    s = server({"/g/index.signed": (200, {}, b"A" * (sg.MAX_INDEX_BYTES + sg.MAX_SIGNATURE_LINE + 1))})
    with pytest.raises(sg.Untrusted):
        sg.Gallery(TCP, s.base, public_key_pem=KEY).schemes()


def test_a_download_larger_than_listed_is_not_the_listed_file(server):
    data = (TESTDATA / "tcp-good.tcpsounds").read_bytes()
    entry = _entry(data)
    s = server({"/g/" + entry.file: (200, {}, data + b"extra")})
    with pytest.raises(sg.NotTheListedFile):
        sg.Gallery(TCP, s.base, public_key_pem=KEY).download(entry)


def test_nothing_reachable_is_unreachable():
    with pytest.raises(sg.Unreachable):
        sg.fetch("http://127.0.0.1:9/index.signed", 100, timeout=2)
