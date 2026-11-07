# Sound schemes and the sound gallery

How every Idea Place app keeps sound schemes, shares them as files, and takes them from the
gallery, so they behave the same in each app. QuickMail (C#) and The Chat Place (Python, through
`theideaplace_appkit.soundschemes` and `soundgallery`) follow this. The files in `testdata/` are
the test: an implementation that gives the results `testdata/README.md` lists follows the spec.

This is format 1. Everything added since QuickMail 0.8.69 is optional to read, so files from 0.8.69
still work, and 0.8.69 can read the new files. The reason for each rule is in TheIdeaPlace/app-kit#31.

## Words

- An **event** is something that happened in an app, such as a message being sent. Each app has its
  own events. `apps.json` lists them, with their **key**: 1 to 40 ASCII letters or digits,
  written PascalCase (`MessageSent`).
- A **built-in sound** is one of the WAV files in this folder, by name (`arrived`), played in a
  **pack**: `glass`, `softbell` or `wood`. Names say what happened, not which app it happened in.
  An app ships some of them; `apps.json` says which.
- A **scheme** is a name and a sound for each event. Each pack is also a **built-in scheme**, with
  the pack's id (`glass`, "Glass"). A person can make their own, which the app gives an id of its
  own.

## A stored sound

One event's sound is one of these strings. The prefixes match without regard to case.

| Value | Plays |
| --- | --- |
| `none` | Nothing. |
| `builtin:<name>` | The built-in sound in the scheme's pack. In a scheme of your own, Glass. |
| `builtin:<pack>/<name>` | The built-in sound in that pack, whatever the scheme. |
| `custom:<file>` | A file in the app's own sounds folder. |

A value that isn't one of these, names a pack or sound the app doesn't ship, or names an unsafe
file, is refused. Where it was read from a file, the event has no sound.

A **safe file name** is a bare name, at most 100 characters, with no `/ \ : * ? " < > |`, no
control characters, no leading or trailing space and no trailing `.`, that isn't a Windows device
name (`CON`, `PRN`, `AUX`, `NUL`, `CONIN$`, `CONOUT$`, `COM` or `LPT` with a digit or `¹²³`, with
any extension), and ends in `.wav` (or, in QuickMail, `.mp3`).

### Built-in and own schemes

- A built-in scheme keeps only the events a person changed. An event it doesn't keep plays the
  event's own built-in sound, in the scheme's pack.
- A scheme of your own keeps every event. An event it doesn't keep plays nothing ("blank means
  silent"), so a new scheme starts silent and you add only the sounds you want.
- **Reset** puts a built-in scheme back to how it ships. Only schemes of your own can be renamed
  or deleted. Deleting a scheme keeps its sound files.
- A scheme's name is trimmed, not empty, has no control characters, is at most 60 characters
  (counted in UTF-16 units, so an emoji is two), and is unique ignoring case.
- A scheme that names a scheme the app doesn't have plays as Glass.

## A scheme file

A ZIP file. Each app has its own extension (`apps.json`): `.qmsounds` for QuickMail, `.tcpsounds`
for The Chat Place. It holds:

- `scheme.json`, UTF-8 (a byte-order mark is allowed), at most 64 KB;
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
| `format` | yes | `1`. A larger number is from a newer app: say so and stop. |
| `app` | no | The app's id from `apps.json`. Missing means `quickmail`, as QuickMail 0.8.69 wrote no `app`. A file for another app is refused, naming that app. |
| `name` | yes | The scheme's name. |
| `author`, `license` | no | Who made it, and its licence (an SPDX id such as `CC0-1.0`). `null` or missing when not known. |
| `description` | no | One line, at most 300 characters. |
| `events` | yes | Event key to stored sound. Every event, from the scheme as it plays (below). |

Unknown fields are ignored when read, so a newer app can add one without a new format number.

### Writing one

- Every event is written, and a `builtin:<name>` with no pack is written with the pack it played
  in (`builtin:softbell/arrived`), so the scheme sounds the same wherever it's imported.
- Each distinct custom file goes in once. A file that's missing, larger than 5 MB, or would take the
  whole file past 80 MB is left out, and the event is written as `none`.
- `scheme.json` comes first. To make the same scheme give the same bytes (the gallery needs that),
  use DEFLATE, the timestamp 2026-01-01 00:00:00, `scheme.json` indented by 2 with non-ASCII
  characters as they are, events in the app's catalog order, and sounds sorted by name.

