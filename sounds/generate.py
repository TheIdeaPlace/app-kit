#!/usr/bin/env python3
"""Generate app sounds: deterministic, standard library only, no licensed samples.

Usage: generate.py <output folder>

Writes <output>/<style>/<name>.wav for every style and sound name: 44.1 kHz, 16-bit, mono,
under one second, peak-normalized so the sounds sit at an even level. Same input, same bytes.

Names say what happened rather than which app it happened in, so any app can map its own
events to them. Each name has one melodic shape across all styles; a style only changes the
timbre. Rising means something went or came right, falling means something was lost or failed.
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

# (note, start seconds, gain). The shape of each sound; styles only change the timbre.
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


def render(shape, voice):
    """Mix a sound's notes, fade the tail, normalize, and return 16-bit samples."""
    tail = 0.35
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
    return [int(round(v / peak * PEAK * 32767)) for v in mix]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = Path(sys.argv[1])
    for style, voice in STYLES.items():
        folder = root / style
        folder.mkdir(parents=True, exist_ok=True)
        for name, shape in SOUNDS.items():
            samples = render(shape, voice)
            with wave.open(str(folder / f"{name}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


if __name__ == "__main__":
    main()
