# Jarvis

A local "Hey Jarvis" voice assistant for this laptop. Speech recognition and the voice run on the
machine itself; only the text of what you say is sent to Claude.

| Part | What it uses |
|---|---|
| Speech-to-text | faster-whisper `large-v3-turbo` on the RTX 3060 (CUDA, float16) |
| Text-to-speech | Piper, `en_GB-alan-medium` voice |
| Brain | Claude via the `anthropic` SDK (`claude-haiku-5-5` by default) |
| Microphone | Logitech Brio 101 webcam mic (the built-in mic is broken) |

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env   # then put your ANTHROPIC_API_KEY in .env
```

The Piper voice goes in `models/piper/` (`en_GB-alan-medium.onnx` plus its `.onnx.json`).
Whisper models download to `~/.cache/huggingface` on first use.

## Checks

```
.venv/bin/python -m tests.claude_hello                     # Claude replies, prints token usage
.venv/bin/python -m jarvis.tts "Good evening, sir."        # speaks aloud
.venv/bin/python -m jarvis.stt 5                           # records 5 s, prints the transcript
.venv/bin/python -m tests.bench_stt                        # compares Whisper models (add @cpu to run one on CPU)
.venv/bin/python -m jarvis.wakeword                        # prints WAKE + what you said, Ctrl+C to stop
```

## Settings (`.env`)

| Setting | Default | Notes |
|---|---|---|
| `CLAUDE_MODEL` | `claude-haiku-5-5` | Any current Claude model id |
| `MAX_TOKENS` | `300` | Caps reply length and cost |
| `WHISPER_MODEL` | `large-v3-turbo` | `tiny.en` / `base.en` / `small.en` for CPU |
| `WHISPER_DEVICE` | `cuda` | `cpu` uses int8 and `WHISPER_THREADS` |
| `PIPER_VOICE` | `models/piper/en_GB-alan-medium.onnx` | |
| `TTS_LENGTH_SCALE`, `TTS_VOLUME` | `1.0` | Speech speed (>1 is slower) and loudness |
| `WAKE_THRESHOLD` | `0.5` | Raise if it wakes by itself, lower if it misses you |
| `SILENCE_SECONDS` | `0.8` | Pause that ends what you're saying |
| `INPUT_DEVICE`, `OUTPUT_DEVICE` | system default | Index or name substring; list with `python -m sounddevice` |

## Measured on this machine

- Whisper `large-v3-turbo` on GPU: 0.15 s per second of audio (CPU `base.en`: 0.24 s, and less accurate).
- Claude `claude-haiku-5-5`, five-word reply: about 0.9 s.

## Status

Done: TTS, STT (GPU), Claude connection, wake word + end-of-speech detection. Next: the
full pipeline, tools, and a systemd service.

## Notes

- openWakeWord 0.6.0 is installed with `--no-deps`: it lists `tflite-runtime`, which has no
  Python 3.12 build, but Jarvis runs it on ONNX instead.
- `av` is pinned to 18.1.0: av 19 breaks faster-whisper 1.2.1.
- The NVIDIA driver is the DKMS `nvidia-driver-595-open`, signed with the enrolled MOK key, so it
  works with Secure Boot on. cuBLAS/cuDNN come from pip and are loaded by `jarvis/stt.py`.