### Reading one

Limits are checked while reading, from what is actually read, never from what the archive says.

- The whole file is at most 80 MB, with at most 64 entries.
- Entry names have `\` taken as `/`. Only `scheme.json` and `sounds/<safe file name>` are read.
  Every other entry is ignored and never written anywhere.
- At most 100 events are read. A key that isn't 1 to 40 ASCII letters or digits, or isn't one of
  the app's events, is skipped. A value that doesn't parse is `none`.
- Each sound is at most 5 MB, and has to be one the app can play: for The Chat Place, a 16-bit PCM
  WAV, mono or stereo, 8 to 192 kHz, at most 30 seconds. One it can't is dropped, and its events are
  `none`. The person is told how many.
- A `custom:` file that isn't in the ZIP but is already in the app's sounds folder is used as it is.
- A sound whose name is already taken by a different file is added as `<name> 2.wav`, `<name> 3.wav`
  and so on, and the events that use it follow.
- Name: control characters removed, trimmed, cut to 60 UTF-16 units without splitting a character;
  empty becomes "Imported scheme". If the name is taken, ` 2`, ` 3` and so on is added, within 60.
  Author is cut to 60, licence to 40, description to 300.
- An imported scheme is always a new scheme of your own. Events the file doesn't list play nothing.

## The gallery

Schemes made by others, listened to before they're published, at
`https://theideaplace.github.io/soundgallery/<app>/`. An app can be pointed at another address for
testing (a folder, `https://`, or `http://` on this computer only).

### index.signed

`<base>/index.signed` is the base64 of a DER (RFC 3279) ECDSA P-256 SHA-256 signature, a newline
(within the first 512 bytes), then the exact bytes of the index JSON (1 to 262,144 bytes). The
signature is over those bytes. Nothing in the index is read until it verifies. The public key is
built into each app:

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

- `format` is an integer, at least 1. Larger than 1: the app says it needs updating.
- `app` has to be the reading app's id. One key signs every app's index, so this is what keeps an
  app from taking another app's list. (QuickMail 0.8.69 doesn't check it; the gallery wasn't
  published before `app` was added.)
- `schemes` is an array; only the first 200 are read, and a repeated `id` keeps the first.
- An entry with any of these wrong is left out, never used in part:
  - `id` matches `^[a-z0-9][a-z0-9-]{0,39}$`;
  - `sha256` matches `^[0-9a-f]{64}$`;
  - `file` is exactly `<id>-<first 16 of sha256><the app's extension>`;
  - `size` is a whole number from 1 to 80 MB;
  - `name`, `author` and `license` aren't empty once cleaned as above.
- `description` is cut to 300; `eventsWithSound` is optional, and kept between 0 and the app's
  number of events. Unknown fields are ignored.
- The JSON is at most 8 levels deep.

### Fetching

- `https://` only (or a folder, or `http://` on this computer). No redirects: anything but 200 is
  "couldn't be reached". At most 20 seconds for each whole read, body included.
- The index read stops at 262,144 + 512 bytes. A scheme file has to be exactly its `size`, and its
  SHA-256 is compared in fixed time before anything in it is read. Then it's imported as a scheme
  file, with every check above.
- What a person is told:
  - can't connect, time out, or not 200: "The gallery could not be reached. Check your connection,
    then choose Reload."
  - signature or index wrong: "The gallery's list could not be checked, so it was not used. Try
    again later."
  - a download that isn't the listed file: "The download of <name> was not the file the gallery
    lists, so it was not used. Try again later."

### Where an added scheme came from

An app keeps, with a scheme added from the gallery, its gallery `id` and the `sha256` of the file it
came from. Browsing then says "Added" for that scheme, and can offer an update when the gallery's
`sha256` for the `id` changes. A scheme that's been edited since keeps the id, so an update can say
it would replace the changes.

## The gallery's own rules

What TheIdeaPlace/soundgallery's validator adds, so anything it publishes passes every app's checks:

- Licence `CC0-1.0` or `CC-BY-4.0`; name and author required.
- Only the app's events and built-in sounds, at least one event with a sound.
- **Sound files are 16-bit PCM WAV**, mono or stereo, 8 to 192 kHz, at most 5 MB and 30 seconds:
  what every app can play. Every file present is used, and no two differ only in case.
- No links, no files other than `scheme.json`, `sounds/` and `README.md`.
- Names unique in each app, ignoring case.
