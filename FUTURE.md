# FUTURE.md — audio_common

> ROS 2 monorepo for audio capture, playback, music streaming, and text-to-speech using PortAudio.
> Last updated: 2026-03-04

## Purpose
Provides audio I/O capabilities for ROS 2 robots: microphone capture, speaker playback, music streaming from files, and TTS (text-to-speech) via an action server. Third-party package by Miguel Ángel González Santamarta (MIT License). Contains two sub-packages: `audio_common` (nodes) and `audio_common_msgs` (message/service/action definitions).

## Nodes

| Node | Executable | Class/File | Purpose |
|------|-----------|-----------|---------|
| `audio_capturer` | `audio_capturer_main.cpp` | `AudioCapturerNode` | Captures microphone audio via PortAudio, publishes as `AudioStamped` |
| `audio_player` | `audio_player_main.cpp` | `AudioPlayerNode` | Plays back `AudioStamped` messages via PortAudio speaker |
| `music` | `music_main.cpp` | `MusicNode` | Streams audio from WAV files; supports play/pause/stop/resume via services |
| `tts` | `tts_main.cpp` | `TtsNode` | Text-to-speech action server; converts text to audio and publishes to player |

## Design Pattern

**Capturer:** Synchronous PortAudio stream → templated `read_data<T>()` based on format → publishes `AudioStamped`.

**Player:** Subscribes to `AudioStamped`; maintains `unordered_map<string, PaStream*>` keyed by stream identifier to support multiple concurrent audio streams. Timer callback cleans up closed streams.

**Music:** Runs a background `publish_thread_` for non-blocking file streaming. Uses `atomic<bool>` flags + `condition_variable` for pause/resume without busy-waiting.

**TTS:** Action server pattern — accepts `TTS` action goals, converts text to audio using a TTS engine, publishes `AudioStamped` to player, and reports completion. Also accepts `std_msgs/String` on subscription for fire-and-forget TTS.

```cpp
// Audio capture → publish pattern:
audio_pub_->publish(stamped_audio);

// Music pause/resume uses condition_variable:
pause_cv_.wait(lock, [this] { return !pause_music_ || stop_music_; });
```

## ROS Interfaces

### Publishers
| Topic | Type | Node | Description |
|-------|------|------|-------------|
| `audio` | `audio_common_msgs/AudioStamped` | audio_capturer | Raw captured audio chunks |
| `audio` | `audio_common_msgs/AudioStamped` | tts, music | Audio to be played (feeds into audio_player) |
| (status) | `std_msgs/Bool` | audio_player | Playback status |

### Subscribers
| Topic | Type | Node | Description |
|-------|------|------|-------------|
| `audio` | `audio_common_msgs/AudioStamped` | audio_player | Audio chunks to play |
| `say` | `std_msgs/String` | tts | Simple text input for TTS without action overhead |

### Services
| Service | Type | Node | Description |
|---------|------|------|-------------|
| `music_play` | `audio_common_msgs/MusicPlay` | music | Play a music file (path in request) |
| `music_stop` | `std_srvs/Trigger` | music | Stop music playback |
| `music_pause` | `std_srvs/Trigger` | music | Pause music |
| `music_resume` | `std_srvs/Trigger` | music | Resume paused music |

### Actions
| Action | Type | Node | Description |
|--------|------|------|-------------|
| `tts` | `audio_common_msgs/TTS` | tts | Goal: text string → Result: done flag; streams synthesized audio |

### Parameters (`AudioCapturerNode`)
| Parameter | Default | Description |
|-----------|---------|-------------|
| `format` | (int) | PortAudio sample format |
| `channels` | (int) | Number of audio channels |
| `rate` | (int) | Sample rate (Hz) |
| `chunk` | (int) | Samples per read |
| `frame_id` | (string) | TF frame id for stamped messages |

## Launch Files
None included in repo. Run nodes directly or wrap in a launch file.

## Config Files
None.

## Key Dependencies

| Dependency | Usage |
|-----------|-------|
| `portaudio` (libportaudio2) | Hardware audio I/O for capture and playback |
| `rclcpp_action` | TTS action server |
| `audio_common_msgs` | Custom message/service/action types (sibling sub-package) |
| TTS backend | External TTS engine required for `tts_node` (not bundled — check implementation in `.cpp`) |

## Build Notes
- Two sub-packages: `audio_common/` and `audio_common_msgs/`. Build order: msgs first.
- Requires `libportaudio-dev` on the host.
- `audio_common_msgs` must be in the same workspace as `audio_common`.
- The TTS engine used internally by `tts_node.cpp` is not clear from headers alone — inspect `tts_node.cpp` for the actual synthesis library (e.g., espeak, pyttsx, or external service).

## Known Issues

| Severity | Description |
|----------|-------------|
| Medium | `AudioPlayerNode` uses `unordered_map<string, PaStream*>` — the key used for stream identification is not obvious from headers; check `audio_player_node.cpp` to understand stream demultiplexing |
| Low | No launch files provided — integration requires manual node composition |
| Low | `TtsNode::goal_lock_` mutex protects `goal_handle_` but action preemption logic is not visible in headers |
