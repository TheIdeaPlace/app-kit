# Sound schemes and the sound gallery

This is how every Idea Place app keeps sound schemes, shares them as files, and takes them from
the gallery, so they behave the same in each app. QuickMail (C#) and The Chat Place (Python, through
`theideaplace_appkit.soundschemes` and `soundgallery`) follow it.

The files in `testdata/` are the test. An implementation that gives the results
`testdata/README.md` describes follows the spec. Where this page and those results seem to differ,
the results are right and this page needs fixing.

This is format 1. Everything added since QuickMail 0.8.69 is optional to read, so files from 0.8.69
still work, and 0.8.69 can read the new files. The reason for each rule is in TheIdeaPlace/app-kit#31
and #32.

## Words and units

- **Event:** something that happened in an app, such as a message being sent. Each app has its own
  events. `apps.json` lists them in the app's order, each with its **key**, its label and its
  default built-in sound. A key is 1 to 40 ASCII letters or digits, written PascalCase
  (`MessageSent`).
- **Built-in sound:** one of the WAV files in this folder, by name (`arrived`), played in a
  **pack**: `glass`, `softbell` or `wood`. Names say what happened, not which app it happened in. An
  app ships the built-in sounds its events use by default, and no others.
- **Scheme:** a name, and a sound for each event.
  - Each pack is also a **built-in scheme**, with the pack's id (`glass`, "Glass").
  - A person can make **schemes of their own**, and the app gives each one an id.
- **Sizes** are exact byte counts:
  - 5 MB is 5,242,880;
  - 80 MB is 83,886,080;
  - 64 KB is 65,536;
  - 256 KB is 262,144.
- **Lengths** are counted in UTF-16 code units, so an emoji counts as two.
- **Control characters** are Unicode category Cc: U+0000 to U+001F and U+007F to U+009F.
- **Ignoring case** compares each character's simple uppercase, one character for one (.NET's
  `OrdinalIgnoreCase`), so `Knock.wav` and `KNOCK.WAV` match but `straße` and `STRASSE` don't.

## Cleaning text from a file

A name, author, licence, description or app id read from a file or the gallery goes through these
steps, in order:

1. remove control characters and unpaired surrogates;
2. trim spaces from both ends;
3. cut to the limit without splitting a character;
4. trim again.

## JSON

JSON is strict RFC 8259:

- `NaN`, `Infinity` and `-Infinity` are refused.
- Objects and arrays may nest at most 8 levels deep, counting the outermost one.
- If a key appears twice in an object, the last one counts.
- A whole number has to fit in 32 bits, signed; one that doesn't is treated as wrong.
- A byte-order mark is allowed at the start of `scheme.json`, but not at the start of the index.

## A stored sound

One event's sound is one of these strings:

| Value | Plays |
| --- | --- |
| `none` | Nothing. |
| `builtin:<name>` | The built-in sound in the scheme's pack. In a scheme of your own, Glass. |
| `builtin:<pack>/<name>` | The built-in sound in that pack, whatever the scheme. |
| `custom:<file>` | A file in the app's own sounds folder. |

The prefix, the pack and the built-in name match whatever their case, and are kept in lowercase
(`BUILTIN:SoftBell/Tap` becomes `builtin:softbell/tap`). A file name keeps its case. A value that
isn't one of these, or names a pack or sound the app doesn't ship, or names an unsafe file, can't
be used. Where it was read from a file, that event has no sound.

A **safe file name**:

- is a bare name of at most 100 UTF-16 units;
- has no `/ \ : * ? " < > |`;
- has no control, formatting (category Cf, such as a right-to-left override) or surrogate
  characters;
- has no space at either end, and doesn't end with `.`;
- isn't a Windows device name (`CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, or `COM` or `LPT`
  followed by a digit or `¹²³`), whatever its extension;
- ends in `.wav` (in QuickMail, `.wav` or `.mp3`).

### Built-in and own schemes

- **A built-in scheme** keeps only the events a person changed. An event it doesn't keep plays the
  event's own built-in sound, in the scheme's pack.
- **A scheme of your own** keeps every event. An event it doesn't keep plays nothing ("blank means
  silent"), so a new scheme starts silent and you add only the sounds you want.
- **Reset** puts a built-in scheme back to how it ships. Only schemes of your own can be renamed or
  deleted. Deleting a scheme keeps its sound files.
- **A scheme's name** is trimmed, isn't empty, has no control characters, is at most 60 units long,
  and is unique ignoring case.
