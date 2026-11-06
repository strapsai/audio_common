"""Explicit, checksum-verified asset preparation. Never called by the audio node."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def download(url, destination, sha256):
    if destination.exists():
        if checksum(destination) == sha256:
            return
        raise ValueError(f'Existing asset has the wrong checksum; move it aside explicitly: {destination}')
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, suffix='.part') as stream:
        temporary = Path(stream.name)
        try:
            with urllib.request.urlopen(url, timeout=60) as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk: break
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
            if checksum(temporary) != sha256:
                raise ValueError(f'Download checksum mismatch: {destination.name}')
            # Atomic no-overwrite publication: concurrent preparation never replaces data.
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--voice', action='append', help='Repeat for multiple voices; default: Lessac and Ryan')
    options = parser.parse_args(args)
    manifest = json.loads(Path(__file__).with_name('voices.json').read_text())
    voices = options.voice or ['en_US-lessac-medium', 'en_US-ryan-medium']
    if any(voice not in manifest for voice in voices):
        parser.error('Supported pinned voices: ' + ', '.join(sorted(manifest)))
    options.destination.mkdir(parents=True, exist_ok=True)
    for voice in voices:
        for extension, info in manifest[voice].items():
            dest = options.destination / f'{voice}.{extension}'
            download(info['url'], dest, info['sha256'])
            print(f'VERIFIED {dest}', flush=True)


if __name__ == '__main__': main()
