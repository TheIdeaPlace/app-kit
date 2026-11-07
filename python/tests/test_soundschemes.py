"""Sound schemes and scheme files (sounds/SCHEMES.md), checked against the shared test files."""
import io
import json
import zipfile
from pathlib import Path

import pytest

from theideaplace_appkit import soundschemes as ss

TESTDATA = Path(__file__).resolve().parents[2] / "sounds" / "testdata"
EXPECTED = json.loads((TESTDATA / "expected.json").read_text(encoding="utf-8"))
TCP = ss.catalog("thechatplace")
QM = ss.catalog("quickmail")


def test_the_package_ships_the_same_apps_json_as_the_sounds_folder():
    shipped = (Path(ss.__file__).with_name("apps.json")).read_text(encoding="utf-8")
    assert shipped == (TESTDATA.parent / "apps.json").read_text(encoding="utf-8")


def test_each_apps_events_keys_and_sounds():
    assert TCP.extension == ".tcpsounds" and QM.extension == ".qmsounds"
    assert len(TCP.events) == 15 and len(QM.events) == 14
    assert TCP.built_in_sounds[:3] == ("tap", "changed", "pulse")
    assert "removed" in QM.built_in_sounds and "removed" not in TCP.built_in_sounds
    for app in (TCP, QM):
        assert all(ss._KEY.match(k) for k in app.keys)
        assert len(set(app.keys)) == len(app.keys)


CASES = [(name, app, want) for name, apps in EXPECTED["schemes"].items() for app, want in apps.items()]


@pytest.mark.parametrize("name,app,want", CASES, ids=[f"{n}-{a}" for n, a, _ in CASES])
def test_each_shared_scheme_file_gives_what_expected_json_says(name, app, want):
    catalog = ss.catalog(app)
    path = TESTDATA / name
    if "refused" in want:
        with pytest.raises(ss.SchemeFileProblem) as caught:
            ss.read_scheme_file(path, catalog)
        assert str(caught.value) == want["refused"]
        return
    scheme = ss.read_scheme_file(path, catalog)
    assert scheme.name == want["name"]
    assert (scheme.author, scheme.license, scheme.description) == (
        want["author"], want["license"], want["description"])
    assert scheme.events == want["events"]
    assert list(scheme.events) == list(want["events"])  # the file's order
    assert sorted(scheme.sounds) == want["sounds"]
    assert scheme.dropped == want["dropped"]


def test_the_same_file_reads_the_same_from_bytes():
    data = (TESTDATA / "tcp-good.tcpsounds").read_bytes()
    assert ss.read_scheme_file(data, TCP).events == ss.read_scheme_file(TESTDATA / "tcp-good.tcpsounds", TCP).events


def test_a_file_already_in_the_sounds_folder_is_kept():
    data = _zip({"format": 1, "app": "thechatplace", "name": "X",
                 "events": {"ToolRan": "custom:mine.wav"}})
    assert ss.read_scheme_file(data, TCP).events["ToolRan"] == "none"
    kept = ss.read_scheme_file(data, TCP, have_file=lambda n: n == "mine.wav")
    assert kept.events["ToolRan"] == "custom:mine.wav" and kept.dropped == 0


def test_mp3_is_for_apps_that_play_it():
    data = _zip({"format": 1, "name": "X", "events": {"NewMail": "custom:a.mp3"}}, {"a.mp3": b"ID3"})
    assert ss.read_scheme_file(data, QM).events["NewMail"] == "none"
    taken = ss.read_scheme_file(data, QM, allow_mp3=True, playable=lambda n, d: True)
    assert taken.events["NewMail"] == "custom:a.mp3" and taken.sounds == {"a.mp3": b"ID3"}


def test_a_sound_over_5_mb_is_dropped_but_a_file_over_80_mb_is_refused(monkeypatch):
    monkeypatch.setattr(ss, "MAX_SOUND_BYTES", 10)
    data = _zip({"format": 1, "app": "thechatplace", "name": "X",
                 "events": {"ToolRan": "custom:big.wav"}}, {"big.wav": b"x" * 11})
    scheme = ss.read_scheme_file(data, TCP, playable=lambda n, d: True)
    assert scheme.events["ToolRan"] == "none" and scheme.dropped == 1
    monkeypatch.setattr(ss, "MAX_FILE_BYTES", 50)
    with pytest.raises(ss.SchemeFileProblem, match="too large"):
        ss.read_scheme_file(data, TCP)


def test_only_the_first_100_events_are_read():
    events = {f"Unknown{i}": "none" for i in range(100)}
    events["ToolRan"] = "builtin:tap"
    data = _zip({"format": 1, "app": "thechatplace", "name": "X", "events": events})
    assert ss.read_scheme_file(data, TCP).events == {}


