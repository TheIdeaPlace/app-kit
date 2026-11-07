"""Make the shared test files for sound schemes and the gallery (SCHEMES.md).

    python sounds/testdata/make.py

Needs the ``cryptography`` package. The files are committed, so tests don't run this; run it again
only to change them, and update expected.json to match. ECDSA signatures differ each time, so the
.signed files change on every run even when nothing else does.

The key in test-key.pem is for these tests only and signs nothing real. Every implementation
should give exactly what expected.json says for each file.
"""
from __future__ import annotations

import base64
import io
import json
import math
import struct
import wave
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

HERE = Path(__file__).resolve().parent
ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def key(name: str) -> ec.EllipticCurvePrivateKey:
    path = HERE / name
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    private = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return private


def public_pem(private) -> bytes:
    return private.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)


def signed(private, index: dict | bytes) -> bytes:
    data = index if isinstance(index, bytes) else json.dumps(index, indent=2, ensure_ascii=False).encode()
    return base64.b64encode(private.sign(data, ec.ECDSA(hashes.SHA256()))) + b"\n" + data


def tone(width: int = 2, seconds: float = 0.05, rate: int = 22050) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        frames = int(seconds * rate)
        if width == 2:
            data = b"".join(struct.pack("<h", int(8000 * math.sin(i / 8))) for i in range(frames))
        else:
            data = bytes(128 + int(60 * math.sin(i / 8)) for i in range(frames))
        w.writeframes(data)
    return buffer.getvalue()


def scheme_zip(meta: dict | bytes | None, sounds: dict[str, bytes], extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(name, data):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
        if meta is not None:
            add("scheme.json", meta if isinstance(meta, bytes) else json.dumps(meta, indent=2).encode())
        for name, data in sounds.items():
            add("sounds/" + name, data)
        for name, data in (extra or {}).items():
            add(name, data)
    return buffer.getvalue()


def main() -> None:
    test_key = key("test-key.pem")
    other_key = key("other-key.pem")
    (HERE / "test-key.pub.pem").write_bytes(public_pem(test_key))

    good = {"id": "rain-at-night", "name": "Rain at Night", "author": "Test Author",
            "description": "Soft sounds.", "license": "CC0-1.0",
            "file": "rain-at-night-" + "a" * 16 + ".tcpsounds", "size": 1234,
            "sha256": "a" * 64, "eventsWithSound": 99}
    second = dict(good, id="bells", name="Bells", file="bells-" + "b" * 16 + ".tcpsounds",
                  sha256="b" * 64, eventsWithSound=3, unknownField=True)
    bad = [
        dict(good, id="Bad_Id", file="Bad_Id-" + "a" * 16 + ".tcpsounds"),
        dict(good, id="bad-sha", sha256="A" * 64, file="bad-sha-" + "A" * 16 + ".tcpsounds"),
        dict(good, id="wrong-file", file="something-else.tcpsounds"),
        dict(good, id="wrong-ext", file="wrong-ext-" + "a" * 16 + ".qmsounds"),
        dict(good, id="zero-size", file="zero-size-" + "a" * 16 + ".tcpsounds", size=0),
        dict(good, id="no-author", file="no-author-" + "a" * 16 + ".tcpsounds", author="  "),
        dict(good, id="float-size", file="float-size-" + "a" * 16 + ".tcpsounds", size=12.5),
        dict(good, name="Duplicate of rain"),
        "not an object",
    ]
    tcp = {"format": 1, "app": "thechatplace", "schemes": [good, second] + bad}
    files = {
        "index-good.signed": signed(test_key, tcp),
        "index-other-app.signed": signed(test_key, dict(tcp, app="quickmail")),
        "index-no-app.signed": signed(test_key, {k: v for k, v in tcp.items() if k != "app"}),
        "index-format2.signed": signed(test_key, dict(tcp, format=2)),
        "index-wrong-key.signed": signed(other_key, tcp),
        "index-deep.signed": signed(test_key, dict(tcp, deep=[[[[[[[[["x"]]]]]]]]])),
        "index-junk-signature.signed": b"not base64!!\n" + json.dumps(tcp).encode(),
        "index-no-newline.signed": b"A" * 600,
    }
    tampered = bytearray(files["index-good.signed"])
    tampered[-5] ^= 1
    files["index-tampered.signed"] = bytes(tampered)

    wav = tone()
    files["tcp-good.tcpsounds"] = scheme_zip({
        "format": 1, "app": "thechatplace", "name": "Rain at Night", "author": "Test Author",
        "license": "CC0-1.0", "description": "Soft sounds.",
        "events": {"ToolRan": "builtin:softbell/tap", "ReplyFinished": "BUILTIN:arrived",
                   "NeedsYou": "custom:knock.wav", "TurnFailed": "none"},
        "newerField": {"ignored": True}}, {"knock.wav": wav})
    files["qm-0869.qmsounds"] = scheme_zip({
        "format": 1, "name": "Old QuickMail", "author": None, "license": None,
        "events": {"MessageSent": "builtin:glass/sent", "NewMail": "custom:ding.wav"}},
        {"ding.wav": wav})
    files["tcp-hostile.tcpsounds"] = scheme_zip({
        "format": 1, "app": "thechatplace", "name": "\u0007  Ho\u0001stile " + "x" * 80,
        "author": "A\nB", "license": 5,
        "events": {"ToolRan": "custom:eight-bit.wav", "FileChanged": "custom:song.mp3",
                   "StillWorking": "custom:missing.wav", "MessageSent": "builtin:chrome/sent",
                   "MessageQueued": "builtin:arrived/extra/x", "MessageNotSent": "custom:../evil.wav",
                   "ReplyFinished": "custom:CON.wav", "NeedsYou": "custom:good.wav",
                   "Not_A_Key": "none", "NewMail": "builtin:sent", "TurnFailed": "builtin:removed",
                   "TurnStopped": 7}},
        {"eight-bit.wav": tone(width=1), "song.mp3": b"ID3\x03\x00", "good.wav": wav,
         "unused.wav": wav, "CON.wav": wav},
        extra={"../evil.wav": wav, "sounds/../escape.wav": wav, "other/readme.txt": b"hi"})
    files["tcp-format2.tcpsounds"] = scheme_zip({"format": 2, "app": "thechatplace", "name": "New",
                                                 "events": {}}, {})
    files["tcp-unknown-app.tcpsounds"] = scheme_zip({"format": 1, "app": "someday", "name": "X",
                                                     "events": {}}, {})
    files["tcp-no-json.tcpsounds"] = scheme_zip(None, {"a.wav": wav})
    files["tcp-too-many.tcpsounds"] = scheme_zip(
        {"format": 1, "app": "thechatplace", "name": "Many", "events": {}},
        {f"s{i}.wav": wav for i in range(64)})
    files["not-a-zip.tcpsounds"] = b"This is not a ZIP file."
    for name, data in files.items():
        (HERE / name).write_bytes(data)
    print(f"Wrote {len(files)} files to {HERE}")


if __name__ == "__main__":
    main()
