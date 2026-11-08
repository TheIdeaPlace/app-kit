#!/usr/bin/env python3
"""Generate app sounds: deterministic, standard library only, no licensed samples.

Usage: generate.py <output folder>

Writes QuickMail's set to <output>/<style>/<name>.wav and The Chat Place's to
<output>/thechatplace/<style>/<name>.wav: 44.1 kHz, 16-bit, mono, under one second,
peak-normalized so the sounds sit at an even level, apart from the ones meant to sit under
everything else (SOFT). Same input, same bytes.

Each app has its own sounds, made for its own events: one sound, one meaning, never borrowed from
another app (The Chat Place's first set did borrow QuickMail's, with other meanings, and was
replaced). The Chat Place's rules are in TheIdeaPlace/AIChat's dev-notes/sound-design.md. Each
sound has one melodic shape across all styles; a style only changes the timbre.
"""
import math
import struct
import sys
import wave
from pathlib import Path

RATE = 44100
PEAK = 0.5  # About -6 dBFS: leaves room next to speech. Volume is the app's to scale down.
MAX_SECONDS = 1.0


def hz(note):
    """MIDI note number to frequency."""
    return 440.0 * 2 ** ((note - 69) / 12)


C4, D4, E4, G4, A4 = 60, 62, 64, 67, 69
C5, D5, E5, G5, A5 = 72, 74, 76, 79, 81
C6, E6 = 84, 88
A3, C3 = 57, 48
G3, CS4, EB4, F4, B4, B5, D6, A6 = 55, 61, 63, 65, 71, 83, 86, 93
GS4, C7, DS4, G7, CS6, F3, B3 = 68, 96, 63, 103, 85, 53, 59

DEFAULT_TAIL = 0.35

# (note, start seconds, gain). The shape of each sound; styles only change the timbre.
# QuickMail's set, unchanged. It's also at the top of each style folder, where QuickMail has
# always copied it from.
SOUNDS = {
    "sent": [(G5, 0.00, 0.8), (C6, 0.09, 1.0)],
    "queued": [(E5, 0.00, 0.8), (E5, 0.20, 0.5)],
    "failed": [(E5, 0.00, 1.0), (C5, 0.13, 0.9), (A4, 0.26, 0.9)],
    "error": [(A3, 0.00, 1.0), (A3, 0.14, 1.0)],
    "arrived": [(C5, 0.00, 0.8), (E5, 0.10, 0.85), (G5, 0.20, 1.0)],
    "attention": [(G5, 0.00, 0.85), (G5, 0.11, 0.85), (C6, 0.22, 1.0)],
    "connected": [(C4, 0.00, 0.9), (G4, 0.18, 1.0)],
    "disconnected": [(G4, 0.00, 1.0), (C4, 0.22, 1.0)],
    "reminder": [(A5, 0.00, 1.0), (A5, 0.20, 0.9), (A5, 0.40, 0.8)],
    "removed": [(E4, 0.00, 1.0), (C4, 0.07, 0.9)],
    "emptied": [(A4, 0.00, 0.9), (E4, 0.07, 0.95), (C4, 0.14, 1.0)],
    "filed": [(C5, 0.00, 0.9), (D5, 0.07, 0.9)],
    "moved": [(D5, 0.00, 0.9), (G5, 0.07, 1.0)],
    "matched": [(E6, 0.00, 1.0)],
}

# The Chat Place's own set, by the rules in TheIdeaPlace/AIChat's dev-notes/sound-design.md.
# Families: work going on (ticks, a hum), your message (two or three quick middle notes),
# answers (rising open fifths), needs you (one note fast three times, then a leap), something
# went wrong (falling, a half-step bend, a waver), housekeeping (a chord settling) and another
# session (a knock).
TCP_SOUNDS = {
    # Work going on
    "tool": [(C7, 0.00, 0.8), (G7, 0.015, 1.0)],
    "edit": [(A6, 0.00, 0.8), (C7, 0.05, 1.0)],
    "working": [(C3, 0.00, 1.0)],
    # Your message
    "send": [(D5, 0.00, 0.8), (D6, 0.05, 1.0)],
    "queue": [(D5, 0.00, 1.0), (D5, 0.12, 0.6), (D5, 0.24, 0.35)],
    "steer": [(D5, 0.00, 0.75), (E5, 0.04, 0.8), (D6, 0.09, 1.0)],
    "steertaken": [(A5, 0.00, 0.8), (B5, 0.04, 0.85), (CS6, 0.08, 0.9), (D6, 0.12, 1.0)],
    "queuesent": [(D5, 0.00, 0.7), (D5, 0.10, 0.6), (D6, 0.16, 1.0)],
    "stop": [(C4, 0.00, 1.0), (F3, 0.05, 0.9)],
    # Answers
    "reply": [(G4, 0.00, 0.8), (D5, 0.09, 0.85), (G5, 0.18, 1.0)],
    "otherreply": [(G3, 0.00, 0.8), (D4, 0.09, 0.85), (G4, 0.18, 1.0)],
    "aside": [(E5, 0.00, 0.9), (A4, 0.08, 0.8), (E5, 0.16, 1.0)],
    # Needs you
    "needsyou": [(A5, 0.00, 0.85), (A5, 0.08, 0.85), (A5, 0.16, 0.85), (E6, 0.26, 1.0)],
    # Something went wrong
    "turnfailed": [(D5, 0.00, 1.0), (A4, 0.12, 0.95), (F4, 0.24, 0.95), (D4, 0.36, 0.95)],
    "notsent": [(E4, 0.00, 1.0), (DS4, 0.10, 1.0), (B3, 0.20, 1.0)],
    "asidefailed": [(E5, 0.00, 0.9), (A4, 0.08, 0.8), (GS4, 0.16, 1.0)],
    "trouble": [(D4, 0.00, 1.0), (CS4, 0.06, 1.0), (D4, 0.12, 1.0), (CS4, 0.18, 1.0)],
    "warning": [(E6, 0.00, 1.0), (C6, 0.12, 0.9), (E6, 0.24, 1.0)],
    # Housekeeping and other sessions
    "compacted": [(C5, 0.00, 0.6), (E5, 0.00, 0.6), (G5, 0.00, 0.6), (C4, 0.18, 1.0)],
    "peer": [(B5, 0.00, 1.0), (B5, 0.06, 0.85), (E5, 0.18, 0.9)],
}

