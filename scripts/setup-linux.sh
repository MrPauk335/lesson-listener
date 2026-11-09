#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" == '--system' ]]; then
  sudo apt-get update -qq
  sudo apt-get install -y python3-venv libpython3-dev libportaudio2 libegl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-render-util0
fi
python3 -m venv .venv
.venv/bin/python -m pip install -r packaging/requirements-build.txt
printf '\nRun: .venv/bin/python app.py\nBuild: .venv/bin/python packaging/build_linux.py\n'
