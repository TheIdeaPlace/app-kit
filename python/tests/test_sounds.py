"""theideaplace_appkit.sounds. Nothing here makes a sound: outputs are fakes, or silent audio."""
import os
import struct
import subprocess
import sys
import threading
import time
import wave

import pytest

from pathlib import Path

from theideaplace_appkit import sounds
from theideaplace_appkit.sounds import AMBIENT, NORMAL, URGENT, SoundPlayer, SoundPolicy, SoundProblem


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make_wav(path, seconds=0.05, width=2, channels=1, rate=44100, value=0):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(int(seconds * rate) * channels * width) if not value
                      else struct.pack("<h", value) * int(seconds * rate) * channels)
    return str(path)


class FakeOutput:
    """Plays until released, or for no time at all with ``hold`` off."""

    def __init__(self, hold=False, fail=()):
        self.hold = hold
        self.fail = set(fail)
        self.played = []
        self.cancelled = []
        self.started = threading.Event()
        self.release = threading.Event()

    def play(self, path, volume, cancel):
        self.played.append((path, volume))
        self.started.set()
        if path in self.fail:
            raise SoundProblem("no")
        if self.hold:
            while not self.release.is_set():
                if cancel.is_set():
                    self.cancelled.append(path)
                    return
                time.sleep(0.002)


def settle(player, seconds=3.0):
    end = time.monotonic() + seconds
    while player.busy and time.monotonic() < end:
        time.sleep(0.002)
    assert not player.busy


@pytest.fixture
def policy():
    clock = Clock()
    made = SoundPolicy(clock=clock)
    made.clock = clock
    return made


def test_ambient_is_dropped_while_anything_plays_or_waits(policy):
    assert not policy.admit("tap", AMBIENT, busy=True, waiting=0)
    assert not policy.admit("tap", AMBIENT, busy=False, waiting=1)
    assert policy.admit("tap", AMBIENT, busy=False, waiting=0)


def test_ambient_plays_at_most_one_each_gap_whichever_sound(policy):
    assert policy.admit("tap", AMBIENT, False, 0)
    policy.clock.now += 0.19
    assert not policy.admit("changed", AMBIENT, False, 0)
    policy.clock.now += 0.02
    assert policy.admit("changed", AMBIENT, False, 0)


def test_a_dropped_ambient_does_not_restart_the_gap(policy):
    assert policy.admit("tap", AMBIENT, False, 0)
    policy.clock.now += 0.15
    assert not policy.admit("tap", AMBIENT, False, 0)
    policy.clock.now += 0.06
    assert policy.admit("tap", AMBIENT, False, 0)


def test_same_sound_within_a_second_is_dropped_but_another_is_not(policy):
    assert policy.admit("sent", NORMAL, False, 0)
    policy.clock.now += 0.5
    assert not policy.admit("sent", NORMAL, False, 0)
    assert policy.admit("arrived", NORMAL, False, 0)
    policy.clock.now += 0.51
    assert policy.admit("sent", NORMAL, False, 0)


def test_normal_is_dropped_past_three_waiting_and_urgent_is_not(policy):
    assert not policy.admit("sent", NORMAL, True, 3)
    assert policy.admit("attention", URGENT, True, 3)


def test_urgent_repeats_are_still_dropped(policy):
    assert policy.admit("attention", URGENT, False, 0)
    assert not policy.admit("attention", URGENT, False, 0)


def test_plays_in_order_at_the_given_volume():
    output = FakeOutput()
    player = SoundPlayer(output)
    assert player.play("a", volume=0.5)
    assert player.play("b")
    settle(player)
    assert output.played == [("a", 0.5), ("b", 1.0)]
    player.close()


def test_urgent_goes_next_and_cuts_off_ambient():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("tap", priority=AMBIENT)
    assert output.started.wait(2)
    player.play("attention", priority=URGENT)
    end = time.monotonic() + 2
    while not output.cancelled and time.monotonic() < end:
        time.sleep(0.002)
    assert output.cancelled == ["tap"]
    output.release.set()
    settle(player)
    assert [p for p, _ in output.played] == ["tap", "attention"]
    player.close()


