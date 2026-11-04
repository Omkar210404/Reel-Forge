"""Claude-style 3D cartoon reels: build a prompt that asks Claude for a Three.js animation as
one HTML file, then record that page frame by frame, timed to the voiceover.

Contract the HTML follows (the prompt spells it out):
  window.REEL          injected by us: {duration, width, height, scenes: [{start, end, text}]}
  window.REEL_CAPTURE  true while recording, so the page skips its own preview loop
  window.renderFrame(t) draws the exact picture for second t
  window.REEL_READY    set to true once the scene is built
"""
import json
import subprocess
from pathlib import Path

from .ffmpeg import binary

THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"


def sentences(text: str) -> list[str]:
    """Split like visuals.beats does (a word ending in . ! or ?), so line N = scene N."""
    out, cur = [], []
    for w in text.split():
        cur.append(w)
        if w[-1] in ".!?":
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def claude_prompt(script: str, topic: str = "") -> str:
    lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences(script)))
    return f"""Make a 3D cartoon animation for a vertical short video (9:16, 1080x1920) as ONE self-contained HTML file using Three.js.
{f"Topic: {topic}" if topic else ""}
NARRATION - one scene per line, in this order:
{lines}

TECHNICAL RULES (a tool records this page frame by frame, so follow them exactly):
- Load Three.js with an import map from {THREE_URL} . No other files: no model, texture or font downloads. Build everything from Three.js primitives.
- Timing comes from window.REEL if it exists: {{ duration, scenes: [{{ start, end, text }}] }} in seconds, one scene per line above. If it does not exist (preview), create a default with the same lines, about 3 seconds each.
- Define window.renderFrame = (t) => {{ ... }} that sets the WHOLE scene for time t in seconds (positions, rotations, camera, colours) from t alone, then calls renderer.render(scene, camera). No Date.now, performance.now or unseeded Math.random inside it. It must work for any t, in any order.
- If window.REEL_CAPTURE is not true, run a preview loop with requestAnimationFrame that calls renderFrame(elapsed % duration).
- Set window.REEL_READY = true once everything is built.
- The canvas fills the window: renderer.setSize(innerWidth, innerHeight), setPixelRatio(1), antialias on.
- No text, letters or UI inside the animation (subtitles are added later). Keep the band between 55% and 72% of the screen height free of the main action, because captions go there.

LOOK:
- Pixar-like cartoon: MeshToonMaterial with a small gradient map, bright saturated colours, warm key light plus a rim light, soft fog, rounded shapes (spheres, capsules, rounded boxes).
- ONE cute main character built from primitives (big glossy eyes with highlights, simple expressive body) that stays the same in every scene and ACTS OUT each line: walks, turns, waves, jumps, reacts.
- Each scene has its own setting and props and its own camera move (push-in, orbit, follow). Use easing so motion feels alive, plus small idle motion (breathing, blinking).
- FRAMING (the 9:16 frame is narrow): keep the WHOLE character in view in every scene, head to feet, about 35-50% of the screen height, standing in the upper half of the frame above the caption band. Use a camera FOV around 50 and pull the camera back far enough; close-ups only on the face, never cutting off the head.
- Smooth, polished shapes: use high segment counts (spheres 32+ segments), rounded edges, a face with big eyes on the FRONT of the head, and a simple colourful ground and background for each scene.
- Keep it light enough to render at 1080x1920: under ~150k triangles, no heavy post-processing.

Reply with the complete HTML file only."""


def ask_claude(script: str, topic: str, out: Path, log) -> Path:
    """Have the local Claude Code CLI (`claude -p`) write the animation. Uses the user's own
    Claude plan; no API key. Runs in an empty folder so it only sees the prompt."""
    import re
    import shutil
    import tempfile

    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI not found. Install it (npm install -g "
                           "@anthropic-ai/claude-code) or paste HTML from claude.ai instead.")
    log("    asking Claude Code to write the 3D animation (usually 5-10 min)...")
    with tempfile.TemporaryDirectory() as empty:
        res = subprocess.run([exe, "-p", "--output-format", "text"], input=claude_prompt(script, topic),
                             capture_output=True, text=True, encoding="utf-8", cwd=empty, timeout=900)
    reply = res.stdout or ""
    m = re.search(r"<!doctype html.*?</html>|<html.*?</html>", reply, re.S | re.I)
    if res.returncode != 0 or not m:
        raise RuntimeError("Claude Code did not return an HTML file. "
                           f"{(res.stderr or reply).strip()[:300]}")
    out.write_text(m[0], encoding="utf-8")
    if problem := check_html(m[0]):
        raise RuntimeError(f"Claude's HTML is not usable: {problem}")
    log(f"    Claude wrote {len(m[0]) // 1024} KB of animation code -> {out.name}")
    return out


def check_html(html: str) -> str | None:
    """Return a friendly problem description, or None if the HTML looks like an animation."""
    if "Reply with the complete HTML file only" in html:
        return ("That is the prompt, not the animation. Paste it into Claude (claude.ai), "
                "then paste the HTML file Claude replies with here.")
    if "<script" not in html.lower():
        return "This doesn't look like an HTML file. Paste the full HTML file Claude made."
    if "renderFrame" not in html:
        return ("This HTML has no renderFrame(t). Ask Claude: 'Follow the TECHNICAL RULES, "
                "define window.renderFrame(t)'.")
    return None


def _browser(pw):
    errors = []
    for channel in ("msedge", "chrome", None):  # installed browsers first: no download needed
        try:
            return pw.chromium.launch(channel=channel, args=[
                "--enable-gpu", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        except Exception as e:  # noqa: BLE001 - try the next browser
            errors.append(f"{channel or 'bundled chromium'}: {str(e).splitlines()[0]}")
    raise RuntimeError("no browser for recording. Install Edge or Chrome, or run "
                       "`playwright install chromium`.\n" + "\n".join(errors))


def record(html: Path, scenes: list[dict], total: float, w: int, h: int, fps: int,
           out: Path, log) -> None:
    """Open the HTML in a headless browser and record renderFrame(t) for every frame."""
    from playwright.sync_api import sync_playwright

    reel = {"duration": round(total, 3), "width": w, "height": h,
            "scenes": [{"start": round(s["start"], 3), "end": round(s["end"], 3),
                        "text": s["text"]} for s in scenes]}
    frames = round(total * fps)
    enc = subprocess.Popen(
        [binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "image2pipe",
         "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-", "-frames:v", str(frames),
         "-vf", f"scale={w}:{h},setsar=1", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    page_errors: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = _browser(pw)
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.add_init_script(f"window.REEL = {json.dumps(reel)}; window.REEL_CAPTURE = true;")
            page.goto(html.resolve().as_uri())
            try:
                page.wait_for_function("window.REEL_READY === true && "
                                       "typeof window.renderFrame === 'function'", timeout=60000)
            except Exception:
                hint = f" Page errors: {page_errors[:2]}" if page_errors else ""
                raise RuntimeError("the animation never became ready (it must set "
                                   "window.REEL_READY and define window.renderFrame)." + hint)
            log(f"    recording {frames} frames in {browser.browser_type.name} "
                f"({browser.version})")
            step = max(frames // 5, 1)
            for i in range(frames):
                page.evaluate("t => window.renderFrame(t)", i / fps)
                enc.stdin.write(page.screenshot(type="jpeg", quality=92))
                if i and i % step == 0:
                    log(f"    {i}/{frames} frames")
            browser.close()
    finally:
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed while recording: {err[-500:]}")
    if page_errors:
        log(f"    note: the page reported {len(page_errors)} JS error(s), first: "
            f"{page_errors[0][:120]}")
