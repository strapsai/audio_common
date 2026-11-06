import hashlib
from pathlib import Path
import pytest
from robot_audio.download_voices import download


def test_verified_download_is_atomic_and_idempotent(tmp_path):
    source=tmp_path/'source';source.write_bytes(b'voice model')
    dest=tmp_path/'model.onnx';sha=hashlib.sha256(source.read_bytes()).hexdigest()
    download(source.as_uri(),dest,sha)
    assert dest.read_bytes()==b'voice model'
    download('file:///unused-after-valid-cache',dest,sha)
    assert not list(tmp_path.glob('*.part'))


def test_corrupt_download_and_existing_model_are_not_published_or_overwritten(tmp_path):
    source=tmp_path/'source';source.write_bytes(b'corrupt')
    dest=tmp_path/'model.onnx'
    with pytest.raises(ValueError,match='checksum mismatch'):download(source.as_uri(),dest,'0'*64)
    assert not dest.exists() and not list(tmp_path.glob('*.part'))
    dest.write_bytes(b'operator model')
    with pytest.raises(ValueError,match='move it aside'):download(source.as_uri(),dest,'0'*64)
    assert dest.read_bytes()==b'operator model'