def test_urgent_leaves_a_normal_sound_alone_and_goes_ahead_of_the_queue():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("sent")
    assert output.started.wait(2)
    player.play("queued")
    player.play("attention", priority=URGENT)
    time.sleep(0.02)
    assert output.cancelled == []
    output.release.set()
    settle(player)
    assert [p for p, _ in output.played] == ["sent", "attention", "queued"]
    player.close()


def test_urgent_sounds_keep_their_order_and_none_is_lost():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("sent")
    assert output.started.wait(2)
    for name in "abcde":
        assert player.play(name, priority=URGENT)
    output.release.set()
    settle(player)
    assert [p for p, _ in output.played] == ["sent", "a", "b", "c", "d", "e"]
    player.close()


def test_urgent_clears_a_tap_still_waiting():
    output = FakeOutput()
    player = SoundPlayer(output)
    with player._lock:  # holds the worker off, so the tap is still waiting
        player._queue.append(sounds._Item("tap", "tap", AMBIENT, 1.0, None, False))
        player._add_urgent(sounds._Item("attention", "attention", URGENT, 1.0, None, False))
        assert [i.path for i in player._queue] == ["attention"]
    player.close()


def test_urgent_cuts_off_a_long_sound_after_a_moment(monkeypatch):
    # Long enough that "not cut off yet" still holds on a slow runner: at 0.05 the
    # wait ran out first on macOS under coverage (#41).
    monkeypatch.setattr(sounds, "URGENT_WAIT_SECONDS", 0.5)
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("my long file")
    assert output.started.wait(2)
    asked = time.monotonic()
    player.play("attention", priority=URGENT)
    time.sleep(0.01)
    assert output.cancelled == []
    end = time.monotonic() + 2
    while not output.cancelled and time.monotonic() < end:
        time.sleep(0.002)
    assert output.cancelled == ["my long file"]
    # It waited for URGENT_WAIT_SECONDS, not some shorter time; a slow runner
    # only makes the gap longer.
    assert time.monotonic() - asked >= 0.4
    output.release.set()
    settle(player)
    player.close()


def test_urgent_cuts_off_a_long_urgent_sound_too(monkeypatch):
    monkeypatch.setattr(sounds, "URGENT_WAIT_SECONDS", 0.05)
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("my long alert", priority=URGENT)
    assert output.started.wait(2)
    player.play("failed", priority=URGENT)
    end = time.monotonic() + 2
    while not output.cancelled and time.monotonic() < end:
        time.sleep(0.002)
    assert output.cancelled == ["my long alert"]
    output.release.set()
    settle(player)
    player.close()


def test_a_full_queue_of_alerts_keeps_a_preview():
    output = FakeOutput()
    player = SoundPlayer(output)
    with player._lock:
        player._queue.append(sounds._Item("p", "p", NORMAL, 1.0, None, True))
        for name in "abcd":
            player._add_urgent(sounds._Item(name, name, URGENT, 1.0, None, False))
        assert [i.path for i in player._queue] == ["a", "b", "c", "d", "p"]
    player.close()


def test_no_thread_for_the_wait_cuts_off_at_once_rather_than_raising(monkeypatch):
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("long")
    assert output.started.wait(2)

    def refuse(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Timer, "start", refuse)
    assert player.play("attention", priority=URGENT) is True
    output.release.set()
    settle(player)
    player.close()


def test_preview_leaves_an_alert_waiting():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("sent")
    assert output.started.wait(2)
    player.play("attention", priority=URGENT)
    player.preview("p")
    output.release.set()
    settle(player)
    assert [p for p, _ in output.played] == ["sent", "attention", "p"]
    player.close()


def test_a_preview_can_fall_back():
    output = FakeOutput(fail=["gone"])
    player = SoundPlayer(output)
    player.preview("gone", fallback="builtin")
    settle(player)
    assert [p for p, _ in output.played] == ["gone", "builtin"]
    player.close()


