"""Background visuals: split the script into beats, plan a scene for each beat, then make one
9:16 clip per beat from Pexels stock video, a free AI image, an animated gradient, or the
user's own picture. Stills get a slow Ken Burns move so nothing sits frozen."""
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import requests

from .config import ROOT
from .ffmpeg import ffmpeg

CACHE = ROOT / "data" / "cache"
STOPWORDS = set("""a an the and or but if then so of to in on at by for with from into onto about
as is are was were be been being am do does did done have has had having it its it's this that
these those there here their they them you your yours we our us i me my he she his her him not no
yes can could would should will shall may might must just very really more most less much many
some any every each all than too also only even still like what which who whom whose when where
why how get got make makes made thing things way ways one two three day time year years people
because while over under again out up down off own same such other almost today right""".split())

# Dark, saturated pairs so white captions stay readable on gradients.
PALETTES = [("0x1d2b64", "0xf8cdda"), ("0x0f2027", "0x2c5364"), ("0x41295a", "0x2f0743"),
            ("0x134e5e", "0x71b280"), ("0x232526", "0x414345"), ("0x3a1c71", "0xd76d77"),
            ("0x16222a", "0x3a6073"), ("0x4b134f", "0xc94b4b")]
ART_STYLES = {
    "cartoon": ", cute 3D Pixar-style animated movie still, adorable characters with big "
               "expressive eyes, bright vibrant colors, soft lighting, highly detailed, "
               "vertical composition, no text",
    "photo": ", cinematic photo, dramatic lighting, highly detailed, vertical composition, no text",
}


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


def plan_scenes(texts: list[str], topic: str, cfg: dict, log) -> list[dict]:
    """For each beat: a stock-video search query and an image prompt.

    A local Ollama model gets the whole script as context and, one line at a time, describes
    what the viewer should SEE. One call per line keeps a small model from losing its place.
    Without Ollama, fall back to keywords from the line plus the topic."""
    fallback = []
    for t in texts:
        kw = " ".join(keywords(t)) or topic or "abstract background"
        fallback.append({"search": kw, "image": f"{kw} {topic}".strip()})
    model = cfg.get("director_model", "llama3.2:3b")
    if not model:
        return fallback
    script = " ".join(texts)
    cartoon = cfg.get("art_style", "cartoon") == "cartoon"

    def ask(prompt: str) -> dict:
        r = requests.post(f"{cfg.get('ollama_url', 'http://127.0.0.1:11434')}/api/chat",
                          json={"model": model, "stream": False, "format": "json",
                                "options": {"temperature": 0.4},
                                "messages": [{"role": "user", "content": prompt}]},
                          timeout=120)
        r.raise_for_status()
        reply = json.loads(r.json()["message"]["content"])
        if not isinstance(reply, dict):
            raise ValueError("model reply is not an object")
        return reply

    out = []
    try:
        hero = ""
        if cartoon:
            # One recurring character, described the same way in every scene, keeps the
            # cartoon pictures feeling like one story.
            hero = ask(
                f"Script: {script}\n\nName the ONE main character of this story for a cute 3D "
                "animated short. If the script already has one (e.g. 'a tiny robot'), use "
                "exactly that. Otherwise invent a cute guide that fits the topic. Describe only "
                "their look in under 20 words, with big expressive eyes. "
                'Reply with JSON only: {"character": "one plain sentence"}').get("character", "")
            if isinstance(hero, dict):  # small models sometimes return fields, not a sentence
                hero = ", ".join(str(v) for v in hero.values())
            hero = str(hero)[:160]
            if hero:
                log(f"    main character: {hero}")
        for t in texts:
            look = ("a scene from a 3D animated cartoon movie" if cartoon else "a realistic photo")
            cast = (f" When a character fits the moment, use the main character: {hero}."
                    if hero else "")
            s = ask(
                "You pick visuals for one moment of a short vertical video.\n"
                f"Full script (context only): {script}\nTopic: {topic or 'infer from script'}\n\n"
                f'Line to illustrate: "{t}"\n\n'
                "Show what THIS line literally mentions (if it mentions airplanes, show "
                f"airplanes; if it mentions the Moon, show the Moon).{cast}\n"
                'Reply with JSON only: {"search": "ONE short phrase of 2-3 words for a stock '
                f'video site, e.g. \\"airplane runway\\"", "image": "one vivid sentence '
                f'describing {look} of this moment, no text in the image"}}')
            if not (s.get("search") and s.get("image")):
                raise ValueError("model reply missing fields")
            search = re.split(r"[,;/]", str(s["search"]))[0].strip()[:50]
            out.append({"search": search, "image": str(s["image"])[:300]})
    except (requests.RequestException, ValueError, KeyError) as e:
        log(f"    Ollama scene plan unavailable ({type(e).__name__}); using keywords")
        return fallback
    log(f"    scene plan from Ollama {model}")
    return out


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


def _download(url: str, path: Path, **kw) -> None:
    with requests.get(url, stream=True, timeout=120, **kw) as r:
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        tmp.replace(path)


def pexels_clip(queries: list[str], key: str, used: set, log) -> Path | None:
    """Find and download a portrait stock clip for this beat. None if nothing usable."""
    folder = CACHE / "pexels"
    folder.mkdir(parents=True, exist_ok=True)
    for q in dict.fromkeys(q for q in queries if q):
        try:
            pick = _pexels_pick(q, key, used)
            if not pick:
                continue
            path = folder / f"{pick['id']}_{pick['height']}.mp4"
            if not path.exists():
                _download(pick["link"], path)
        except requests.RequestException as e:
            log(f"    pexels failed ({type(e).__name__})")
            return None
        log(f"    '{q}' -> pexels video {pick['id']}")
        return path
    return None


