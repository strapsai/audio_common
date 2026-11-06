"""Offline recording generation; fake tools never open a speaker or ROS graph."""
import subprocess
import wave

import pytest
from robot_audio.generate_clip import generate_clip, main
from test_controller import rig


def test_render_only_has_no_player_and_publishes_valid_wav(rig, tmp_path):
    _, events, _, voices, binaries = rig
    # Use real SoX to normalize the fake Piper WAV without any audio device.
    output = generate_clip("It's a test; $(no-shell)", tmp_path/'recordings/test.wav', voices,
                           piper=binaries/'piper')
    with wave.open(str(output)) as stream:
        assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (48000, 2, 2)
    assert [event['tool'] for event in events()] == ['piper']
    assert events()[0]['text'] == "It's a test; $(no-shell)\n"
    assert not list(output.parent.glob('.piper-clip-*'))
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        generate_clip('replace', output, voices, piper=binaries/'piper')
    assert output.read_bytes() == original


def test_synthesis_failure_or_timeout_does_not_publish(rig, tmp_path):
    _, _, _, voices, binaries = rig
    output = tmp_path/'failed.wav'
    with pytest.raises(subprocess.TimeoutExpired):
        generate_clip('BLOCK', output, voices, piper=binaries/'piper', timeout=.1)
    assert not output.exists()
    broken = tmp_path/'broken-sox'
    broken.write_text('#!/bin/sh\necho conversion-failed >&2\nexit 7\n')
    broken.chmod(0o755)
    with pytest.raises(RuntimeError, match='conversion-failed'):
        generate_clip('Hello', output, voices, piper=binaries/'piper', converter=broken)
    assert not output.exists()
    assert not list(tmp_path.glob('.piper-clip-*'))


def test_invalid_names_text_and_output_are_rejected(rig, tmp_path):
    _, _, _, voices, binaries = rig
    for text, voice, output in [('', 'en_US-lessac-medium', 'a.wav'),
                                 ('hello', '../escape', 'a.wav'),
                                 ('hello', 'en_US-lessac-medium', 'a.mp3')]:
        with pytest.raises(ValueError):
            generate_clip(text, tmp_path/output, voices, voice=voice, piper=binaries/'piper')


def test_cli_utf8_file_and_missing_model(rig, tmp_path):
    _, events, _, voices, binaries = rig
    words = tmp_path/'words.txt'
    words.write_text('Please stop moving.\nMeasuring vitals.', encoding='utf-8')
    output = tmp_path/'message.wav'
    main(['--text-file', str(words), '--output', str(output), '--voices-dir', str(voices),
          '--piper', str(binaries/'piper')])
    assert output.is_file()
    assert events()[0]['text'] == words.read_text()+'\n'
    with pytest.raises(SystemExit) as error:
        main(['--text', 'Hi', '--output', str(tmp_path/'missing.wav'),
              '--voices-dir', str(voices), '--voice', 'missing'])
    assert error.value.code == 1
