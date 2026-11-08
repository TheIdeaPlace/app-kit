"""No two sounds a listener would mistake for each other, checked from the exact notes in
sounds/generate.py, since nobody here can listen. The Chat Place's first set borrowed QuickMail's
sounds with other meanings; this keeps any app's set from coming close to another's, or to itself.

Two sounds count as the same gesture when they have the same number of notes (notes struck
together count once), the same steps between notes within a tolerance in semitones, similar
gaps, and start near each other. Between apps the tolerances are strict; within an app, normal."""
import importlib.util
import itertools
from pathlib import Path

import pytest

SOUNDS = Path(__file__).resolve().parents[2] / "sounds"
spec = importlib.util.spec_from_file_location("generate", SOUNDS / "generate.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

#: Pairs meant to sound related (dev-notes/sound-design.md, rule 4): another chat's reply is the
#: reply's rise an octave lower and softer.
RELATED = {frozenset({"thechatplace:reply", "thechatplace:otherreply"})}
STRICT = {"step": 2, "gap": 0.06, "start": 12}
NORMAL = {"step": 1, "gap": 0.04, "start": 7}


def shape(notes):
    notes = sorted(notes, key=lambda n: (n[1], n[0]))
    onsets, pitches = [], []
    for note, start, _gain in notes:
        if onsets and abs(start - onsets[-1]) < 0.005:
            continue
        onsets.append(start)
        pitches.append(note)
    return (pitches, [b - a for a, b in zip(pitches, pitches[1:])],
            [b - a for a, b in zip(onsets, onsets[1:])])


def alike(a, b, limits):
    pa, sa, ga = shape(a)
    pb, sb, gb = shape(b)
    return (len(pa) == len(pb)
            and all(abs(x - y) <= limits["step"] for x, y in zip(sa, sb))
            and all(abs(x - y) <= limits["gap"] for x, y in zip(ga, gb))
            and abs(pa[0] - pb[0]) <= limits["start"])


def all_sounds():
    return {f"{app}:{name}": notes for app, (_sub, sounds, _soft) in gen.SETS.items()
            for name, notes in sounds.items()}


def test_no_sound_is_like_another_apps():
    found = [(a, b) for (a, na), (b, nb) in itertools.combinations(all_sounds().items(), 2)
             if a.split(":")[0] != b.split(":")[0] and alike(na, nb, STRICT)]
    assert found == []


@pytest.mark.parametrize("app", ["thechatplace"])
def test_no_two_sounds_in_an_app_are_alike(app):
    sounds = {k: v for k, v in all_sounds().items() if k.startswith(app + ":")}
    found = [(a, b) for (a, na), (b, nb) in itertools.combinations(sounds.items(), 2)
             if frozenset({a, b}) not in RELATED and alike(na, nb, NORMAL)]
    assert found == []


def test_the_check_catches_what_it_should():
    """The first set's mistake: The Chat Place's tap was QuickMail's matched, an octave apart."""
    assert alike([(88, 0.0, 1.0)], [(81, 0.0, 1.0)], NORMAL)
    assert alike([(74, 0.0, 1.0), (79, 0.07, 1.0)], [(72, 0.0, 1.0), (77, 0.09, 1.0)], NORMAL)
    assert not alike([(74, 0.0, 1.0), (86, 0.05, 1.0)], [(79, 0.0, 1.0), (84, 0.09, 1.0)], STRICT)
