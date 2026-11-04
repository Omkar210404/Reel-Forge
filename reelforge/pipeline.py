"""The full reel pipeline: voiceover -> captions -> visuals -> assemble."""
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import captions, visuals, voice
from .config import ROOT, load_config, pexels_key
from .ffmpeg import binary, ffmpeg, probe

TAIL = 0.6  # seconds of silence kept after the last word

SAMPLE_SCRIPT = ("Most people check their phone within minutes of waking up. "
                 "Try this instead: drink a glass of water, and step outside for two minutes. "
                 "Your brain will thank you.")


def find_music(cfg: dict, override: str | None) -> Path | None:
    if override:
        return Path(override)
    folder = ROOT / cfg["music"]["folder"]
    tracks = sorted(folder.glob("*.mp3")) if folder.exists() else []
    return tracks[0] if tracks else None


def assemble(bg: Path, cap_list: Path, voice_wav: Path, music: Path | None, total: float,
             cfg: dict, out: Path) -> None:
    fps = cfg["video"]["fps"]
    inputs = ["-i", bg, "-f", "concat", "-safe", "0", "-i", cap_list, "-i", voice_wav]
    graph = ["[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
             f"[2:a]aresample=48000,apad,atrim=0:{total:.3f}[vo]"]
    if music:
        m = cfg["music"]
        inputs = [*inputs, "-stream_loop", "-1", "-i", music]
        graph[1] = graph[1].replace("[vo]", ",asplit=2[vo][sc]")
        graph.append(f"[3:a]aresample=48000,aformat=channel_layouts=stereo,"
                     f"volume={float(m['volume']):.3f},atrim=0:{total:.3f}[m0]")
        if m["duck"]:
            # The voice drives a compressor on the music, so music dips while someone speaks.
            graph.append("[m0][sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[m1]")
        else:
            graph.append("[sc]anullsink;[m0]anull[m1]")
        graph.append(f"[m1]afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5[mf];"
                     "[vo][mf]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        graph[1] = graph[1].replace("[vo]", "[a]")
    ffmpeg(*inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
           "-t", f"{total:.3f}", "-r", fps, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
           "-movflags", "+faststart", out)


def make(script: str, out: Path, *, bg: str | None = None, topic: str = "",
         image: str | None = None, html: str | None = None, music: str | None = None, voice_name: str | None = None,
         overrides: dict | None = None, cfg: dict | None = None, keep_work: bool = False,
         log=print) -> dict:
    """Build a reel from script text. Returns a report dict used by the CLI, UI and selftest."""
    cfg = cfg or load_config()
    for section, vals in (overrides or {}).items():
        cfg[section].update(vals)
    script = " ".join(script.split())
    if not script:
        raise ValueError("script is empty")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = ROOT / "data" / "work" / time.strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True, exist_ok=True)
    rep: dict = {"work": str(work)}
    t0 = time.time()
    v = cfg["video"]

    log("[1/4] voiceover   edge-tts " + (voice_name or cfg["voice"]["name"]))
    wav = work / "voice.wav"
    dur = voice.synthesize(script, voice_name or cfg["voice"]["name"], cfg["voice"]["rate"], wav)
    total = dur + TAIL
    rep.update(voice=str(wav), voice_seconds=round(dur, 2))
    log(f"      {dur:.1f}s of speech")

    log(f"[2/4] captions   faster-whisper {cfg['voice']['whisper_model']} word timing")
    words_in = voice.script_words(script)
    heard = voice.transcribe(wav, cfg["voice"]["whisper_model"])
    words, matched = voice.align(words_in, heard, dur)
    (work / "words.json").write_text(json.dumps(words, indent=1), encoding="utf-8")
    cap_list, phrases = captions.build(words, cfg["captions"], v["width"], v["height"], v["fps"],
                                       total, work / "captions")
    rep.update(words=words, words_matched=matched, phrases=phrases)
    log(f"      {matched}/{len(words)} script words matched to speech, {phrases} caption phrases")

    mode = bg or ("html" if html else cfg["background"]["mode"])
    image = image or cfg["background"]["image"]
    if mode == "html":
        if not (html and Path(html).exists()):
            raise ValueError(f"animation HTML not found: {html!r}")
        from .animate import check_html
        if problem := check_html(Path(html).read_text(encoding="utf-8", errors="replace")):
            raise ValueError(problem)
        image = html
    key = pexels_key() if mode in ("pexels", "auto") else ""
    if mode == "auto":
        mode = "pexels" if key else "ai"
    if mode == "pexels" and not key:
        log("      no PEXELS_API_KEY in .env -> using free AI images instead")
        mode = "ai"
    if mode == "image" and not (image and Path(image).exists()):
        raise ValueError(f"background image not found: {image!r}")
    log(f"[3/4] visuals    mode={mode}")
    bg_video, kinds = visuals.build(words, total, cfg["background"], v, mode, image or "",
                                    topic, key, work / "bg", log)
    rep.update(background=str(bg_video), beats=kinds)

    track = find_music(cfg, music)
    log(f"[4/4] assemble   {v['width']}x{v['height']} @ {v['fps']}fps, music: "
        + (track.name + (" (ducked)" if cfg["music"]["duck"] else "") if track else "none"))
    assemble(bg_video, cap_list, wav, track, total, cfg, out)
    rep.update(output=str(out), music=str(track) if track else None,
               seconds=round(time.time() - t0, 1))
    log(f"      done -> {out}  ({rep['seconds']}s)")
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return rep


