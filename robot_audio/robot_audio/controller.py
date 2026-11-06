"""One cancellable worker owns synthesis, conversion and USB playback.

Service callbacks only enqueue/cancel; subprocesses use argument arrays and their
own process groups. No shell, network requests or robot-motion interface exists.
"""
from collections import deque
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
from uuid import uuid4


class Cancelled(Exception):
    pass


@dataclass
class Job:
    kind: str
    source: str
    voice: str = ''
    loop: bool = False
    id: str = field(default_factory=lambda: str(uuid4()))
    cancel: threading.Event = field(default_factory=threading.Event)


class AudioController:
    def __init__(self, clips_dir, voices_dir, *, device='plughw:CARD=UACDemoV10,DEV=0',
                 piper='/opt/piper/piper', default_voice='en_US-lessac-medium',
                 player='aplay', converter='sox', gain=0.7, timeout=60., max_queue=16):
        if not str(clips_dir).strip() or not str(voices_dir).strip():
            raise ValueError('clips_dir and voices_dir must be configured explicitly')
        self.clips_dir = Path(clips_dir).resolve(strict=True)
        self.voices_dir = Path(voices_dir).resolve(strict=True)
        self.builtin_clips_dir = Path(__file__).resolve().parent / 'clips'
        if not self.clips_dir.is_dir() or not self.voices_dir.is_dir():
            raise ValueError('clips_dir and voices_dir must be directories')
        if not device or device in ('default', 'sysdefault'):
            raise ValueError('Select an explicit USB ALSA device; no default-output fallback')
        self.device, self.default_voice = device, default_voice
        self.piper, self.player, self.converter = [self._executable(x) for x in (piper, player, converter)]
        if not math.isfinite(gain) or not 0 < gain <= 1:
            raise ValueError('gain must be in (0, 1]')
        if not math.isfinite(timeout) or timeout <= 0 or max_queue < 1:
            raise ValueError('Invalid processing timeout or queue limit')
        self.gain, self.timeout, self.max_queue = gain, timeout, max_queue
        self._voice(default_voice)  # Piper is required: never silently fall back.
        self._condition = threading.Condition(threading.RLock())
        self._queue = deque()
        self._active = None
        self._process = None
        self._closing = False
        self._state = 'idle'
        self._last_error = ''
        self._last_job = None
        self._worker = threading.Thread(target=self._work, name='usb-audio-worker', daemon=True)
        self._worker.start()

    @staticmethod
    def _executable(binary):
        result = shutil.which(str(binary))
        if not result:
            raise ValueError(f'Required executable missing: {binary}')
        return result

    @staticmethod
    def _inside(root, name):
        if not name or Path(name).is_absolute():
            raise ValueError('Use a relative clip/voice name')
        path = (root / name).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Requested asset is outside its configured directory')
        return path

    def _clip(self, name):
        try:
            path = self._inside(self.clips_dir, name)
        except FileNotFoundError:
            path = self._inside(self.builtin_clips_dir, name)
        if path.suffix.lower() not in ('.wav', '.mp3', '.flac', '.ogg'):
            raise ValueError('Supported clips: WAV, MP3, FLAC, OGG')
        return path

    def _voice(self, name):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Invalid voice name')
        model = self._inside(self.voices_dir, name + '.onnx')
        config = self._inside(self.voices_dir, name + '.onnx.json')
        if model.stat().st_size == 0:
            raise ValueError(f'Empty Piper model: {name}')
        with config.open() as stream:
            metadata = json.load(stream)
        rate = metadata.get('audio', {}).get('sample_rate')
        if type(rate) is not int or rate <= 0:
            raise ValueError(f'Invalid Piper voice config: {name}')
        return model, config

    def list_assets(self):
        clips = []
        for root in (self.clips_dir, self.builtin_clips_dir):
            for path in root.rglob('*'):
                if path.suffix.lower() in ('.wav', '.mp3', '.flac', '.ogg'):
                    try:
                        name = str(path.relative_to(root))
                        self._clip(name)
                        clips.append(name)
                    except (ValueError, OSError):
                        continue
        voices = []
        for path in self.voices_dir.glob('*.onnx'):
            try:
                self._voice(path.stem)
                voices.append(path.stem)
            except (ValueError, OSError):
                continue
        return sorted(set(clips)), sorted(voices)

    def play(self, clip, loop=False, interrupt=True):
        self._clip(clip)
        return self._submit(Job('play', clip, loop=loop), interrupt)

    def say(self, text, voice='', loop=False, interrupt=True):
        if not text.strip() or len(text) > 4000 or '\x00' in text:
            raise ValueError('Text must contain 1–4000 characters, without NUL')
        voice = voice or self.default_voice
        self._voice(voice)
        return self._submit(Job('say', text, voice, loop), interrupt)

    def _submit(self, job, interrupt):
        process = None
        with self._condition:
            if self._closing:
                raise RuntimeError('Audio controller is shutting down')
            if interrupt:
                self._queue.clear()
                if self._active:
                    self._active.cancel.set()
                    process = self._process
            elif len(self._queue) >= self.max_queue:
                raise ValueError('Audio request queue is full')
            self._queue.append(job)
            self._condition.notify_all()
        self._terminate(process)
        return job.id

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            pass
        # Kill remaining descendants even if the immediate child already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Reap after SIGKILL as well; timeout paths have no communicate() waiter.
        try:
            process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            pass

    def stop(self):
        with self._condition:
            self._queue.clear()
            if self._active:
                self._active.cancel.set()
            process = self._process
            self._condition.notify_all()
        self._terminate(process)

    def close(self):
        with self._condition:
            self._closing = True
        self.stop()
        self._worker.join(timeout=2.)
        if self._worker.is_alive():
            raise RuntimeError('Audio worker did not stop')

    def status(self):
        with self._condition:
            job = self._active or self._last_job
            return dict(busy=self._active is not None, state=self._state,
                        job_id=job.id if job else '', kind=job.kind if job else '',
                        source=job.source if job else '', voice=job.voice if job else '',
                        loop=job.loop if job else False, queued=len(self._queue),
                        last_error=self._last_error)

    def _run_process(self, job, command, *, text=None, bounded=True):
        with tempfile.TemporaryFile() as errors:
            with self._condition:
                if job.cancel.is_set() or self._closing:
                    raise Cancelled()
                process = subprocess.Popen(command, stdin=subprocess.PIPE if text is not None else subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True)
                self._process = process
            try:
                process.communicate(input=text.encode('utf-8') if text is not None else None,
                                    timeout=self.timeout if bounded else None)
                if job.cancel.is_set():
                    raise Cancelled()
                if process.returncode:
                    errors.seek(0)
                    raise RuntimeError(errors.read(4096).decode(errors='replace').strip()
                                       or f'{Path(command[0]).name} exited {process.returncode}')
            except subprocess.TimeoutExpired:
                self._terminate(process)
                raise RuntimeError(f'{Path(command[0]).name} timed out')
            finally:
                with self._condition:
                    if self._process is process:
                        self._process = None

    def _phase(self, job, state):
        with self._condition:
            if job.cancel.is_set():
                raise Cancelled()
            self._state = state

    def _run(self, job):
        with tempfile.TemporaryDirectory(prefix='robot-audio-') as directory:
            root = Path(directory)
            if job.kind == 'say':
                model, config = self._voice(job.voice)
                source = root / 'speech.wav'
                self._phase(job, 'synthesizing')
                self._run_process(job, [self.piper, '--model', str(model), '--config', str(config),
                                       '--output_file', str(source), '--quiet'], text=job.source + '\n')
            else:
                source = self._clip(job.source)
            self._phase(job, 'converting')
            output = root / 'playback.wav'
            # Hardware requires 48 kHz stereo S16_LE. Reuse one converted WAV for loops.
            self._run_process(job, [self.converter, str(source), '-r', '48000', '-c', '2',
                                   '-b', '16', '-e', 'signed-integer', str(output),
                                   'vol', str(self.gain)])
            while not job.cancel.is_set():
                self._phase(job, 'playing')
                self._run_process(job, [self.player, '-q', '-D', self.device,
                                       '--buffer-time=100000', '--period-time=20000', str(output)], bounded=False)
                if not job.loop:
                    return
                # Interruptible yield prevents busy-spinning on empty/very short files.
                if job.cancel.wait(.01):
                    break
            raise Cancelled()

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or bool(self._queue))
                if self._closing:
                    return
                job = self._queue.popleft()
                self._active = job
                self._last_error = ''
            state, error = 'done', ''
            try:
                self._run(job)
            except Cancelled:
                state = 'stopped'
            except Exception as exc:
                state, error = 'failed', str(exc)
            with self._condition:
                self._active = None
                self._last_job = job
                self._state = state
                self._last_error = error