@pytest.mark.parametrize("value,ref", [
    ("none", "none"), ("NONE", "none"), ("builtin:tap", "builtin:tap"),
    ("Builtin:SoftBell/Tap", "builtin:softbell/tap"), ("custom:a.wav", "custom:a.wav"),
    ("builtin:removed", None), ("builtin:chrome/tap", None), ("custom:a.mp3", None),
    ("custom:a/b.wav", None), ("custom:COM1.wav", None), ("custom:LPT¹.wav", None),
    ("custom: a.wav", None), ("custom:a.wav.", None), ("custom:" + "a" * 97 + ".wav", None),
    ("tap", None), (3, None), (None, None),
])
def test_stored_sounds(value, ref):
    parsed = ss.parse_ref(value, TCP)
    assert (str(parsed) if parsed else None) == ref


def test_resolve_and_portable_pin_the_pack_a_sound_played_in():
    stored = {"ReplyFinished": "builtin:wood/arrived", "ToolRan": "none", "NeedsYou": "builtin:sent"}
    assert str(ss.resolve(TCP, "FileChanged", stored, built_in=True)) == "builtin:changed"
    assert str(ss.resolve(TCP, "FileChanged", stored, built_in=False)) == "none"
    out = ss.portable(TCP, stored, built_in=True, scheme_pack="softbell")
    assert out["FileChanged"] == "builtin:softbell/changed"
    assert out["ReplyFinished"] == "builtin:wood/arrived"
    assert out["NeedsYou"] == "builtin:softbell/sent"
    assert out["ToolRan"] == "none"
    assert list(out) == list(TCP.keys)
    own = ss.portable(TCP, stored, built_in=False, scheme_pack=None)
    assert own["NeedsYou"] == "builtin:glass/sent" and own["FileChanged"] == "none"


def test_writing_gives_the_same_bytes_and_reads_back(tmp_path):
    wav = (TESTDATA / "tcp-good.tcpsounds")
    knock = zipfile.ZipFile(wav).read("sounds/knock.wav")
    scheme = ss.SchemeFile("thechatplace", "Mine", {"ToolRan": "custom:knock.wav",
                                                    "NeedsYou": "custom:gone.wav",
                                                    "ReplyFinished": "builtin:wood/arrived"},
                           author="Me", description="Quiet.")
    first = ss.write_scheme_file(scheme, TCP, read_sound=lambda n: knock if n == "knock.wav" else None)
    again = ss.write_scheme_file(scheme, TCP, read_sound=lambda n: knock if n == "knock.wav" else None)
    assert first == again
    with zipfile.ZipFile(io.BytesIO(first)) as zf:
        assert zf.namelist() == ["scheme.json", "sounds/knock.wav"]
        meta = json.loads(zf.read("scheme.json"))
    assert meta["app"] == "thechatplace" and meta["description"] == "Quiet." and meta["license"] is None
    assert list(meta["events"]) == list(TCP.keys)
    assert meta["events"]["NeedsYou"] == "none"  # missing file left out
    back = ss.read_scheme_file(first, TCP)
    assert back.events["ToolRan"] == "custom:knock.wav" and back.sounds["knock.wav"] == knock


def test_installing_numbers_a_clashing_name_and_reuses_the_same_bytes(tmp_path):
    scheme = ss.read_scheme_file(TESTDATA / "tcp-good.tcpsounds", TCP)
    (tmp_path / "knock.wav").write_bytes(b"someone else's")
    first = ss.install_sounds(scheme, tmp_path)
    assert first.added == ["knock 2.wav"] and first.events["NeedsYou"] == "custom:knock 2.wav"
    second = ss.install_sounds(scheme, tmp_path)
    assert second.added == [] and second.events["NeedsYou"] == "custom:knock 2.wav"
    assert second.events["ToolRan"] == "builtin:softbell/tap"


def test_names():
    assert ss.unique_name("Rain", ["rain", "Rain 2"]) == "Rain 3"
    assert ss.unique_name("x" * 60, ["x" * 60]) == "x" * 58 + " 2"
    assert ss.cut("ab\U0001F600c", 3) == "ab"  # an emoji is two units and isn't split
    assert ss.utf16_length("\U0001F600") == 2
    assert ss.name_problem("  ") and ss.name_problem("a\tb") and ss.name_problem("x" * 61)
    assert ss.name_problem("rain", ["Rain"]) == "There's already a scheme called rain."
    assert ss.name_problem("Rain") is None
    assert ss.clean_line(" a\u0007b ", 60) == "ab" and ss.clean_line(5, 60) == ""


def test_wav_problem():
    good = zipfile.ZipFile(TESTDATA / "tcp-good.tcpsounds").read("sounds/knock.wav")
    eight = zipfile.ZipFile(TESTDATA / "tcp-hostile.tcpsounds").read("sounds/eight-bit.wav")
    assert ss.wav_problem(good) is None
    assert "16-bit" in ss.wav_problem(eight)
    assert ss.wav_problem(b"RIFF") is not None


def _zip(meta, sounds=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("scheme.json", json.dumps(meta))
        for name, data in (sounds or {}).items():
            zf.writestr("sounds/" + name, data)
    return buffer.getvalue()
