# USB robot audio

Play local recordings once or in a loop, synthesize offline Piper speech, and stop
any active synthesis/conversion/playback through ROS services. No sound plays on
startup. Audio files stay on the robot; service requests carry clip names or text.

This uses the standalone Piper release already used by Starling's `teensy_speaker`
(`2023.11.14-2`), with a USB/ALSA backend instead of the Teensy serial protocol.
It does not depend on the Teensy hardware, firmware, or microSD card. Piper is
required: missing voices/runtime fail explicitly; there is no eSpeak fallback.
The original `audio_common` nodes and interfaces remain available separately.

## Install and prepare assets

Build `audio_common_msgs` and `robot_audio` in the robot workspace. The runtime
needs `aplay` (alsa-utils), SoX (including MP3 support if used), and the pinned
standalone Piper distribution at `/opt/piper/piper`. AirLab's companion image
change supplies these without installing Python ONNX packages into the ROS stack.

Models are deliberately outside the image/source repository. Before a field run:

```bash
mkdir -p "$AIRLAB_PATH/audio/clips"
python3 -m robot_audio.download_voices \
  --destination "$AIRLAB_PATH/weights/audio/voices"
```

This explicit preparation step downloads **en_US-lessac-medium** and
**en_US-ryan-medium**, their configurations and upstream model cards. Files use a
pinned Hugging Face revision and SHA-256 manifest. Matching existing assets are
reused; mismatching files are refused rather than overwritten. Partial downloads
are not published as finished assets. `--voice en_US-ryan-medium` selects one
voice instead of both. The running node never downloads files.

Additional compatible Piper `.onnx`/`.onnx.json` pairs may be installed manually
in the voice directory. Their filename stem is the service's voice ID; verify
new models with the pinned runtime before using them. See the upstream
[Piper CLI](https://github.com/rhasspy/piper) and
[voice repository](https://huggingface.co/rhasspy/piper-voices). Preserve/check the
model card supplied with each voice.

Put recordings into `$AIRLAB_PATH/audio/clips`. Supported formats are WAV, MP3,
FLAC and OGG, subject to installed SoX decoders. Relative subdirectories are
allowed. Absolute paths and symlinks escaping the asset directory are rejected.

```bash
ros2 launch robot_audio speaker.launch.py robot:=spot1 \
  clips_dir:="$AIRLAB_PATH/audio/clips" \
  voices_dir:="$AIRLAB_PATH/weights/audio/voices"
```

Both audited Spot speakers identify as Jieli `UACDemoV1.0` (`4c4a:4155`). Their
native playback is 48 kHz stereo S16_LE. The default explicit ALSA selector is
`plughw:CARD=UACDemoV10,DEV=0`, which avoids relying on card number 0 or selecting
HDMI by accident. SoX converts each job once to that format, then reuses the
converted file for looping. `gain` defaults to 0.7 and is configured at startup.
A disconnected/busy device fails the job visibly; it is not replaced with another
output device. A new request can use a reconnected device.

## Services

Replace `spot1` with `spot2` for the other robot. Native robot services live in
that robot's ROS domain. The optional proxy below exposes the same names in the
base-station domain.

```bash
ros2 service call /spot1/audio/list_clips audio_common_msgs/srv/ListAudio '{}'
ros2 service call /spot1/audio/play audio_common_msgs/srv/PlayAudio \
  "{clip: 'message.wav', loop: true, interrupt: true}"
ros2 service call /spot1/audio/say audio_common_msgs/srv/SayAudio \
  "{text: 'Medical help is on the way.', voice: 'en_US-ryan-medium', interrupt: true}"
ros2 service call /spot1/audio/stop std_srvs/srv/Trigger '{}'
ros2 service call /spot1/audio/status audio_common_msgs/srv/AudioStatus '{}'
```

- `play` and `say` return acceptance plus a generated `job_id`, not playback
  completion. `status` returns the current job, or most recently finished job,
  with `state`, `busy`, queue count and `last_error`; it is not a per-job history.
- `interrupt: true` is the default: cancel active work, discard queued requests,
  and replace it. Invalid requests are rejected before disturbing current audio.
- `interrupt: false` queues the request after current work (maximum 16 queued).
  A looping job holds the worker until stopped/interrupted.
- `say` accepts up to 4000 characters. An empty voice selects `default_voice`
  (Lessac by default). `loop: true` repeats the synthesized recording without
  rerunning Piper on every iteration.
- `stop` is idempotent. It clears pending work and terminates the owned process
  group, including Piper if synthesis is in progress. No unrelated processes are
  killed. Playback uses a 100 ms ALSA buffer; physical audible stop latency still
  needs robot acceptance testing.
- States include `idle`, `synthesizing`, `converting`, `playing`, `done`, `stopped`,
  and `failed`. Processing has a 60 s timeout; normal playback can last longer.

Each job uses private temporary files. Text/paths are passed as argument arrays
or stdin, never shell interpolation. One worker owns the device; service
callbacks do not wait for the duration of a sentence or recording. The queue is
in memory and does not survive node restart. There are no command/status topics
in this version beyond normal ROS logs/parameters.

## Base-station services

The stock domain_bridge YAML executable does not register these custom service
types. `speaker_service_bridge` uses explicit typed proxies and separate ROS
contexts for Spot1 (domain 10), Spot2 (20) and the current base-station domain:

```bash
ros2 run robot_audio speaker_service_bridge
```

Build the same `audio_common_msgs`/`robot_audio` version on the base station too.
`spot1_domain`, `spot2_domain` and `request_timeout_sec` are configurable startup
parameters. All three domains must be distinct. Services use reliable default
ROS service QoS. No raw audio traverses the radio bridge.

Check `success` on every response, including status: an offline robot is reported
as unavailable, not idle. A proxy timeout reports **unknown command outcome**;
query status rather than blindly replaying a play/say request. The proxy does not
retry commands automatically. Stop/status have separate executor capacity so a
single delayed response does not block all controls.

## Tests

Run in a network-none container with no sound devices:

```bash
python3 -m pytest -q robot_audio/test
```

Tests use fake child processes to check looping, queue limits, validation,
interruption during synthesis, error recovery, and real three-domain ROS service
forwarding. Real Piper/SoX output can be validated separately by substituting a
file-writing player; no physical sound is necessary. Fast DDS was used for the
offline ROS tests; field Connext and audible speaker acceptance are separate.
