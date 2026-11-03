#!/usr/bin/env bash
# Fresh-machine install of Jarvis on Linux: the Python environment, pinned requirements, model
# downloads, echo cancellation for the microphone, and the systemd user units (jarvis and
# jarvis-panel). Safe to run again: it only does what's missing, never overwrites .env, an
# existing unit file or an existing echo cancel config, and never restarts or stops anything.
# Anything that needs sudo is printed at the end, not run.
#
#   deploy/install.sh [--skip-python] [--skip-models] [--with-browser] [--no-systemd] [--help]
#
#   --skip-python   leave the .venv and its packages alone
#   --skip-models   don't download the voice, wake word, "stop" listener or Whisper model
#   --with-browser  also install Chromium for the OnQ / Smith Learning sign-in (about 150 MB)
#   --no-systemd    write the unit files but don't talk to systemd (containers, testing)
#
# The echo cancel config goes in $XDG_CONFIG_HOME/pipewire/pipewire.conf.d and the units in
# $XDG_CONFIG_HOME/systemd/user (default ~/.config), so HOME can be pointed at a scratch folder.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}
PIPEWIRE_DIR=$CONFIG/pipewire/pipewire.conf.d
UNIT_DIR=$CONFIG/systemd/user
PIPER_BASE=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium
VOSK_URL=https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip

SKIP_PYTHON=0 SKIP_MODELS=0 WITH_BROWSER=0 NO_SYSTEMD=0
for arg in "$@"; do
    case $arg in
        --skip-python) SKIP_PYTHON=1 ;;
        --skip-models) SKIP_MODELS=1 ;;
        --with-browser) WITH_BROWSER=1 ;;
        --no-systemd) NO_SYSTEMD=1 ;;
        -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

DONE=() KEPT=() SUDO=() NEXT=()
say() { printf '\n== %s\n' "$*"; }
did() { DONE+=("$*"); echo "  done: $*"; }
kept() { KEPT+=("$*"); echo "  already there: $*"; }
need_sudo() { SUDO+=("$*"); echo "  needs sudo (listed at the end): $*"; }
have() { command -v "$1" >/dev/null 2>&1; }

case $ROOT in *[[:space:]]*) echo "The Jarvis folder's path has a space in it ($ROOT); move it somewhere without one." >&2; exit 1 ;; esac
cd "$ROOT"

