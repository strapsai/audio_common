"""Pure input tests; no ROS graph or playback."""
import json
from pathlib import Path
import hashlib
import wave

import pytest
from robot_audio.hotkey import AudioHotkey


def send(key, t, lt=False, x=False, rt=False, extra=False, age=0):
    axes = [0., 0., -1. if lt else 0., 0., 0., -1. if rt else 0., 0., 0.]
    buttons = [0]*11
    buttons[2] = int(x)
    buttons[0] = int(extra)
    return key.observe(axes, buttons, t, t+age, t)


def test_startup_held_and_press_release_latch():
    key = AudioHotkey()
    assert not send(key, 10, lt=True, x=True)
    assert not send(key, 10.1)
    assert send(key, 10.2, lt=True, x=True)
    assert not send(key, 10.3, lt=True, x=True)
    assert not send(key, 10.4, x=True)
    assert not send(key, 10.5, lt=True, x=True)
    assert not send(key, 10.6, lt=True)
    assert send(key, 10.7, lt=True, x=True)


@pytest.mark.parametrize('kwargs', [dict(rt=True), dict(extra=True), dict(age=1), dict(age=-1)])
def test_unrouted_conflicting_or_stale_input_is_rejected(kwargs):
    key = AudioHotkey()
    send(key, 10)
    assert not send(key, 10.1, lt=True, x=True, **kwargs)


def test_gap_requires_release_and_stale_or_duplicate_cannot_replay():
    key = AudioHotkey()
    send(key, 10)
    assert not send(key, 12, lt=True, x=True)
    assert not send(key, 12.1)
    assert send(key, 12.2, lt=True, x=True)
    assert not send(key, 12.2, lt=True, x=True)
    assert not send(key, 12.3, lt=True)
    assert not send(key, 12.4, lt=True, x=True)
    send(key, 12.5)
    assert send(key, 12.6, lt=True, x=True)


def test_malformed_messages_disarm():
    key = AudioHotkey()
    for axes, buttons in [([], []), ([float('nan')]*8, [0]*11), ([0.]*8, [2]*11)]:
        send(key, 10)
        assert not key.observe(axes, buttons, 10.1, 10.1, 10.1)
        assert not key.ready


def test_packaged_announcement_matches_provenance():
    root = Path(__file__).parents[1]/'robot_audio/clips'
    metadata = json.loads((root/'vitals_measurement.json').read_text())
    assert metadata['text'] == 'Please stop moving and look at me. Measuring vitals.'
    wav = root/'vitals_measurement.wav'
    assert hashlib.sha256(wav.read_bytes()).hexdigest() == metadata['wav_sha256']
    with wave.open(str(wav)) as stream:
        assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (48000, 2, 2)
        assert 2 < stream.getnframes()/stream.getframerate() < 10
