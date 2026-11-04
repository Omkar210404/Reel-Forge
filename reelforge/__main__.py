"""reelforge make | selftest | ui"""
import argparse
import sys
from pathlib import Path

from .config import ROOT


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="reelforge", description="Script in, vertical reel out.")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="make a reel from a script file")
    m.add_argument("--script", required=True, help="plain-text script file")
    m.add_argument("--topic", default="", help="topic, used as a fallback stock-footage search")
    m.add_argument("--bg", choices=["pexels", "gradient", "image"], help="override background mode")
    m.add_argument("--image", help="picture for --bg image")
    m.add_argument("--music", help="music file (default: first mp3 in assets/music/)")
    m.add_argument("--voice", help="edge-tts voice name, e.g. en-GB-RyanNeural")
    m.add_argument("--config", help="config file (default: config.toml)")
    m.add_argument("--out", default=str(ROOT / "output" / "reel.mp4"))
    sub.add_parser("selftest", help="run the full pipeline on a built-in sample")
    u = sub.add_parser("ui", help="open the local web app")
    u.add_argument("--port", type=int, default=8770)
    a = p.parse_args(argv)

    if a.cmd == "selftest":
        from .pipeline import selftest
        return 0 if selftest() else 1
    if a.cmd == "ui":
        from .ui import serve
        serve(a.port)
        return 0

    from .config import load_config
    from .pipeline import make
    try:
        make(Path(a.script).read_text(encoding="utf-8"), Path(a.out), bg=a.bg, topic=a.topic,
             image=a.image, music=a.music, voice_name=a.voice, cfg=load_config(a.config))
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
