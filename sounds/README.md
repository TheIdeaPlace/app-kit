# Sounds

Short sounds any app can use, and the rules for how an app should use them. The first user is
[QuickMail](https://github.com/kellylford/QuickMail/issues/912). The Chat Place and GHManage are
likely next.

This folder holds files and rules rather than code an app calls, because the apps don't share a
language. QuickMail's C# player stays in QuickMail for now, and moves to `TheIdeaPlace.AppKit` when a
second .NET app wants sounds. A Python player belongs in `theideaplace-appkit`, built with the first
Python app that adopts sounds.

**These are a first version.** QuickMail ships all three styles and is asking its users what they
think, so expect them to change.

## The sounds

There are three styles, in `glass/`, `softbell/` and `wood/`. Each has every sound below with the
same melody, and only the tone differs, so a sound means the same thing in any style. Wood also
measures somewhat quieter than the other two.

Each file is a 44.1 kHz, 16-bit mono WAV, under one second, peaking at about -6 dB. Everything is
generated, so there is nothing to license: the sounds are under this repo's license like the code.

Names say what happened, not which app it happened in. Each app maps its own events to them.

| Name | Meaning | Shape | In QuickMail |
|---|---|---|---|
| `sent` | Something went out | Two notes, rising | Message sent |
| `queued` | Saved to go out later | One note, repeated softer | Message queued |
| `failed` | Something important didn't happen | Three notes, falling | Send failed |
| `error` | A smaller action failed | Two low notes, repeated | Delete, move or archive failed |
| `arrived` | Something new came in | Three notes, rising | New mail |
| `attention` | Something you asked to watch | Repeated note, then a higher one | Watched conversation |
| `connected` | Back online | Two low notes, rising | Connection restored |
| `disconnected` | Went offline | Two low notes, falling | Connection lost |
| `reminder` | Time for something | One high note, three times | Calendar reminder |
| `removed` | Something was deleted | Two quick low notes, falling | Message deleted |
| `filed` | Something was put away | Two quick notes, a small step up | Message archived |
| `moved` | Something was moved | Two quick notes, a bigger step up | Message moved |
| `matched` | A rule of yours fired | One bright note | Default for a rule |

Rising means something went or came right; falling means something was lost or failed.

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
- Let them use their own file, and copy it into the app's data so it survives the original moving and travels with settings.

**Don't flood**

- Play one at a time from a short queue, and drop anything past that rather than delaying it. QuickMail keeps at most 3 waiting.
- Drop the same sound repeated within a second.
- One sound for a batch, not one per item.
- No sound for a backlog: nothing older than launch, and nothing for more than a few items at once, such as after waking up.

**Don't sound twice**

- A Windows toast plays its own sound unless it's marked silent, so silence it when your sound plays (measured).
- If two things can sound for one item, record which item was sounded rather than relying on a timer.

**Stay quiet at the right times**

- Sounds that happen on their own stay quiet during presentations and full-screen use: on Windows, ask `SHQueryUserNotificationState`.
- Sounds answering something the person just did still play.
- That API does **not** report Do Not Disturb (measured), and Microsoft documents none that does. Say so in your docs, and point people to Mute.

**Playing them reliably**

- Never let an audio problem reach the caller or delay real work. Play on a background thread, and treat no audio device as normal.
- Open the device per sound, so it follows the default device and doesn't hold it awake.
- A player may report it has stopped while audio is still in its buffer. With NAudio 3.3 this lost about 50 ms from every sound; 200 ms of trailing silence fixed it.
- Limit the length of any file you didn't make when you open it, not only when it's chosen, since settings files and hand-copied files skip the chooser.
- Refuse file names that are Windows device names, such as `COM1.wav`, and store only a bare file name, never a path.
