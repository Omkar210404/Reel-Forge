"""Voiceover (edge-tts) and word timing (faster-whisper aligned back to the script)."""
import asyncio
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from .config import ROOT
from .ffmpeg import ffmpeg, probe_duration


def synthesize(text: str, voice: str, rate: str, out_wav: Path, pitch: str = "+0Hz") -> float:
    """Speak `text` with an edge-tts neural voice. Returns the audio length in seconds."""
    import edge_tts

    mp3 = out_wav.with_suffix(".mp3")
    # The free edge-tts service sometimes drops a request ("NoAudioReceived"); retrying works.
    for attempt in range(4):
        try:
            asyncio.run(edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(mp3)))
            break
        except Exception as e:  # noqa: BLE001 - network/service errors vary
            if attempt == 3:
                raise RuntimeError(f"voice service failed 4 times ({type(e).__name__}). "
                                   "Check your internet and try again in a minute.") from e
            time.sleep(2 * (attempt + 1))
    # One decoded WAV is used for both transcription and mixing, so timings match exactly.
    ffmpeg("-i", mp3, "-ac", "1", "-ar", "48000", out_wav)
    mp3.unlink(missing_ok=True)
    return probe_duration(out_wav)


# Dialogue scripts: every line starts with "NAME:" (up to 3 words, no apostrophes).
SPEAKER_RE = re.compile(r"^\s*([A-Za-z][\w-]*(?: [A-Za-z0-9][\w-]*){0,2})\s*:\s*(\S.*)$")
# Voices handed out to characters in order of appearance (NARRATOR uses the main voice).
# Mascot-like names get the cute child voice; people get the others (a pitched-up voice
# stands in for a boy, since edge-tts has no boy voice).
CUTE_VOICE = "en-US-AnaNeural"
CUTE_NAMES = re.compile(r"CLAUDE|BUDDY|BOT|ROBOT|PET|BUNNY|PUPPY|KITTY")
CAST_POOL = ["en-IN-PrabhatNeural+20Hz", "en-GB-MaisieNeural", "en-US-GuyNeural",
             "en-US-JennyNeural", "en-GB-RyanNeural"]


