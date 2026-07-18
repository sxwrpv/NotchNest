#!/bin/zsh
# Standalone engine launcher — starts the dictation daemon if it isn't already
# running. (When used inside NotchNest, the app manages this process itself.)
cd "$(dirname "$0")" || exit 1

if pgrep -f '\.venv/bin/python -u main\.py' > /dev/null 2>&1; then
  echo "Dictation engine is already running."
  exit 0
fi

mkdir -p "$HOME/.murmur"
# The HF "xet" transfer backend stalls on some networks; plain HTTP is reliable.
export HF_HUB_DISABLE_XET=1
nohup .venv/bin/python -u main.py >> "$HOME/.murmur/murmur.log" 2>&1 &
disown
echo "Dictation engine started (logs: ~/.murmur/murmur.log)"
