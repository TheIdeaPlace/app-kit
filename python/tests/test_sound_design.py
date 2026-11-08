"""No two sounds a listener would mistake for each other, checked from the exact notes in
sounds/generate.py, since nobody here can listen. The Chat Place's first set borrowed QuickMail's
sounds with other meanings; this keeps any app's set from coming close to another's, or to itself.

Two sounds count as the same gesture when they have the same number of notes (notes struck
together count once), the same steps between notes within a tolerance in semitones, similar
gaps, and start near each other. Between apps the tolerances are strict; within an app, normal.

That compares shapes, which you follow when you listen for them. Two sounds can differ in shape
and still blur together when you aren't paying attention: The Chat Place's Tick and Double tick
(C7 and G7 15 ms apart, A6 then C7 50 ms apart) were both a soft, high blip of about a tenth of a
second, and passed. So within an app, two sounds that are both very short and both soft, and start
within a few semitones of each other, count as too alike whatever their shape."""
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
#: Heard in passing: a sound this short (seconds, last note's start plus its ring) and this soft
#: (peak level; PEAK is 0.5) is a blip, and two blips this close in register are one sound.
BLIP = {"seconds": 0.2, "peak": 0.25, "start": 7}


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


def blip(notes, level):
    """A sound's first pitch when it's a soft, very short blip, or None. ``level`` is its entry
    in the app's soft levels (generate.py's TCP_SOFT), empty for a sound at the normal level."""
    seconds = min(gen.MAX_SECONDS, max(start for _, start, _ in notes)
                  + level.get("tail", gen.DEFAULT_TAIL))
    if seconds <= BLIP["seconds"] and level.get("peak", gen.PEAK) <= BLIP["peak"]:
        return shape(notes)[0][0]
    return None


def blips_alike(a, b):
    return a is not None and b is not None and abs(a - b) <= BLIP["start"]


@pytest.mark.parametrize("app", sorted(gen.SETS))
def test_no_two_soft_blips_in_an_app_are_alike(app):
    _sub, sounds, soft = gen.SETS[app]
    pitch = {name: blip(notes, soft.get(name, {})) for name, notes in sounds.items()}
    found = [(a, b) for a, b in itertools.combinations(sounds, 2)
             if frozenset({f"{app}:{a}", f"{app}:{b}"}) not in RELATED
             and blips_alike(pitch[a], pitch[b])]
    assert found == []


def test_the_blip_check_catches_tick_and_double_tick():
    """The pair it was written for, as they were (both pass the shape check): it fails them."""
    tick = blip([(96, 0.0, 0.8), (103, 0.015, 1.0)], {"peak": 0.16, "tail": 0.06})
    double_tick = blip([(93, 0.0, 0.8), (96, 0.05, 1.0)], {"peak": 0.20, "tail": 0.07})
    assert not alike([(96, 0.0, 0.8), (103, 0.015, 1.0)], [(93, 0.0, 0.8), (96, 0.05, 1.0)], NORMAL)
    assert blips_alike(tick, double_tick)
    # The hum is as short and soft, but four octaves down: not the same blip.
    assert not blips_alike(tick, blip(gen.TCP_SOUNDS["working"], gen.TCP_SOFT["working"]))
    # A soft sound with a longer ring, or a short one at the normal level, isn't a blip.
    assert blip([(93, 0.0, 1.0)], {"peak": 0.20, "tail": 0.25}) is None
    assert blip([(93, 0.0, 1.0)], {"tail": 0.1}) is None


def test_the_check_catches_what_it_should():
    """The first set's mistake: The Chat Place's tap was QuickMail's matched, an octave apart."""
    assert alike([(88, 0.0, 1.0)], [(81, 0.0, 1.0)], NORMAL)
    assert alike([(74, 0.0, 1.0), (79, 0.07, 1.0)], [(72, 0.0, 1.0), (77, 0.09, 1.0)], NORMAL)
    assert not alike([(74, 0.0, 1.0), (86, 0.05, 1.0)], [(79, 0.0, 1.0), (84, 0.09, 1.0)], STRICT)
