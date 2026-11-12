#!/bin/sh
# Run on the Pi without stream secrets. Captures capability inventory, not watts.
set -eu
tr '\000' '\n' </proc/device-tree/model
uname -a
cat /etc/os-release
free -m
ffmpeg -version
ffmpeg -hide_banner -hwaccels
ffmpeg -hide_banner -decoders
ffmpeg -hide_banner -encoders
ls -l /dev/video* /dev/dri/* 2>/dev/null || true
command -v v4l2-ctl >/dev/null && v4l2-ctl --list-devices || true
command -v vcgencmd >/dev/null && vcgencmd get_throttled || true