- **A scheme id the app doesn't have** plays as Glass.

### Sounds in a scheme file

A sound in a scheme file, or in the gallery, is one every app reads alike: a 16-bit PCM WAV
(format tag 1, not WAVE_FORMAT_EXTENSIBLE), mono or stereo, 8 to 192 kHz, at most 30 seconds and
5 MB. A file a person picks for themselves on their own computer is up to each app: QuickMail plays
MP3 too, and either app may play a WAVE_FORMAT_EXTENSIBLE file it can read.

## A scheme file

A ZIP file. Each app has its own extension (`apps.json`): `.qmsounds` for QuickMail and
`.tcpsounds` for The Chat Place. It holds:

- `scheme.json`, in UTF-8, at most 64 KB;
- `sounds/<file>` for each `custom:` file an event uses, with a safe file name.

```json
{
  "format": 1,
  "app": "thechatplace",
  "name": "Rain at Night",
  "author": "Someone",
  "license": "CC0-1.0",
  "description": "Soft sounds for working late.",
  "events": { "MessageSent": "builtin:softbell/sent", "NeedsYou": "custom:knock.wav" }
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `format` | yes | `1`. A larger whole number means a newer app made it: say so and stop. Anything else means the file can't be read. |
| `app` | no | The app's id from `apps.json`. Missing or `null` means `quickmail`, because QuickMail 0.8.69 wrote no `app`. A file for another app is refused, and the message names that app: by app-kit's name for it if it has one, otherwise by the value cleaned to 40 units, otherwise "another app". |
| `name` | yes | The scheme's name. |
| `author`, `license` | no | Who made it, and its licence (an SPDX id such as `CC0-1.0`). `null` or missing when not known. |
| `description` | no | One line, at most 300 units. |
| `events` | yes | An object of event key to stored sound. A file that's written lists every event. |

Unknown fields are ignored when reading, so a newer app can add one without a new format number.

### Writing one

- Every event is written in the app's order. A `builtin:<name>` with no pack is written with the
  pack it played in (`builtin:softbell/arrived`), so the scheme sounds the same wherever it's
  imported.
- Each distinct custom file goes in once. A file that's missing, larger than 5 MB, or would take
  the whole file past 80 MB minus 64 KB is left out, and its events are written as `none`.
- `scheme.json` comes first, indented by 2, with non-ASCII characters left as they are. Sounds
  follow in name order.
- Entries use DEFLATE, the timestamp 2026-01-01 00:00:00, "made by" system 0 and external
  attributes 0. With these, one implementation always gives the same bytes for the same scheme,
  which the gallery needs, since it names a file by its hash. Two different implementations' DEFLATE
  output needn't match, and doesn't have to.

### Reading one

Anything wrong with the file gives one of the messages below, never a crash. Limits are checked
while reading, from the bytes actually read, never from what the archive says about itself.

- **Size:** the whole file is at most 80 MB, counting every byte decompressed, and has at most 64
  entries. Otherwise: "This sound scheme is too large to use." or the unreadable message.
- **Entry names:** `\` in a name is read as `/`. Only `scheme.json` and `sounds/<safe file name>`
  are read; every other entry, including one whose name contains a NUL, is ignored and never
  written anywhere. If a name appears twice, the first counts.
- **Compression:** only stored and DEFLATE entries that aren't encrypted can be read. If
  `scheme.json` can't be read, the whole file can't be. If a sound can't be read, it's dropped.
- **scheme.json** is strict JSON (above), at most 64 KB. If it isn't, the message is "This file
  could not be read as a sound scheme for <app>."
- **Events:** at most the first 100 are read. A key that isn't one of the app's events is skipped.
  A value that can't be used becomes `none`.
- **Sounds:** each `custom:` file an event uses is taken from the ZIP, in the app's event order.
  - It's **dropped** if it's missing from the ZIP and not already in the app's sounds folder, larger
    than 5 MB, damaged, not a sound the scheme file may hold, or named the same, ignoring case, as an
    earlier file the scheme uses, whether or not that one was kept (Windows would treat the two as
    one file).
  - A file that isn't in the ZIP but is already in the app's sounds folder is used as it is.
  - Every event that used a dropped file becomes `none`, and the person is told how many events
    that is: the **dropped** count. A value that couldn't be used in the first place isn't counted.
- **Names:** text is cleaned (above): the name to 60 units, the author to 60, the licence to 40 and
  the description to 300. An empty name becomes "Imported scheme". If the name is taken, ` 2`, ` 3`
  and so on is added, and the name is cut to fit within 60.
- **Result:** an imported scheme is always a new scheme of your own. Events the file doesn't list
  play nothing.
- **Putting sounds in place:** a sound whose name is already taken by a different file, or by
  anything that isn't a plain file, is added as `<name> 2.wav`, `<name> 3.wav` and so on, and the
  events that use it follow. A file is never overwritten.

## The gallery

Schemes made by others, listened to before they're published, at
`https://theideaplace.github.io/soundgallery/<app>/`. An app can be pointed at another address for
testing: a folder on this computer, `https://`, or `http://` on this computer only. Never a network
share, and never an address with a user name in it.