def selftest() -> bool:
    """Run the whole pipeline on a built-in script with free gradient backgrounds and check
    each stage. Needs internet only for edge-tts."""
    out = ROOT / "output" / "selftest.mp4"
    results: list[tuple[str, bool, str]] = []

    def check(stage: str, ok: bool, detail: str):
        results.append((stage, bool(ok), detail))

    print("ReelForge selftest  (gradient background, synthetic music bed, no API keys)\n")
    try:
        binary("ffmpeg")
        check("ffmpeg found", True, binary("ffmpeg"))
        # A soft generated chord stands in for user music, so mixing + ducking get exercised.
        music = ROOT / "data" / "selftest_music.mp3"
        music.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg("-f", "lavfi", "-i", "sine=f=220:d=8", "-f", "lavfi", "-i", "sine=f=277:d=8",
               "-filter_complex", "amix=inputs=2,volume=0.5", music)
        rep = make(SAMPLE_SCRIPT, out, bg="gradient", music=str(music), keep_work=True,
                   log=lambda s: print("   " + s))
    except Exception as e:  # report the failing stage instead of a traceback
        check("pipeline ran", False, f"{type(e).__name__}: {e}")
        return _report(results)

    work = Path(rep["work"])
    vd = rep["voice_seconds"]
    check("voiceover generated", Path(rep["voice"]).exists() and vd > 2, f"{vd}s wav")

    words = rep["words"]
    n_script = len(voice.script_words(SAMPLE_SCRIPT))
    starts = [w["start"] for w in words]
    timed = (len(words) == n_script and starts == sorted(starts)
             and all(w["end"] > w["start"] for w in words)
             and starts[0] < 1.0 and words[-1]["end"] <= vd + 0.2
             and rep["words_matched"] >= 0.8 * n_script)
    check("captions timed", timed,
          f"{rep['words_matched']}/{n_script} words matched, first word at {starts[0]:.2f}s, "
          f"last ends {words[-1]['end']:.2f}s of {vd:.2f}s")
    check("caption frames drawn", any((work / "captions").glob("c*.png")),
          f"{rep['phrases']} phrases")

    bg_ok = Path(rep["background"]).exists() and set(rep["beats"]) == {"gradient"}
    check("visuals rendered", bg_ok, f"{len(rep['beats'])} gradient beats")

    info = probe(out) if out.exists() else {"streams": [], "format": {"duration": 0}}
    vs = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    dur = float(info["format"]["duration"])
    check("video rendered", vs.get("width") == 1080 and vs.get("height") == 1920 and has_audio
          and abs(dur - (vd + TAIL)) < 0.3,
          f"{vs.get('width')}x{vs.get('height')}, {dur:.2f}s, audio={'yes' if has_audio else 'no'}")
    # Decode every frame: catches files that exist but would not play.
    dec = subprocess.run([binary("ffmpeg"), "-v", "error", "-i", str(out), "-f", "null", "-"],
                         capture_output=True, text=True)
    check("file plays (full decode)", out.exists() and dec.returncode == 0 and not dec.stderr.strip(),
          f"{out.stat().st_size // 1024} KB, {dec.stderr.strip()[:80] or 'no decode errors'}")
    shutil.rmtree(work, ignore_errors=True)
    return _report(results, out)


def _report(results, out: Path | None = None) -> bool:
    print()
    for stage, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {stage:<26} {detail}")
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} checks passed" + (f"  ->  {out}" if out else ""))
    return passed == len(results)
