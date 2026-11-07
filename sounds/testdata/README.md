# Test files for sound schemes and the gallery

Every implementation of `../SCHEMES.md` reads these and checks it gets what `expected.json` says.
That includes app-kit's Python (`python/tests/test_soundschemes.py`, `test_soundgallery.py`) and
QuickMail's C#. So an app that passes is reading the same files the same way as the others.

- **`index-*.signed`:** gallery lists, signed with `test-key.pem`. Read them with
  `test-key.pub.pem` as the gallery's key. `other-key.pem` signs one, to check that a list signed
  by any other key is refused. These keys are for tests only and sign nothing real.
- **`*.tcpsounds`, `*.qmsounds`:** scheme files, some deliberately hostile: paths that try to leave
  the folder, device names, unknown events and packs, an MP3 and an 8-bit WAV, too many entries.
- **`expected.json`:** for each file, and each app that reads it, either what it gives or why it's
  refused, with the message where the spec words it.

`make.py` made them. Run it again only to change them, then update `expected.json`. The `.signed`
files change on every run, because ECDSA signatures differ each time.