_last_ai = [0.0]


def ai_image(prompt: str, seed: int, cfg: dict, log) -> Path | None:
    """Free text-to-image from Pollinations (no key). The free tier allows about one image
    per 45 s per IP, so we pace requests and retry when told to wait. Results are cached."""
    folder = CACHE / "ai"
    folder.mkdir(parents=True, exist_ok=True)
    full = prompt + ART_STYLES.get(cfg.get("art_style", "cartoon"), ART_STYLES["cartoon"])
    path = folder / (hashlib.sha1(f"{full}|{seed}".encode()).hexdigest()[:16] + ".jpg")
    if path.exists():
        log(f"    cached image: {prompt[:60]}")
        return path
    url = (f"https://image.pollinations.ai/prompt/{quote(full)}"
           f"?width=1080&height=1920&nologo=true&seed={seed}")
    gap = float(cfg.get("ai_min_interval", 45))
    for _ in range(4):
        wait = _last_ai[0] + gap - time.time()
        if wait > 0:
            log(f"    waiting {wait:.0f}s (free image limit)")
            time.sleep(wait)
        _last_ai[0] = time.time()
        try:
            r = requests.get(url, timeout=90)
        except requests.RequestException as e:
            log(f"    image request failed ({type(e).__name__}), retrying")
            continue
        if r.status_code in (402, 429):
            m = re.search(r"after (\d+)s", r.text)
            _last_ai[0] = time.time() - gap + (int(m[1]) + 2 if m else gap)
            continue
        if r.ok and r.headers.get("content-type", "").startswith("image/"):
            path.write_bytes(r.content)
            log(f"    AI image: {prompt[:60]}")
            return path
        log(f"    image service returned {r.status_code}")
        return None
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
        # Upscale first so the move stays smooth. Rotate through zoom in, zoom out, pan
        # right and pan left so consecutive stills don't feel the same.
        p = f"on/{frames}"
        z, x = [(f"1+0.15*{p}", "iw/2-(iw/zoom/2)"), (f"1.15-0.15*{p}", "iw/2-(iw/zoom/2)"),
                ("1.15", f"(iw-iw/zoom)*{p}"), ("1.15", f"(iw-iw/zoom)*(1-{p})")][idx % 4]
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps},"
              f"setsar=1,{dim}")
        ffmpeg("-loop", "1", "-framerate", fps, "-i", src, "-t", f"{dur:.3f}", "-vf", vf, *enc)
    else:
        c0, c1 = PALETTES[idx % len(PALETTES)]
        src_f = (f"gradients=s={w}x{h}:r={fps}:d={dur:.3f}:c0={c0}:c1={c1}:n=2:speed=0.012:"
                 f"seed={idx + 7}")
        ffmpeg("-f", "lavfi", "-i", src_f, "-vf", "vignette=PI/5,setsar=1", *enc)


def build(words: list[dict], total: float, cfg: dict, video: dict, mode: str,
          image: str, topic: str, key: str, workdir: Path, log,
          scenes: list[dict] | None = None) -> tuple[Path, list[str]]:
    """Render the background track. Returns (video path, source used for each beat).

    mode: pexels (stock video, AI image if a search finds nothing), ai (AI images),
    gradient, image (the user's picture, path in `image`), or html (a Three.js animation
    file, path in `image`)."""
    workdir.mkdir(parents=True, exist_ok=True)
    w, h, fps = video["width"], video["height"], video["fps"]
    if mode == "html":
        # A Claude-written Three.js animation: one scene per sentence, recorded in one pass.
        from .animate import record
        scenes = scenes or beats(words, total, max_len=1e9)
        out = workdir / "background.mp4"
        record(Path(image), scenes, total, w, h, fps, out, log, words)
        return out, ["html"] * len(scenes)
    bs = scenes or beats(words, total, float(cfg["max_beat_seconds"]))
    scenes = (plan_scenes([b["text"] for b in bs], topic, cfg, log)
              if mode in ("pexels", "ai") else [{}] * len(bs))
    used, parts, kinds = set(), [], []
    for i, (b, sc) in enumerate(zip(bs, scenes)):
        frames = round(b["end"] * fps) - round(b["start"] * fps)
        kind, src = "gradient", None
        if mode == "pexels":
            src = pexels_clip([sc["search"], *keywords(b["text"], 1), topic], key, used, log)
            kind = "pexels"
        if mode == "ai" or (mode == "pexels" and not src and cfg.get("ai_fallback", True)):
            src = ai_image(sc["image"], 1000 + i, cfg, log)
            kind = "image"
        if mode == "image":
            src, kind = Path(image), "image"
        if src is None:
            kind = "gradient"
        out = workdir / f"bg{i:03d}.mp4"
        render_segment(kind, src, frames, i, out, w, h, fps, float(cfg["darken"]))
        parts.append(out)
        kinds.append("ai" if kind == "image" and mode != "image" else kind)
        log(f"    beat {i + 1}/{len(bs)}  {b['end'] - b['start']:.1f}s  {kinds[-1]}")
    lst = workdir / "bg.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    out = workdir / "background.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out)
    return out, kinds
