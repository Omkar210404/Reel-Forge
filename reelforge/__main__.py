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
    m.add_argument("--bg", choices=["auto", "pexels", "ai", "gradient", "image", "html"], help="override background mode")
    m.add_argument("--image", help="picture for --bg image")
    m.add_argument("--characters", default="", help="how the characters look (3D cartoon mode)")
    m.add_argument("--cast", default="", help="voice per speaker, e.g. \"CLAUDE=en-US-AnaNeural, ARJUN=en-IN-PrabhatNeural+20Hz\"")
    m.add_argument("--html", help="Three.js animation from Claude (see `reelforge prompt`)")
    m.add_argument("--music", help="music file (default: first mp3 in assets/music/)")
    m.add_argument("--voice", help="edge-tts voice name, e.g. en-GB-RyanNeural")
    m.add_argument("--config", help="config file (default: config.toml)")
    m.add_argument("--out", default=str(ROOT / "output" / "reel.mp4"))
    pr = sub.add_parser("prompt", help="print a Claude prompt for a 3D cartoon animation")
    pr.add_argument("--script", required=True)
    pr.add_argument("--topic", default="")
    pr.add_argument("--characters", default="")
    sc = sub.add_parser("script", help="have Claude Code write a reel script for a topic")
    sc.add_argument("--topic", required=True)
    sc.add_argument("--characters", default="")
    sc.add_argument("--seconds", type=int, default=45, help="target length, 15-90")
    sc.add_argument("--dialogue", action="store_true", help="mini movie: characters talk (NAME: line)")
    sub.add_parser("selftest", help="run the full pipeline on a built-in sample")
    u = sub.add_parser("ui", help="open the local web app")
    u.add_argument("--port", type=int, default=8770)
    a = p.parse_args(argv)

    if a.cmd == "selftest":
        from .pipeline import selftest
        return 0 if selftest() else 1
    if a.cmd == "prompt":
        from .animate import claude_prompt
        print(claude_prompt(Path(a.script).read_text(encoding="utf-8"), a.topic, a.characters))
        return 0
    if a.cmd == "script":
        from .animate import write_script
        print(write_script(a.topic, a.characters, a.seconds, a.dialogue))
        return 0
    if a.cmd == "ui":
        from .ui import serve
        serve(a.port)
        return 0

    from .config import load_config
    from .pipeline import make
    try:
        make(Path(a.script).read_text(encoding="utf-8"), Path(a.out), bg=a.bg, topic=a.topic, characters=a.characters, cast=a.cast,
             image=a.image, html=a.html, music=a.music, voice_name=a.voice, cfg=load_config(a.config))
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