# Sounds that happen often, or about something you aren't looking at, so they sit under speech
# rather than at the level of the rest. tail is how long the last note rings, so a tick is over
# quickly. Everything else uses PEAK and DEFAULT_TAIL.
SOFT = {}
TCP_SOFT = {
    "tool": {"peak": 0.16, "tail": 0.06},
    "edit": {"peak": 0.20, "tail": 0.07},
    "working": {"peak": 0.12, "tail": 0.12},
    "otherreply": {"peak": 0.32},
    "steertaken": {"peak": 0.30, "tail": 0.25},
    "queuesent": {"peak": 0.40},
    "stop": {"peak": 0.40, "tail": 0.18},
    "aside": {"peak": 0.35},
    "asidefailed": {"peak": 0.35},
}

#: Each app's set: (subfolder under the output, sounds, soft levels).
SETS = {
    "quickmail": ("", SOUNDS, SOFT),
    "thechatplace": ("thechatplace", TCP_SOUNDS, TCP_SOFT),
}


def peak_of(name, app="quickmail"):
    """The level a sound is normalized to, which measure.py checks."""
    return SETS[app][2].get(name, {}).get("peak", PEAK)


def bell(freq, seconds):
    """Soft bell: two-operator FM with an inharmonic modulator that fades as the note decays."""
    out = []
    for i in range(int(seconds * RATE)):
        t = i / RATE
        decay = math.exp(-t * 6.0)
        index = 2.0 * math.exp(-t * 9.0)
        attack = min(1.0, t / 0.003)
        out.append(attack * decay * math.sin(2 * math.pi * freq * t + index * math.sin(2 * math.pi * freq * 1.41 * t)))
    return out


def glass(freq, seconds):
    """Glass: a clean sine with quiet high partials, slow to decay."""
    out = []
    for i in range(int(seconds * RATE)):
        t = i / RATE
        attack = min(1.0, t / 0.008)
        s = (math.sin(2 * math.pi * freq * t) * math.exp(-t * 4.5)
             + 0.25 * math.sin(2 * math.pi * freq * 2.0 * t) * math.exp(-t * 9.0)
             + 0.10 * math.sin(2 * math.pi * freq * 3.01 * t) * math.exp(-t * 14.0))
        out.append(attack * s)
    return out


def wood(freq, seconds):
    """Wood: marimba-like decaying partials. Built from tones rather than a noise burst, which
    started with a click and set the peak so high that the sounds came out far too quiet."""
    out = []
    for i in range(int(seconds * RATE)):
        t = i / RATE
        attack = min(1.0, t / 0.002)
        s = (math.sin(2 * math.pi * freq * t) * math.exp(-t * 11.0)
             + 0.35 * math.sin(2 * math.pi * freq * 3.93 * t) * math.exp(-t * 30.0)
             + 0.10 * math.sin(2 * math.pi * freq * 9.2 * t) * math.exp(-t * 70.0))
        out.append(attack * s)
    return out


STYLES = {"glass": glass, "softbell": bell, "wood": wood}


def render(shape, voice, peak_level=PEAK, tail=DEFAULT_TAIL):
    """Mix a sound's notes, fade the tail, normalize, and return 16-bit samples."""
    length = min(MAX_SECONDS, max(start for _, start, _ in shape) + tail)
    mix = [0.0] * int(length * RATE)
    for note, start, gain in shape:
        offset = int(start * RATE)
        for i, value in enumerate(voice(hz(note), length - start)):
            if offset + i < len(mix):
                mix[offset + i] += gain * value
    fade = int(0.03 * RATE)  # Always end at silence, so no sound ends in a click.
    for i in range(fade):
        mix[len(mix) - 1 - i] *= i / fade
    peak = max(abs(v) for v in mix)
    return [int(round(v / peak * peak_level * 32767)) for v in mix]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = Path(sys.argv[1])
    for app, (sub, sounds, soft_levels) in SETS.items():
        for style, voice in STYLES.items():
            folder = root / sub / style if sub else root / style
            folder.mkdir(parents=True, exist_ok=True)
            for name, shape in sounds.items():
                soft = soft_levels.get(name, {})
                samples = render(shape, voice, peak_of(name, app), soft.get("tail", DEFAULT_TAIL))
                with wave.open(str(folder / f"{name}.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(RATE)
                    w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


if __name__ == "__main__":
    main()
