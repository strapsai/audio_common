"""Generate a portable WAV offline; no ROS graph, network or audio device is used."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import wave


def generate_clip(text, output, voices_dir, *, voice='en_US-lessac-medium',
                  piper='/opt/piper/piper', converter='sox', timeout=60.):
    if not text.strip() or len(text) > 4000 or '\x00' in text:
        raise ValueError('Text must contain 1–4000 characters, without NUL')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', voice):
        raise ValueError('Invalid voice name')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Timeout must be positive and finite')
    root = Path(voices_dir).resolve(strict=True)
    model = (root / (voice + '.onnx')).resolve(strict=True)
    config = (root / (voice + '.onnx.json')).resolve(strict=True)
    for path in (model, config):
        if not path.is_relative_to(root) or not path.is_file() or not path.stat().st_size:
            raise ValueError('Voice model/config must be nonempty files inside voices_dir')
    rate = json.loads(config.read_text())['audio']['sample_rate']
    if type(rate) is not int or rate <= 0:
        raise ValueError('Invalid voice sample rate')
    output = Path(output).expanduser().absolute()
    if output.suffix.lower() != '.wav':
        raise ValueError('Output must have a .wav extension')
    if output.exists() or output.is_symlink():
        raise FileExistsError(f'Refusing to replace existing clip: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.piper-clip-', dir=output.parent) as temporary:
        raw, normalized = (Path(temporary) / name for name in ('raw.wav', 'normalized.wav'))
        commands = [
            ([str(piper), '--model', str(model), '--config', str(config),
              '--output_file', str(raw), '--quiet'], text + '\n'),
            ([str(converter), str(raw), '-r', '48000', '-c', '2', '-b', '16',
              '-e', 'signed-integer', str(normalized)], None),
        ]
        for command, input_text in commands:
            result = subprocess.run(command, input=input_text, text=True, capture_output=True,
                                    timeout=timeout, check=False)
            if result.returncode:
                raise RuntimeError(f'{Path(command[0]).name} failed ({result.returncode}): '
                                   f'{result.stderr[-2000:]}')
        with wave.open(str(normalized)) as stream:
            if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (48000, 2, 2):
                raise ValueError('Generated clip must be 48 kHz stereo 16-bit PCM')
            if stream.getnframes() == 0:
                raise ValueError('Generated clip is empty')
        # Atomic no-overwrite publication, including concurrent generation attempts.
        os.link(normalized, output)
    return output


def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--text')
    source.add_argument('--text-file', type=Path, help='UTF-8 text file')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--voices-dir', type=Path, required=True)
    parser.add_argument('--voice', default='en_US-lessac-medium')
    parser.add_argument('--piper', default='/opt/piper/piper')
    parser.add_argument('--converter', default='sox')
    parser.add_argument('--timeout', type=float, default=60.)
    options = parser.parse_args(args)
    try:
        text = options.text if options.text is not None else options.text_file.read_text(encoding='utf-8')
        output = generate_clip(text, options.output, options.voices_dir, voice=options.voice,
                               piper=options.piper, converter=options.converter, timeout=options.timeout)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Audio generation failed: {error}\n')
    print(f'Generated {output}')


if __name__ == '__main__':
    main()
