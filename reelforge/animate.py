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


WORDS_PER_SECOND = 2.6   # edge-tts at +5% speaks about this fast


DIALOGUE_RULES = """Write it as a MINI MOVIE with dialogue:
- One line per line of text, each starting with the speaker's name in capitals and a colon, e.g.
NARRATOR: It was midnight, and the project was due tomorrow.
ARJUN: I don't even know where to start!
- Mostly the characters talking to each other (natural, fun, short lines a kid would enjoy). NARRATOR only opens the scene, jumps in time, or gives the final takeaway.
- Use the characters' names exactly as given (one or two words, no apostrophes) as the speaker names.
- 8 to 14 lines. Each line is one shot of the movie.

Reply with the script lines only. No title, no blank lines, no stage directions."""

TALK_RULES = """
DIALOGUE - this is a mini movie, so the characters talk:
- Each line above is spoken by the named character (NARRATOR is an off-screen voice: nobody talks, just show the action).
- window.REEL.words = [{ start, end, speaker }] lists every spoken word with its speaker NAME in capitals. In renderFrame(t), whoever has a word at time t is talking: open and close their mouth quickly while each word plays (a character with no mouth bounces or squashes its eyes instead) and add small hand gestures. Everyone else listens and reacts (nods, looks at the speaker, surprised or happy faces).
- Use the rhythm of a film conversation (two-shot, over-the-shoulder, close-up) and turn the speaker partly towards the camera.
- In preview (no window.REEL) fake the words by spreading each line's words evenly across its 3 seconds.
"""


def claude_prompt(script: str, topic: str = "", characters: str = "") -> str:
    from .voice import parse_dialogue
    dialogue = parse_dialogue(script)
    if dialogue:
        lines = "\n".join(f"{i + 1}. {sp}: {t}" for i, (sp, t) in enumerate(dialogue))
    else:
        lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences(script)))
    talk = TALK_RULES if dialogue else ""
    chars = characters.strip()
    cast = (f"CHARACTERS - build exactly these and keep the same look in every scene:\n{chars}\n"
            if chars else "CHARACTERS - invent ONE cute main character (big glossy eyes with "
                          "highlights, simple expressive body) and keep it the same in every scene.\n")
    return f"""Make a 3D cartoon animation for a vertical short video (9:16, 1080x1920) as ONE self-contained HTML file using Three.js. It should feel like a scene from an animated movie, not two toys moving in front of a camera.
{f"Topic: {topic}" if topic else ""}
{cast}
NARRATION - one scene per line, in this order:
{lines}
{talk}
STEP 1 - STORYBOARD FIRST. Right after <!doctype html>, write an HTML comment <!-- STORYBOARD ... --> with one entry per line: location, the props that line mentions, what each character does, their emotion, the shot (wide / medium / close-up / over-the-shoulder / top-down) and the camera move. No two scenes in a row may use the same shot. Then build exactly that storyboard.

STEP 2 - BUILD IT. Rules for a proper scene:
- SETS: every location is a full set with depth: a foreground object, the characters in the middle, and a detailed background (walls, windows, furniture, shelves, sky, plants, whatever fits) with 8+ props. Give each location its own colour mood and lighting (warm lamp at night, bright morning, glowing lab and so on).
- PROPS: every object the narration mentions must be clearly modelled, recognisable at a glance, large enough to read on a phone, and turned towards the camera when it matters. Example: a laptop has a base, keyboard keys and an open glowing screen; "code" is rows of coloured bars on that screen; a test result is a big glowing red or green light. Show ideas visually: icons, lights, arrows, sparkles, things changing colour.
- ACTING: characters touch, carry, point at and react to the props. Show emotion with the eyes, eyebrows and mouth (worried, surprised, happy), plus body language (slumped, jumping, high-fiving).
- STAGING: characters never block each other's faces. Place them side by side or staggered in depth, keep their sizes consistent with the descriptions, and keep faces visible.
- FRAMING (9:16 is narrow): camera FOV about 50. In wide and medium shots show the characters head to feet, about 35-50% of the screen height. Close-ups only on faces or the key prop. Keep the main action in the top 55% of the frame or below 72%: captions cover the band between 55% and 72% of the height.
- MOTION: easing on everything, a camera move in every shot, small idle motion (breathing, blinking), and a quick transition at each scene change (a whip pan or a fade through a colour that lasts 0.25 s at most, so no frame is mostly faded).
- BRIGHT: every shot is well lit and easy to see on a phone, night scenes included (strong ambient + hemisphere light, a bright key light, lit faces, no near-black areas behind the characters).
- CAMERA SAFETY: the camera never sits inside or right behind a character's head or body, and nothing fills the foreground so much that it hides the action.
- LOOK: Pixar-like cartoon. MeshToonMaterial with a small gradient map, saturated colours, a warm key light plus a rim light, soft fog, rounded smooth shapes (spheres with 32+ segments, capsules, rounded boxes), emissive glow for screens and lights.

TECHNICAL RULES (a tool records this page frame by frame, so follow them exactly):
- Load Three.js with an import map from {THREE_URL} . No other files: no model, texture or font downloads. Build everything from Three.js primitives.
- Timing comes from window.REEL if it exists: {{ duration, scenes: [{{ start, end, text }}] }} in seconds, one scene per line above. If it does not exist (preview), create a default with the same lines, about 3 seconds each.
- Define window.renderFrame = (t) => {{ ... }} that sets the WHOLE scene for time t in seconds (positions, rotations, visibility, camera, colours) from t alone, then calls renderer.render(scene, camera). No Date.now, performance.now or unseeded Math.random inside it. It must work for any t, in any order.
- If window.REEL_CAPTURE is not true, run a preview loop with requestAnimationFrame that calls renderFrame(elapsed % duration).
- Set window.REEL_READY = true once everything is built.
- The canvas fills the window: renderer.setSize(innerWidth, innerHeight), setPixelRatio(1), antialias on.
- No letters or words inside the animation (subtitles are added later).
- Keep it light enough to render at 1080x1920: under ~200k triangles, no heavy post-processing.

Reply with the complete HTML file only."""