def parse_dialogue(text: str) -> list[tuple[str, str]] | None:
    """[(SPEAKER, line), ...] if every non-empty line is 'NAME: text', else None."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    found = [SPEAKER_RE.match(ln) for ln in lines]
    if len(lines) < 2 or not all(found):
        return None
    return [(m[1].strip().upper(), " ".join(m[2].split())) for m in found]


# Measured speaking speeds: narration (Andrew, +5%) ~2.6 words/s; dialogue voices ~1.9 words/s
# (the cute child voice is the slowest), plus a 0.3 s pause between lines.
NARRATION_WPS, DIALOGUE_WPS, LINE_GAP = 2.6, 1.9, 0.3


def estimate_seconds(text: str) -> float:
    lines = parse_dialogue(text)
    if lines:
        words = sum(len(t.split()) for _, t in lines)
        return words / DIALOGUE_WPS + LINE_GAP * (len(lines) - 1)
    return len(text.split()) / NARRATION_WPS


def cast_voices(speakers: list[str], narrator_voice: str, overrides: str = "") -> dict:
    """Map each speaker to (voice, pitch). `overrides` looks like
    'CLAUDE=en-US-AnaNeural, ARJUN=en-IN-PrabhatNeural+20Hz'."""
    wanted = {}
    for part in re.split(r"[,\n;]", overrides or ""):
        if "=" in part:
            name, v = part.split("=", 1)
            wanted[name.strip().upper()] = v.strip()
    cast, pool = {}, [v for v in CAST_POOL if v not in wanted.values()]
    for sp in dict.fromkeys(speakers):
        if sp in wanted:
            v = wanted[sp]
        elif sp == "NARRATOR":
            v = narrator_voice
        elif CUTE_NAMES.search(sp) and CUTE_VOICE not in [c[0] for c in cast.values()]:
            v = CUTE_VOICE
        else:
            v = pool.pop(0) if pool else narrator_voice
        m = re.match(r"^(.*?)([+-]\d+Hz)?$", v)
        cast[sp] = (m[1], m[2] or "+0Hz")
    return cast


def synthesize_dialogue(lines: list[tuple[str, str]], cast: dict, rate: str, out_wav: Path,
                        gap: float = 0.3) -> tuple[float, list[dict]]:
    """Speak each line with its speaker's voice and join them with short pauses.
    Returns (total seconds, [{speaker, text, start, end}])."""
    import wave

    parts, segs, t = [], [], 0.0
    for i, (sp, text) in enumerate(lines):
        part = out_wav.with_name(f"line{i:03d}.wav")
        voice, pitch = cast[sp]
        dur = synthesize(text, voice, rate, part, pitch)
        segs.append({"speaker": sp, "text": text, "start": round(t, 3), "end": round(t + dur, 3)})
        parts.append(part)
        t += dur + gap
    silence = b"\0\0" * int(48000 * gap)
    with wave.open(str(out_wav), "wb") as out:
        out.setnchannels(1), out.setsampwidth(2), out.setframerate(48000)
        for i, part in enumerate(parts):
            with wave.open(str(part), "rb") as w:
                out.writeframes(w.readframes(w.getnframes()))
            if i < len(parts) - 1:
                out.writeframes(silence)
            part.unlink()
    return probe_duration(out_wav), segs


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def script_words(text: str) -> list[str]:
    """Words as they should appear on screen (keeps the script's punctuation and spelling)."""
    return [w for w in text.split() if norm(w)]


def transcribe(wav: Path, model_name: str) -> list[dict]:
    from faster_whisper import WhisperModel

    local = ROOT / "data" / "models" / model_name
    model = WhisperModel(str(local) if (local / "model.bin").exists() else model_name,
                         device="cpu", compute_type="int8",
                         download_root=str(ROOT / "data" / "models"))
    lang = "en" if model_name.endswith(".en") else None
    segments, _ = model.transcribe(str(wav), word_timestamps=True, language=lang, beam_size=5)
    return [{"text": w.word.strip(), "start": w.start, "end": w.end}
            for seg in segments for w in (seg.words or []) if norm(w.word)]


def align(script: list[str], heard: list[dict], duration: float) -> tuple[list[dict], int]:
    """Give every script word a start/end using Whisper's timings.

    Whisper may spell things differently ("1,000" vs "one thousand"), so we diff the two word
    lists, copy times for matching words, spread time across replaced spans, and interpolate
    anything left over. Returns (timed words, number of exact matches).
    """
    times: list[list[float] | None] = [None] * len(script)
    matched = 0
    ops = SequenceMatcher(None, [norm(w) for w in script], [norm(h["text"]) for h in heard],
                          autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = [heard[j1 + k]["start"], heard[j1 + k]["end"]]
            matched += i2 - i1
        elif tag == "replace":
            t0, t1 = heard[j1]["start"], heard[j2 - 1]["end"]
            step = (t1 - t0) / (i2 - i1)
            for k in range(i2 - i1):
                times[i1 + k] = [t0 + k * step, t0 + (k + 1) * step]

    # Fill words Whisper skipped by splitting the gap between their known neighbours.
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else 0.0
        hi = times[j][0] if j < len(times) else duration
        step = max(hi - lo, 0.0) / (j - i)
        for k in range(i, j):
            times[k] = [lo + (k - i) * step, lo + (k - i + 1) * step]
        i = j

    out, prev = [], 0.0
    for word, (s, e) in zip(script, times):
        s = max(s, prev)               # keep timings monotonic
        e = max(e, s + 0.05)
        out.append({"text": word, "start": round(s, 3), "end": round(e, 3)})
        prev = s
    return out, matched