# ---- 1. what the machine needs ---------------------------------------------------------------
say "Checking this machine"
[ "$(uname -s)" = Linux ] || { echo "Jarvis needs Linux." >&2; exit 1; }
have python3 || { echo "python3 isn't installed." >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' ||
    { echo "Python 3.11 or newer is needed (Jarvis is developed on 3.12); this is $(python3 -V)." >&2; exit 1; }
python3 -c 'import venv, ensurepip' 2>/dev/null || need_sudo "sudo apt install python3-venv   # the venv module is missing"
have curl || need_sudo "sudo apt install curl   # needed to download models"

if have apt-get; then
    PACKAGES=()
    libs=$(ldconfig -p 2>/dev/null || true)   # not piped into grep -q: that can SIGPIPE ldconfig and trip pipefail
    [[ $libs == *libportaudio* ]] || PACKAGES+=(libportaudio2)   # sounddevice needs it
    have pactl || PACKAGES+=(pipewire-pulse pulseaudio-utils)
    [ -n "$(find /usr/lib /usr/lib64 -name 'libspa-aec-webrtc.so' 2>/dev/null | head -1)" ] || PACKAGES+=(pipewire-audio libspa-0.2-modules)
    [ ${#PACKAGES[@]} -eq 0 ] || need_sudo "sudo apt install ${PACKAGES[*]}"
else
    echo "  Not an apt system: make sure PortAudio, PipeWire (with its echo-cancel module) and pactl are installed."
fi
if ! have nvidia-smi; then
    echo "  No NVIDIA driver found (nvidia-smi). Jarvis listens on the GPU by default: install NVIDIA's driver,"
    echo "  or after setup set 'Whisper device' to cpu and 'Whisper model' to small.en in the panel's Settings."
fi

# ---- 2. Python environment ---------------------------------------------------------------------
if [ $SKIP_PYTHON = 1 ]; then
    say "Python environment: skipped"
else
    say "Python environment"
    if [ -x .venv/bin/python ]; then
        kept ".venv"
    else
        python3 -m venv .venv && did "created .venv"
    fi
    .venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt && did "pinned requirements installed"
    # --no-deps, as in the README: it lists tflite-runtime, which has no Python 3.12 build, and Jarvis runs it on ONNX
    if [ "$(.venv/bin/pip show openwakeword 2>/dev/null | sed -n 's/^Version: //p')" = 0.6.0 ]; then
        kept "openwakeword 0.6.0"
    else
        .venv/bin/pip install --quiet --disable-pip-version-check --no-deps openwakeword==0.6.0 && did "openwakeword 0.6.0 installed"
    fi
    if [ $WITH_BROWSER = 1 ]; then
        .venv/bin/python -m playwright install chromium && did "Chromium installed for the OnQ sign-in"
    else
        NEXT+=("For OnQ / Smith Learning: .venv/bin/python -m playwright install chromium   (or re-run this script with --with-browser)")
    fi
fi

# ---- 3. settings file --------------------------------------------------------------------------
say "Settings file"
if [ -e .env ]; then
    kept ".env (not touched)"
else
    cp .env.example .env && chmod 600 .env && did "created .env from .env.example (mode 600)"
fi

# ---- 4. models ---------------------------------------------------------------------------------
download() {  # download URL DESTINATION: to a temp name first, so a failed download leaves nothing half-written
    curl --fail --location --silent --show-error --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"
}
if [ $SKIP_MODELS = 1 ]; then
    say "Models: skipped"
else
    say "Models (this can take a few minutes the first time)"
    mkdir -p models/piper
    voice=models/piper/en_GB-alan-medium
    if [ -e $voice.onnx ] && [ -e $voice.onnx.json ]; then
        kept "voice (Piper en_GB-alan-medium)"
    else
        download "$PIPER_BASE/en_GB-alan-medium.onnx.json" $voice.onnx.json
        download "$PIPER_BASE/en_GB-alan-medium.onnx" $voice.onnx
        did "voice downloaded (Piper en_GB-alan-medium)"
    fi
    if [ -e models/vosk-model-small-en-us-0.15/am/final.mdl ]; then
        kept "stop-word listener (Vosk)"
    else
        scratch=$(mktemp -d)
        download "$VOSK_URL" "$scratch/vosk.zip"
        python3 -m zipfile -e "$scratch/vosk.zip" models/
        rm -rf "$scratch"
        did "stop-word listener downloaded (Vosk)"
    fi
    if [ $SKIP_PYTHON = 1 ] && [ ! -x .venv/bin/python ]; then
        echo "  No .venv, so the wake word and Whisper downloads are skipped."
    else
        .venv/bin/python -c 'from openwakeword.utils import download_models; download_models(["hey_jarvis"])' >/dev/null 2>&1 &&
            did "wake word (Hey Jarvis) is in place" || echo "  Couldn't fetch the wake word: check the internet connection and run this again."
        whisper=$(.venv/bin/python -c 'from jarvis.settings import env_values; print(env_values().get("WHISPER_MODEL") or "large-v3-turbo")')
        .venv/bin/python - "$whisper" <<'PY' && did "Whisper model $whisper is in place" || echo "  Couldn't fetch the Whisper model: check the internet connection and run this again."
import sys
from faster_whisper import download_model
try:
    download_model(sys.argv[1], local_files_only=True)
except Exception:
    download_model(sys.argv[1])
PY
    fi
fi

# ---- 5. echo cancellation ----------------------------------------------------------------------
say "Echo cancellation (so Jarvis hears \"stop\" over its own voice)"
ECHO_CONF=$PIPEWIRE_DIR/60-jarvis-echo-cancel.conf
if [ -e "$ECHO_CONF" ]; then
    kept "$ECHO_CONF"
else
    mic=$(pactl get-default-source 2>/dev/null || true)
    speaker=$(pactl get-default-sink 2>/dev/null || true)
    case $mic in ""|echo-cancel-*|*.monitor) mic="" ;; esac
    case $speaker in ""|echo-cancel-*) speaker="" ;; esac
    mkdir -p "$PIPEWIRE_DIR"
    {
        echo '# Echo cancellation for Jarvis: the microphone with the speakers'"'"' sound removed, so Jarvis can'
        echo '# hear "stop" while it is talking. Written by deploy/install.sh. The two target.object lines'
        echo '# pin the real microphone and speaker; without them PipeWire uses its defaults.'
        echo 'context.modules = ['
        echo '  { name = libpipewire-module-echo-cancel'
        echo '    args = {'
        echo '      library.name = aec/libspa-aec-webrtc'
        echo '      aec.args = {'
        echo '        webrtc.gain_control = false'
        echo '        webrtc.extended_filter = true'
        echo '        webrtc.noise_suppression = false'
        echo '      }'
        echo '      capture.props = {'
        echo '        node.name = "echo-cancel-capture"'
        [ -z "$mic" ] || echo "        target.object = \"$mic\""
        echo '        node.passive = true'
        echo '      }'
        echo '      source.props = {'
        echo '        node.name = "echo-cancel-source"'
        echo '        node.description = "Mic (echo cancelled)"'
        echo '      }'
        echo '      sink.props = {'
        echo '        node.name = "echo-cancel-sink"'
        echo '        node.description = "Speakers (echo cancelled)"'
        echo '      }'
        echo '      playback.props = {'
        echo '        node.name = "echo-cancel-playback"'
        [ -z "$speaker" ] || echo "        target.object = \"$speaker\""
        echo '        node.passive = true'
        echo '      }'
        echo '    }'
        echo '  }'
        echo ']'
    } > "$ECHO_CONF"
    did "wrote $ECHO_CONF"
    NEXT+=("Restart the sound system once so it loads the echo cancel config (this briefly interrupts any audio):  systemctl --user restart pipewire pipewire-pulse wireplumber")
    NEXT+=("Then make the echo-cancelled devices the default:  pactl set-default-source echo-cancel-source  and  pactl set-default-sink echo-cancel-sink")
fi

# ---- 6. systemd user units ---------------------------------------------------------------------
say "systemd user units"
mkdir -p "$UNIT_DIR"
install_unit() {  # install_unit NAME [FALLBACK-TEXT]: from deploy/NAME.service with this folder's path, never over an existing one
    local name=$1 dest=$UNIT_DIR/$1.service
    if [ -e "$dest" ] || [ -L "$dest" ]; then
        kept "$dest"
    elif [ -e "deploy/$name.service" ]; then
        sed "s|%h/jarvis|$ROOT|g" "deploy/$name.service" > "$dest" && did "installed $dest"
    elif [ -n "${2:-}" ]; then
        printf '%s\n' "$2" > "$dest" && did "installed $dest"
    else
        echo "  deploy/$name.service is missing: skipped $name"
    fi
}
install_unit jarvis
install_unit jarvis-panel "[Unit]
Description=Jarvis Panel (setup, settings and status web app)

[Service]
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m jarvis.panel
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target"

if [ $NO_SYSTEMD = 1 ]; then
    echo "  --no-systemd: left systemd alone."
elif ! systemctl --user show-environment >/dev/null 2>&1; then
    echo "  No systemd user session here: start the panel with  .venv/bin/python -m jarvis.panel  instead."
else
    systemctl --user daemon-reload   # reads the new unit files; restarts nothing
    # The panel works before Jarvis is configured, so it's started now (unless it's already running).
    if systemctl --user is-active --quiet jarvis-panel; then
        kept "jarvis-panel is already running (left as it is)"
    elif [ -e "$UNIT_DIR/jarvis-panel.service" ]; then
        systemctl --user enable --now jarvis-panel && did "jarvis-panel enabled and started"
    fi
    # Jarvis itself needs its Anthropic key first, so it's left for you to start.
    if systemctl --user is-active --quiet jarvis; then
        kept "jarvis is already running (left as it is)"
    else
        NEXT+=("When the Anthropic key is in (Setup page), start Jarvis and have it start at login:  systemctl --user enable --now jarvis")
    fi
    [ "$(loginctl show-user "${USER:-$(id -un)}" -p Linger --value 2>/dev/null)" = yes ] ||
        need_sudo "sudo loginctl enable-linger ${USER:-$(id -un)}   # optional: start Jarvis at boot without logging in"
fi

# ---- summary -----------------------------------------------------------------------------------
say "Summary"
[ ${#DONE[@]} -eq 0 ] || { echo "Done:"; printf '  - %s\n' "${DONE[@]}"; }
[ ${#KEPT[@]} -eq 0 ] || { echo "Already in place:"; printf '  - %s\n' "${KEPT[@]}"; }
if [ ${#SUDO[@]} -gt 0 ]; then
    echo
    echo "These need administrator rights, so they were NOT run. Copy them into a terminal yourself:"
    printf '  %s\n' "${SUDO[@]}"
fi
echo
echo "Next:"
[ ${#NEXT[@]} -eq 0 ] || printf '  - %s\n' "${NEXT[@]}"
echo "  - Open the panel at http://127.0.0.1:${PANEL_PORT:-8767} (its sign-in token is the file data/panel-token),"
echo "    and follow the Setup page. Full guide: docs/setup.md"