def test_no_thread_to_be_had_never_reaches_the_caller(monkeypatch):
    def refuse(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", refuse)
    player = SoundPlayer(FakeOutput())
    assert player.play("a") is False
    assert not player.busy
    player.preview("b")
    assert not player.busy
    player.close()


def test_stop_cuts_off_and_forgets_what_waits():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("a")
    assert output.started.wait(2)
    player.play("b")
    player.stop()
    settle(player)
    assert output.cancelled == ["a"]
    assert [p for p, _ in output.played] == ["a"]
    player.close()


def test_preview_ends_what_is_playing_and_skips_the_policy():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("a")
    assert output.started.wait(2)
    player.preview("a", volume=0.3)
    player.preview("a", volume=0.3)  # twice in a row isn't "the same sound again"
    end = time.monotonic() + 2
    while len(output.played) < 2 and time.monotonic() < end:
        time.sleep(0.002)
    output.release.set()
    settle(player)
    assert output.played[-1] == ("a", 0.3)
    player.close()


def test_stop_preview_leaves_other_sounds_alone():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.play("event")
    assert output.started.wait(2)
    player.stop_preview()
    time.sleep(0.02)
    assert output.cancelled == []
    output.release.set()
    settle(player)
    player.close()


def test_stop_preview_ends_a_preview():
    output = FakeOutput(hold=True)
    player = SoundPlayer(output)
    player.preview("p")
    assert output.started.wait(2)
    player.stop_preview()
    settle(player)
    assert output.cancelled == ["p"]
    player.close()


def test_a_failure_never_reaches_the_caller_and_the_queue_goes_on():
    output = FakeOutput(fail=["bad"])
    player = SoundPlayer(output)
    player.play("bad")
    player.play("good")
    settle(player)
    assert [p for p, _ in output.played] == ["bad", "good"]
    player.close()


def test_a_sound_that_fails_plays_its_fallback():
    output = FakeOutput(fail=["custom"])
    player = SoundPlayer(output)
    player.play("custom", fallback="builtin")
    settle(player)
    assert [p for p, _ in output.played] == ["custom", "builtin"]
    player.close()


def test_a_broken_output_is_logged_once_per_sound(caplog):
    output = FakeOutput(fail=["bad"])
    player = SoundPlayer(output, SoundPolicy(same_gap=0))
    for _ in range(3):
        player.play("bad")
    settle(player)
    assert sum("Couldn't play" in r.message for r in caplog.records) == 1
    player.close()


def test_after_close_nothing_plays_and_close_can_repeat():
    output = FakeOutput()
    player = SoundPlayer(output)
    player.close()
    player.close()
    assert not player.play("a")
    player.preview("a")
    assert output.played == []


def test_play_from_many_threads_at_once():
    output = FakeOutput()
    player = SoundPlayer(output, SoundPolicy(same_gap=0, max_waiting=10000))
    threads = [threading.Thread(target=player.play, args=(f"s{i}",)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    settle(player)
    assert len(output.played) == 50
    player.close()


@pytest.mark.parametrize("name", [
    "tap.wav", "my sound.wav", "Uni\u00e9.wav", "COMET.wav", "console.wav"])
def test_safe_names(name):
    assert sounds.safe_file_name(name)


@pytest.mark.parametrize("name", [
    "", ".", "..", "../x.wav", "..\\x.wav", "a/b.wav", "a\\b.wav", "C:x.wav", "COM1.wav",
    "COM1 .wav", "COM\u00b9.wav", "LPT\u00b3.wav",
    "com1", "LPT9.wav", "nul.wav", "CON.x.wav", "x.wav.", "x.wav ", "a\x01.wav", "a?.wav"])
def test_unsafe_names(name):
    assert not sounds.safe_file_name(name)


def test_read_wav_takes_16_bit_mono_and_stereo(tmp_path):
    for channels in (1, 2):
        params, frames = sounds.read_wav(make_wav(tmp_path / f"{channels}.wav", channels=channels))
        assert params.nchannels == channels and frames


@pytest.mark.parametrize("build", [
    lambda p: make_wav(p, width=1),
    lambda p: make_wav(p, width=3),
    lambda p: make_wav(p, seconds=0),
    lambda p: make_wav(p, seconds=31, rate=8000),
    lambda p: (p.write_bytes(b"not a wav"), str(p))[1],
    lambda p: (p.write_bytes(b""), str(p))[1],
    lambda p: (p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt "), str(p))[1],
    lambda p: str(p) + ".missing",
])
def test_read_wav_refuses_what_it_cannot_play(tmp_path, build):
    with pytest.raises(SoundProblem):
        sounds.read_wav(build(tmp_path / "x.wav"))


def test_read_wav_says_what_is_wrong_with_a_rate_of_zero(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    data = bytearray(Path(path).read_bytes())
    data[24:28] = struct.pack("<I", 0)
    Path(path).write_bytes(data)
    with pytest.raises(SoundProblem):
        sounds.read_wav(path)


def test_read_wav_refuses_an_absurd_sample_rate(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    data = bytearray(Path(path).read_bytes())
    data[24:28] = struct.pack("<I", 0xFFFFFFFF)
    Path(path).write_bytes(data)
    with pytest.raises(SoundProblem):
        sounds.read_wav(path)


def test_read_wav_trims_a_file_cut_off_mid_frame(tmp_path):
    path = make_wav(tmp_path / "x.wav", channels=2)
    data = Path(path).read_bytes()
    Path(path).write_bytes(data[:-1])
    try:
        _params, frames = sounds.read_wav(path)
    except SoundProblem:
        return  # refused outright is fine too
    assert len(frames) % 4 == 0


def test_read_wav_refuses_a_huge_file_before_reading_it(tmp_path, monkeypatch):
    path = make_wav(tmp_path / "x.wav")
    monkeypatch.setattr(sounds, "MAX_BYTES", 10)
    with pytest.raises(SoundProblem, match="larger"):
        sounds.read_wav(path)


def test_scaled():
    raw = struct.pack("<4h", 1000, -1000, 32767, -32768)
    assert struct.unpack("<4h", sounds.scaled(raw, 0.5)) == (500, -500, 16383, -16384)
    assert sounds.scaled(raw, 1.0) == raw
    assert sounds.scaled(raw, 2.0) == raw
    assert set(struct.unpack("<4h", sounds.scaled(raw, 0.0))) == {0}
    assert set(struct.unpack("<4h", sounds.scaled(raw, -1.0))) == {0}


class FakeProcess:
    def __init__(self, code=0, runs=False):
        self.returncode = None
        self._code = code
        self._runs = runs
        self.terminated = False

    def poll(self):
        if self._runs and not self.terminated:
            return None
        self.returncode = 1 if self.terminated else self._code
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return self.poll()


def recording(process=None):
    calls = []

    def popen(command, **kwargs):
        calls.append(command)
        assert kwargs["stdin"] == subprocess.DEVNULL
        return process or FakeProcess()
    return popen, calls


def test_afplay_gets_the_volume(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    popen, calls = recording()
    sounds.AfplayOutput(popen).play(path, 0.8, threading.Event())
    assert calls == [["/usr/bin/afplay", "-v", "0.80", path]]


def test_a_command_output_is_killed_to_cut_off(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    process = FakeProcess(runs=True)
    popen, _ = recording(process)
    cancel = threading.Event()
    cancel.set()
    sounds.AfplayOutput(popen).play(path, 1.0, cancel)
    assert process.terminated


def test_a_command_that_fails_is_a_sound_problem(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    popen, _ = recording(FakeProcess(code=3))
    with pytest.raises(SoundProblem, match="exit 3"):
        sounds.AfplayOutput(popen).play(path, 1.0, threading.Event())


def test_a_command_output_checks_the_file_first(tmp_path):
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"junk")
    popen, calls = recording()
    with pytest.raises(SoundProblem):
        sounds.AfplayOutput(popen).play(str(bad), 1.0, threading.Event())
    assert calls == []


def linux(popen, *present):
    return sounds.LinuxOutput(popen, which=lambda name: name if name in present else None)


def test_linux_prefers_pipewire_then_pulse(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    popen, calls = recording()
    linux(popen, "pw-play", "paplay", "aplay").play(path, 0.5, threading.Event())
    linux(popen, "paplay", "aplay").play(path, 0.5, threading.Event())
    assert calls == [["pw-play", "--volume=0.50", path],
                     ["paplay", f"--volume={int(0.5 ** (1 / 3) * 65536)}", path]]


def test_pulseaudio_gets_the_same_loudness_on_its_cubic_scale(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    popen, calls = recording()
    linux(popen, "paplay").play(path, 0.125, threading.Event())
    assert calls == [["paplay", "--volume=32768", path]]


def test_linux_passes_over_a_player_whose_server_is_not_there(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    calls = []

    def popen(command, **kwargs):
        calls.append(command[0])
        return FakeProcess(code=1 if command[0] == "pw-play" else 0)
    output = linux(popen, "pw-play", "paplay")
    output.play(path, 1.0, threading.Event())
    output.play(path, 1.0, threading.Event())
    assert calls == ["pw-play", "paplay", "paplay"]  # what works goes first from then on


def test_linux_gives_no_player_up_for_good(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    broken = {"pw-play", "paplay"}
    calls = []

    def popen(command, **kwargs):
        calls.append(command[0])
        return FakeProcess(code=1 if command[0] in broken else 0)
    output = linux(popen, "pw-play", "paplay", "aplay")
    output.play(path, 1.0, threading.Event())
    broken.update({"aplay"})
    broken.discard("pw-play")  # a sound server back after a restart
    output.play(path, 1.0, threading.Event())
    assert calls[-1] == "pw-play"


def test_aplay_at_full_volume_needs_no_copy(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    popen, calls = recording()
    linux(popen, "aplay").play(path, 1.0, threading.Event())
    assert calls == [["aplay", "-q", path]]


def test_linux_says_so_when_the_last_player_fails(tmp_path):
    path = make_wav(tmp_path / "x.wav")
    popen, _ = recording(FakeProcess(code=1))
    with pytest.raises(SoundProblem):
        linux(popen, "pw-play").play(path, 1.0, threading.Event())


def test_a_player_that_hangs_is_killed_and_reported(tmp_path, monkeypatch):
    path = make_wav(tmp_path / "x.wav")
    process = FakeProcess(runs=True)
    popen, _ = recording(process)
    now = [100.0]
    monkeypatch.setattr(sounds.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(sounds.time, "sleep", lambda s: now.__setitem__(0, now[0] + 10))
    with pytest.raises(SoundProblem, match="didn't finish"):
        sounds.AfplayOutput(popen).play(path, 1.0, threading.Event())
    assert process.terminated


def test_linux_aplay_plays_a_scaled_copy_and_removes_it(tmp_path):
    path = make_wav(tmp_path / "x.wav", value=1000)
    seen = {}

    def popen(command, **kwargs):
        seen["command"] = command
        with wave.open(command[-1]) as w:
            seen["first"] = struct.unpack("<h", w.readframes(1))[0]
        return FakeProcess()
    linux(popen, "aplay").play(path, 0.5, threading.Event())
    assert seen["command"][:2] == ["aplay", "-q"] and seen["first"] == 500
    assert not os.path.exists(seen["command"][-1])


def test_linux_with_no_player_is_silent(tmp_path):
    popen, calls = recording()
    linux(popen).play(make_wav(tmp_path / "x.wav"), 1.0, threading.Event())
    assert calls == []


def test_quiet_now_answers_without_raising():
    assert sounds.quiet_now() in (True, False)


windows = pytest.mark.skipif(sys.platform != "win32", reason="waveOut is Windows'")


@windows
def test_wave_out_plays_silence_or_says_there_is_no_device(tmp_path):
    path = make_wav(tmp_path / "x.wav", seconds=0.05)
    try:
        sounds.WaveOutOutput().play(path, 0.5, threading.Event())
    except SoundProblem as exc:
        assert "No sound device" in str(exc)  # a CI machine


@windows
def test_wave_out_stops_when_cancelled(tmp_path):
    path = make_wav(tmp_path / "x.wav", seconds=3)
    output = sounds.WaveOutOutput()
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    try:
        output.play(path, 0.5, cancel)
    except SoundProblem:
        pytest.skip("no sound device")
    assert time.monotonic() - started < 1.5


@windows
def test_wave_out_refuses_a_bad_file(tmp_path):
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"junk")
    with pytest.raises(SoundProblem):
        sounds.WaveOutOutput().play(str(bad), 0.5, threading.Event())
