"""Load config.toml merged over defaults, plus .env for the optional Pexels key."""
import copy
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "voice": {"name": "en-US-AndrewMultilingualNeural", "rate": "+5%", "whisper_model": "base.en"},
    "video": {"width": 1080, "height": 1920, "fps": 30, "max_seconds": 90},
    "background": {"mode": "auto", "art_style": "cartoon", "image": "", "max_beat_seconds": 6.0, "darken": 0.2,
                   "director_model": "llama3.2:3b", "ollama_url": "http://127.0.0.1:11434",
                   "ai_fallback": True, "ai_min_interval": 45},
    "captions": {
        "style": "highlight", "words_per_group": 3, "font": "", "size": 92,
        "color": "#FFFFFF", "highlight_color": "#FFD60A", "stroke_color": "#000000",
        "stroke_width": 8, "position": 0.62, "uppercase": True,
    },
    "music": {"folder": "assets/music", "volume": 0.25, "duck": True},
}


def load_config(path: str | Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = Path(path) if path else ROOT / "config.toml"
    if path.exists():
        with open(path, "rb") as f:
            for section, values in tomllib.load(f).items():
                cfg.setdefault(section, {}).update(values)
    return cfg


def pexels_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return os.environ.get("PEXELS_API_KEY", "").strip()