### index.signed

`<base>/index.signed` is laid out as three parts:

1. the base64 of a DER (RFC 3279) ECDSA P-256 SHA-256 signature, and nothing else on that line;
2. a newline (`\n` alone, no `\r`), which must be one of the first 512 bytes;
3. the exact bytes of the index JSON, 1 to 262,144 bytes long, with no byte-order mark.

The signature is over those JSON bytes, and nothing in the index is read until it verifies. The
public key is built into each app:

```
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEQwiXJpzslRlBsXFp1i5SA+mq+RjX
omSYj0/L6q6wMmhKLQjZvF5NUTIHNZVBJG4SkaZPgEH0Dfl/BQpvo8otKQ==
-----END PUBLIC KEY-----
```

```json
{
  "format": 1,
  "app": "thechatplace",
  "schemes": [
    { "id": "rain-at-night", "name": "Rain at Night", "author": "Someone",
      "description": "Soft sounds for working late.", "license": "CC0-1.0",
      "file": "rain-at-night-0123456789abcdef.tcpsounds", "size": 12345,
      "sha256": "<64 lowercase hex>", "eventsWithSound": 8 }
  ]
}
```

- **`format`** is a whole number, at least 1. If it's larger than 1, the app says it needs
  updating.
- **`app`** has to be the reading app's id. One key signs every app's index, so this is what stops
  an app from taking another app's list. QuickMail 0.8.69 doesn't check it, but the gallery wasn't
  published before `app` was added.
- **`schemes`** is an array. Only the first 200 entries are read, and if an `id` appears twice, the
  first counts.
- **An entry is left out entirely**, never used in part, unless all of these hold:
  - `id` matches `^[a-z0-9][a-z0-9-]{0,39}$`;
  - `sha256` matches `^[0-9a-f]{64}$`;
  - `file` is exactly `<id>-<first 16 characters of sha256><the app's extension>`;
  - `size` is a whole number from 1 to 80 MB;
  - `name`, `author` and `license` aren't empty once cleaned.
- **Other fields:** `description` is cut to 300 units. `eventsWithSound` is optional, and is kept
  between 0 and the app's number of events. Unknown fields are ignored.

### Fetching

- **Answers:** no redirects; anything but a 200 answer is "couldn't be reached". At most 20 seconds
  for each whole fetch: connecting, the headers and the body together.
- **Size:** the index read stops at 262,144 + 512 bytes. A scheme file has to be exactly its
  `size`, and its SHA-256 is compared in fixed time before anything in it is read. It's then
  imported as a scheme file, with every check above.
- **What a person is told:**
  - can't connect, timed out, not a 200 answer, or a damaged answer: "The gallery could not be
    reached. Check your connection, then choose Reload."
  - signature or index wrong, or no way to check the signature: "The gallery's list could not be
    checked, so it was not used. Try again later."
  - a download that isn't the listed file: "The download of <name> was not the file the gallery
    lists, so it was not used. Try again later."

### Where an added scheme came from

An app keeps two things with a scheme added from the gallery: its gallery `id`, and the `sha256` of
the file it came from.

- When browsing, the gallery says "Added" for that scheme.
- When the gallery's `sha256` for that `id` changes, it offers an update, which says it replaces any
  changes the person made.

## The gallery's own rules

TheIdeaPlace/soundgallery's validator adds these rules, so anything it publishes passes every app's
checks:

- The licence is `CC0-1.0` or `CC-BY-4.0`, and name and author are required.
- A scheme uses only its app's events and built-in sounds, and at least one event has a sound.
- Sound files are 16-bit PCM WAV, as above. Every file present is used, and no two names differ
  only in case.
- A scheme folder holds no links, and no files other than `scheme.json`, `sounds/` and `README.md`.
- Names are unique within each app, ignoring case.
