"""Voiceover (edge-tts) and word timing (faster-whisper aligned back to the script)."""
import asyncio
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

from .config import ROOT
from .ffmpeg import ffmpeg, probe_duration


def synthesize(text: str, voice: str, rate: str, out_wav: Path) -> float:
    """Speak `text` with an edge-tts neural voice. Returns the audio length in seconds."""
    import edge_tts

    mp3 = out_wav.with_suffix(".mp3")
    # The free edge-tts service sometimes drops a request ("NoAudioReceived"); retrying works.
    for attempt in range(4):
        try:
            asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(mp3)))
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