def script_prompt(topic: str, characters: str = "", seconds: int = 45,
                  dialogue: bool = False) -> str:
    wps = 1.75 if dialogue else WORDS_PER_SECOND   # dialogue voices speak slower, plus pauses
    words = int(seconds * wps)
    cast = (f"Characters:\n{characters.strip()}" if characters.strip()
            else "Invent one cute main character if a character helps.")
    return f"""Write the voiceover script for a {seconds}-second vertical reel (YouTube Shorts / Instagram Reels) that will be animated as a 3D cartoon.
Topic: {topic}
{cast}

Rules:
- About {words} words in total (speaker names not counted), never more than {int(85 * wps)}. 8 to 12 sentences.
- Line 1 is a hook: a surprising fact, a bold question or a relatable problem, under 12 words.
- A clear arc: hook, problem, turning point, the idea explained through what the characters DO, payoff, then a one-line takeaway the viewer remembers.
- Every sentence is one animated scene: concrete, visual actions and objects (what we SEE), not abstract talk.
- Short spoken sentences (under 15 words), simple words, no jargon unless the scene shows what it means.
- Every sentence ends with a full stop, question mark or exclamation mark. No emojis, hashtags, stage directions, quotes, titles or speaker labels.

{DIALOGUE_RULES if dialogue else "Reply with the script only, as one paragraph."}"""


def run_claude(prompt: str, timeout: int = 2400) -> str:
    """Run the local Claude Code CLI (`claude -p`) on the user's own Claude plan (no API key)
    in an empty temp folder, so it sees only the prompt."""
    import shutil
    import tempfile

    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI not found. Install it (npm install -g "
                           "@anthropic-ai/claude-code), run `claude` once to log in, or use "
                           "claude.ai and paste the result instead.")
    import os
    # Output goes to files, not pipes: on Windows `claude` is a .cmd wrapper, and a pipe held
    # open by the real claude.exe would block forever after a timeout.
    with tempfile.TemporaryDirectory() as empty:
        out_f, err_f = Path(empty) / "out.txt", Path(empty) / "err.txt"
        work = Path(empty) / "work"
        work.mkdir()
        with open(out_f, "w", encoding="utf-8") as so, open(err_f, "w", encoding="utf-8") as se:
            proc = subprocess.Popen(
                [exe, "-p", "--output-format", "text", "--disallowedTools", "Bash", "Edit",
                 "Write"], stdin=subprocess.PIPE, stdout=so, stderr=se, cwd=work,
                text=True, encoding="utf-8")
            proc.stdin.write(prompt)
            proc.stdin.close()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":   # kill the whole tree: cmd.exe -> claude.exe
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                   capture_output=True)
                else:
                    proc.kill()
                proc.wait(timeout=30)
                raise RuntimeError(f"Claude Code took longer than {timeout // 60} minutes and "
                                   "was stopped. Press Make reel again, or shorten the script.")
        out = out_f.read_text(encoding="utf-8", errors="replace")
        err = err_f.read_text(encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"Claude Code failed: {(err or out).strip()[:300]}")
    return out


def write_script(topic: str, characters: str = "", seconds: int = 45,
                 dialogue: bool = False) -> str:
    """Have Claude Code write a reel script (15-90 s) for a topic: narration as one paragraph,
    or a mini movie with one 'NAME: line' per line."""
    from .voice import SPEAKER_RE
    seconds = max(15, min(int(seconds), 90))
    reply = run_claude(script_prompt(topic, characters, seconds, dialogue), timeout=300)
    if not dialogue:
        return " ".join(reply.split()).strip('"')
    lines = [" ".join(ln.replace("**", "").split()) for ln in reply.splitlines()]
    return "\n".join(ln for ln in lines if SPEAKER_RE.match(ln))


def ask_claude(script: str, topic: str, out: Path, log, characters: str = "") -> Path:
    """Have Claude Code storyboard and write the animation; saves it to `out`."""
    import re

    log("    asking Claude Code to storyboard and build the 3D animation (usually 5-12 min)...")
    reply = run_claude(claude_prompt(script, topic, characters))
    m = re.search(r"<!doctype html.*?</html>|<html.*?</html>", reply, re.S | re.I)
    if not m:
        raise RuntimeError(f"Claude Code did not return an HTML file. {reply.strip()[:300]}")
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
           out: Path, log, words: list[dict] | None = None) -> None:
    """Open the HTML in a headless browser and record renderFrame(t) for every frame."""
    from playwright.sync_api import sync_playwright

    reel = {"duration": round(total, 3), "width": w, "height": h,
            "scenes": [{"start": round(s["start"], 3), "end": round(s["end"], 3),
                        "text": s["text"], "speaker": s.get("speaker")} for s in scenes]}
    if words and any(x.get("speaker") for x in words):  # who is talking when, for mouth movement
        reel["words"] = [{"start": x["start"], "end": x["end"], "speaker": x.get("speaker")}
                         for x in words]
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
