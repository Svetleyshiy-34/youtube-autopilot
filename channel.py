#!/usr/bin/env python3
"""ai-channel: automatic short educational videos on any topic.


One run = one video:
    topic -> script (LLM) -> slides (Pillow) -> voise (TTS) -> video (ffmpeg) -> YouTube (optional)

All setings live in config.toml, so the same code works for any niche,
language, model and voice.

Usage:
    python channel.py                 # uses ./config.toml
    python channel.py my_config.toml  # another config
    python channel.py --no-upload     #build the video only
"""

import argparse
import json
import os
import subprocess
import sys
import textwrap
import tomllib
import urllib.request
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_SANS_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
FONT_MONO_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejavuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "C:/Windows/Fonts/consola.ttf",
]

def load_config(path: Path) -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg["_root"] = path.resolve().parent
    return cfg

def resolve(cfg: dict, p: str) -> Path:
    path = Path(p).expanduser()
    return path if path.is_absolute() else cfg["_root"] / path

def pick_topic(cfg: dict) -> str:
    topics_file = resolve(cfg, cfg["channel"]["topics_file"])
    done_file = resolve(cfg, cfg["channel"]["done_file"])
    done = set(done_file.read_text(encoding="utf-8").splitlines()) if done_file.exists() else set()
    for line in topics_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and line not in done:
            return line
        sys.exit("No topics left: add new lines to" + str(topics_file))

