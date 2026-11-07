"""Objective checks on generated sounds, since nobody here can hear them: format, length, level, clicks."""
import struct
import sys
import wave
from pathlib import Path

from generate import peak_of

bad = 0
for path in sorted(p for p in Path(sys.argv[1]).rglob("*.wav") if not p.name.startswith("Listen")):
    with wave.open(str(path)) as w:
        fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        n = w.getnframes()
        s = struct.unpack(f"<{n}h", w.readframes(n))
    seconds = n / fmt[2]
    peak = max(abs(v) for v in s) / 32767
    rms = (sum(v * v for v in s) / n) ** 0.5 / 32767
    jump = max(abs(s[i] - s[i - 1]) for i in range(1, n)) / 32767
    problems = []
    if fmt != (1, 2, 44100): problems.append(f"format {fmt}")
    if seconds > 1.0: problems.append("too long")
    expected = peak_of(path.stem)
    if abs(peak - expected) > 0.01: problems.append(f"peak off, expected {expected}")
    if abs(s[0]) > 400: problems.append(f"starts at {s[0]}")
    if abs(s[-1]) > 40: problems.append(f"ends at {s[-1]}")
    bad += bool(problems)
    print(f"{path.parent.name}/{path.stem:13} {seconds:.2f}s peak {peak:.2f} rms {rms:.3f} maxjump {jump:.3f} {' '.join(problems)}")
print("problems:", bad)
sys.exit(1 if bad else 0)
