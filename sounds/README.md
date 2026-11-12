# Sounds

Short sounds any app can use, and the rules for how an app should use them. The first user is
[QuickMail](https://github.com/kellylford/QuickMail/issues/912). The Chat Place and GHManage are
likely next.

This folder holds files and rules, because the apps don't share a language. QuickMail's C# player
stays in QuickMail for now, and moves to `TheIdeaPlace.AppKit` when a second .NET app wants sounds. The
Python player is `theideaplace_appkit.sounds` (see `python/README.md`), built for The Chat Place. An app
copies the WAVs it uses and records the commit they came from, since a pip install of `python/` can't
reach this folder.

Sound schemes, scheme files and the sound gallery work the same in every app: `SCHEMES.md` is the
spec, `apps.json` lists each app's events, and `testdata/` holds files every implementation is
checked against.

**These are a first version.** QuickMail ships all three styles and is asking its users what they
think, so expect them to change.

## The sounds

There are three styles, in `glass/`, `softbell/` and `wood/`. Each has every sound below with the
same melody, and only the tone differs, so a sound means the same thing in any style. Wood also
measures somewhat quieter than the other two.

Each file is a 44.1 kHz, 16-bit mono WAV, under one second, peaking at about -6 dB, apart from the soft
ones below, which sit under speech. Everything is
generated, so there is nothing to license: the sounds are under this repo's license like the code.

**One sound, one meaning, and each app has its own set.** Every sound is made for one event in one
app. No two events share a sound, and no app uses another app's sounds for something else.
The Chat Place's first set did exactly that, taking QuickMail's Filed (a message archived) for a
conversation compacted, Matched (a rule fired) for a message from another session, and so on, and
it was replaced. `apps.json` lists each app's events with its sound; a test fails if two events
share one or two apps' sets overlap. A new event gets a new sound, never an existing one.

### QuickMail's

At the top of each style folder, where QuickMail has always copied them from.

| Name | Meaning | Shape | In QuickMail |
|---|---|---|---|
| `sent` | Something went out | Two notes, rising | Message sent |
| `queued` | Saved to go out later | One note, repeated softer | Message queued |
| `failed` | Something important didn't happen | Three notes, falling | Send failed |
| `error` | A smaller action failed | Two low notes, repeated | Delete, move, archive or empty trash failed |
| `arrived` | Something new came in | Three notes, rising | New mail |
| `attention` | Something you asked to watch | Repeated note, then a higher one | Watched conversation |
| `connected` | Back online | Two low notes, rising | Connection restored |
| `disconnected` | Went offline | Two low notes, falling | Connection lost |
| `reminder` | Time for something | One high note, three times | Calendar reminder |
| `removed` | Something was deleted | Two quick low notes, falling | Message deleted |
| `emptied` | Everything in a place was deleted | Three quick notes, falling | Trash emptied |
| `filed` | Something was put away | Two quick notes, a small step up | Message archived |
| `moved` | Something was moved | Two quick notes, a bigger step up | Message moved |
| `matched` | A rule of yours fired | One bright note | Default for a rule |
| `edge` | Nowhere further to go | One soft, short middle note | Up or Home at the top of the message list, Down or End at the bottom |

### The Chat Place's

In `thechatplace/glass/`, `thechatplace/softbell/` and `thechatplace/wood/`: twenty-three sounds,
one for each event. The rules, the families and every sound's notes are in TheIdeaPlace/AIChat's
`dev-notes/sound-design.md`. In short, events in a family share a pattern:

| Family | Pattern | Its sounds |
|---|---|---|
| Work going on | A very short high tick, or a low hum; soft | `tool`, `working` |
| Your message | From D5, leaping an octave as it goes; dropping when you take it back (Withdraw an octave, Stop a low fourth); an answer comes to rest on the note Needs you calls on | `send`, `queue`, `steer`, `steertaken`, `queuesent`, `queuedelete`, `stop`, `answer` |
| Answers | Notes rising to an open fifth; an agent's reply handed on to the next agent starts the rise, then leaps an octave like a send | `reply`, `otherreply` (lower, softer), `handon` (softer), `aside` |
| Needs you | One note fast three times, then a leap up | `needsyou` |
| Something went wrong | Falling, a half-step bend, or a waver | `turnfailed`, `notsent`, `asidefailed`, `trouble`, `warning` |
| Housekeeping and other sessions | A chord settling down (an octave for a compacted conversation, a fourth, quieter, for a file written); two knocks and an answer | `compacted`, `write`, `peer` |

In every app, rising means something went or came right; falling means something was lost or
failed; a repeated note means waiting, or wanting you.

`python/tests/test_sound_design.py` compares every sound's notes with every other's, strictly
between apps and normally within one, so no sound can be mistaken for another before anyone has
to listen. Within an app it also fails two sounds that are both soft and very short and start
within a few semitones of each other, which blur together heard in passing whatever their notes:
The Chat Place's first Tool ran and File changed sounds did.

## Regenerating them

```
python generate.py .
python measure.py .
```

`generate.py` uses only the standard library, and the same input gives the same bytes, so a change
shows up in the diff only where a sound really changed. `measure.py` checks format, length, level and
clicks at the start and end, which matters because the sounds are written by code and an AI can't hear
them. It caught a style whose every sound began with a click. It can't tell you whether a sound is
pleasant, so a change still needs a person to listen.

## How an app should use sounds

These come from building and measuring QuickMail's sounds. Apply them unless you have a reason not to.

**Respect the person**

- Off by default. People using screen readers are listening to speech, and an update shouldn't add noises nobody asked for.
- A sound adds to an announcement or status text, and never replaces it.
- Have one Mute command that silences everything, and let it cut off a sound already playing.
- A sound the person chose to preview plays even when muted, and stops when they leave the screen.
- In the list of events, Space plays the selected event's sound, so a person can arrow through and hear each one. Don't rely on a Preview button's access key alone: a Mac has none.
- Let them use their own file, and copy it into the app's data so it survives the original moving and travels with settings.

**Don't flood**

- Play one at a time from a short queue, and drop anything past that rather than delaying it. QuickMail keeps at most 3 waiting.
- Drop the same sound repeated within a second.
- A sound that only says something is going on never waits: drop it if anything is playing or queued, since late it says nothing, and play at most one every 200 ms.
- Let what the person must hear go first, cutting off anything of that kind.
- One sound for a batch, not one per item.
- No sound for a backlog: nothing older than launch, and nothing for more than a few items at once, such as after waking up.

**Don't sound twice**

- A Windows toast or tray balloon plays its own sound unless it's marked silent (measured), so your sound can be a second one. Offer a setting to leave it out, off to start: what Windows does for someone's notifications is theirs to change. wx's `ShowBalloon` can't ask for `NIIF_NOSOUND`.
- If two things can sound for one item, record which item was sounded rather than relying on a timer.

**Stay quiet at the right times**

- Sounds that happen on their own stay quiet during presentations and full-screen use: on Windows, ask `SHQueryUserNotificationState`.
- Sounds answering something the person just did still play.
- That API does **not** report Do Not Disturb (measured), and Microsoft documents none that does. Say so in your docs, and point people to Mute.

**Playing them reliably**

- Never let an audio problem reach the caller or delay real work. Play on a background thread, and treat no audio device as normal.
- Open the device per sound, so it follows the default device and doesn't hold it awake.
- In Python on Windows, don't use `winsound` or `wx.adv.Sound`: `PlaySound` held a 40 ms clip for about 500 ms and can't be cut off from another thread (measured). `waveOut` through ctypes took about 75 ms and stops at once.
- Cutting a sound off and closing the device straight away can put a faint click out afterwards, about one time in seven with `waveOut` (measured). Holding the device open for 100 ms stopped it.
- A player may report it has stopped while audio is still in its buffer. With NAudio 3.3 this lost about 50 ms from every sound; 200 ms of trailing silence fixed it.
- Limit the length of any file you didn't make when you open it, not only when it's chosen, since settings files and hand-copied files skip the chooser.
- Refuse file names that are Windows device names, such as `COM1.wav`, and store only a bare file name, never a path.
