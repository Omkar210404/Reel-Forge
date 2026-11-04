"""Thin helpers around the ffmpeg / ffprobe binaries."""
import glob
import json
import os
import shutil
import subprocess
from functools import cache
from pathlib import Path


class FFmpegMissing(RuntimeError):
    pass


@cache
def binary(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    # winget installs land here and are not on PATH until a new shell starts.
    pattern = os.path.expandvars(
        rf"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\*\bin\{name}.exe")
    hits = glob.glob(pattern)
    if hits:
        return hits[0]
    raise FFmpegMissing(
        f"{name} not found. Install FFmpeg: winget install Gyan.FFmpeg.Essentials "
        "| brew install ffmpeg | sudo apt install ffmpeg")


def ffmpeg(*args) -> None:
    cmd = [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[-800:]}")


def probe(path: Path) -> dict:
    res = subprocess.run([binary("ffprobe"), "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def probe_duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])
