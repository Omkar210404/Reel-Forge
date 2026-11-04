"""Background visuals: split the script into beats, then make one 9:16 clip per beat from
Pexels stock video, an animated gradient, or a Ken Burns move on a still image."""
from collections import Counter
from pathlib import Path

import requests

from .config import ROOT
from .ffmpeg import ffmpeg

STOPWORDS = set("""a an the and or but if then so of to in on at by for with from into onto about
as is are was were be been being am do does did done have has had having it its it's this that
these those there here their they them you your yours we our us i me my he she his her him not no
yes can could would should will shall may might must just very really more most less much many
some any every each all than too also only even still like what which who whom whose when where
why how get got make makes made thing things way ways one two three day time year years people
because while over under again out up down off own same such into most other those over every""".split())

# Dark, saturated pairs so white captions stay readable on gradients.
PALETTES = [("0x1d2b64", "0xf8cdda"), ("0x0f2027", "0x2c5364"), ("0x41295a", "0x2f0743"),
            ("0x134e5e", "0x71b280"), ("0x232526", "0x414345"), ("0x3a1c71", "0xd76d77"),
            ("0x16222a", "0x3a6073"), ("0x4b134f", "0xc94b4b")]


def beats(words: list[dict], total: float, max_len: float) -> list[dict]:
    """One beat per sentence, split further if a sentence runs longer than `max_len`."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        long_enough = w["end"] - cur[0]["start"] >= max_len
        if w["text"][-1] in ".!?" or long_enough or i == len(words) - 1:
            out.append({"start": cur[0]["start"], "text": " ".join(x["text"] for x in cur)})
            cur = []
    # Beats tile the whole timeline with no gaps: each ends where the next one starts.
    for i, b in enumerate(out):
        b["start"] = 0.0 if i == 0 else b["start"]
        b["end"] = out[i + 1]["start"] if i + 1 < len(out) else total
    return [b for b in out if b["end"] - b["start"] > 0.05]


def keywords(text: str, n: int = 2) -> list[str]:
    words = [w.strip(".,!?;:\"'()").lower() for w in text.split()]
    words = [w for w in words if len(w) > 3 and w.isalpha() and w not in STOPWORDS]
    counts = Counter(words)
    return sorted(counts, key=lambda w: (-counts[w], -len(w)))[:n]


def _pexels_pick(query: str, key: str, used: set) -> dict | None:
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                     params={"query": query, "orientation": "portrait", "per_page": 10},
                     timeout=15)
    r.raise_for_status()
    for video in r.json().get("videos", []):
        if video["id"] in used:
            continue
        files = [f for f in video["video_files"]
                 if f.get("height") and f.get("width") and f["height"] >= 960
                 and f.get("file_type") == "video/mp4"]
        if files:
            used.add(video["id"])
            # Closest to 1920 tall keeps downloads small and quality high.
            return min(files, key=lambda f: abs(f["height"] - 1920)) | {"id": video["id"]}
    return None


def pexels_clip(text: str, topic: str, key: str, used: set, log) -> Path | None:
    """Find and download a portrait stock clip for this beat. None if nothing usable."""
    queries = []
    kw = keywords(text)
    if kw:
        queries.append(" ".join(kw))
        queries.append(kw[0])
    if topic:
        queries.append(topic)
    cache = ROOT / "data" / "cache" / "pexels"
    cache.mkdir(parents=True, exist_ok=True)
    for q in queries:
        try:
            pick = _pexels_pick(q, key, used)
        except requests.RequestException as e:
            log(f"    pexels search failed ({type(e).__name__}); using gradient")
            return None
        if not pick:
            continue
        path = cache / f"{pick['id']}_{pick['height']}.mp4"
        if not path.exists():
            with requests.get(pick["link"], stream=True, timeout=60) as r:
                r.raise_for_status()
                tmp = path.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                tmp.replace(path)
        log(f"    '{q}' -> pexels video {pick['id']}")
        return path
    return None


def render_segment(kind: str, src: Path | None, frames: int, idx: int, out: Path,
                   w: int, h: int, fps: int, darken: float) -> None:
    dur = frames / fps + 0.5
    dim = f"colorchannelmixer=rr={1 - darken:.2f}:gg={1 - darken:.2f}:bb={1 - darken:.2f}"
    enc = ["-frames:v", frames, "-an", "-r", fps, "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", out]
    if kind == "pexels":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1,{dim}"
        ffmpeg("-stream_loop", "-1", "-i", src, "-t", f"{dur:.3f}", "-vf", vf, *enc)
    elif kind == "image":
        # Upscale first so the slow zoom stays smooth; alternate zoom in / zoom out per beat.
        z = (f"1+0.12*on/{frames}" if idx % 2 == 0 else f"1.12-0.12*on/{frames}")
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},"
              f"setsar=1,{dim}")
        ffmpeg("-loop", "1", "-framerate", fps, "-i", src, "-t", f"{dur:.3f}", "-vf", vf, *enc)
    else:
        c0, c1 = PALETTES[idx % len(PALETTES)]
        src_f = (f"gradients=s={w}x{h}:r={fps}:d={dur:.3f}:c0={c0}:c1={c1}:n=2:speed=0.012:"
                 f"seed={idx + 7}")
        ffmpeg("-f", "lavfi", "-i", src_f, "-vf", "vignette=PI/5,setsar=1", *enc)


def build(words: list[dict], total: float, cfg: dict, video: dict, mode: str,
          image: str, topic: str, key: str, workdir: Path, log) -> tuple[Path, list[str]]:
    """Render the background track. Returns (video path, source used for each beat)."""
    workdir.mkdir(parents=True, exist_ok=True)
    w, h, fps = video["width"], video["height"], video["fps"]
    bs = beats(words, total, float(cfg["max_beat_seconds"]))
    used, parts, kinds = set(), [], []
    for i, b in enumerate(bs):
        frames = round(b["end"] * fps) - round(b["start"] * fps)
        kind, src = mode, None
        if mode == "pexels":
            src = pexels_clip(b["text"], topic, key, used, log)
            kind = "pexels" if src else "gradient"
        elif mode == "image":
            src = Path(image)
        out = workdir / f"bg{i:03d}.mp4"
        render_segment(kind, src, frames, i, out, w, h, fps, float(cfg["darken"]))
        parts.append(out)
        kinds.append(kind)
        log(f"    beat {i + 1}/{len(bs)}  {b['end'] - b['start']:.1f}s  {kind}")
    lst = workdir / "bg.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    out = workdir / "background.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out)
    return out, kinds
