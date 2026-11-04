"""Word-synced captions: group words, draw each caption state with Pillow, and write an
ffmpeg concat list so all states play back as one transparent overlay stream."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\seguibl.ttf", r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
]
POP_SCALES = (0.82, 0.94)   # first frames of each new phrase, for a small "pop" in
BREAKS = ".!?,;:"


def group_words(words: list[dict], per_group: int) -> list[list[dict]]:
    groups, cur = [], []
    for w in words:
        if cur and w.get("speaker") != cur[-1].get("speaker"):  # never mix two speakers
            groups.append(cur)
            cur = []
        cur.append(w)
        if len(cur) >= per_group or w["text"][-1] in BREAKS:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def timeline(groups: list[list[dict]], style: str, total: float) -> list[tuple]:
    """List of (start, end, group_index, highlighted_word_index or None)."""
    items = []
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else total
        # Hold the phrase until the next one starts, unless there is a long pause.
        end = nxt if nxt - g[-1]["end"] < 0.7 else g[-1]["end"] + 0.3
        end = min(end, total)
        if style == "pop":
            items.append((g[0]["start"], end, gi, None))
            continue
        for wi, w in enumerate(g):
            s = g[0]["start"] if wi == 0 else w["start"]
            e = g[wi + 1]["start"] if wi + 1 < len(g) else end
            items.append((s, e, gi, wi))
    return items


class Renderer:
    def __init__(self, cfg: dict, width: int, height: int):
        self.c, self.w, self.h = cfg, width, height
        path = cfg.get("font") or next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
        self.font = (ImageFont.truetype(path, cfg["size"]) if path
                     else ImageFont.load_default(cfg["size"]))

    def _band(self, words: list[str], hi: int | None, hl: str | None = None) -> Image.Image:
        """Draw the phrase into a tight transparent image, wrapping to ~86% of frame width."""
        c, font, sw = self.c, self.font, self.c["stroke_width"]
        space = font.getlength(" ")
        lines, line, width = [], [], 0.0
        for i, word in enumerate(words):
            wl = font.getlength(word)
            if line and width + space + wl > self.w * 0.86:
                lines.append(line)
                line, width = [], 0.0
            width += (space if line else 0) + wl
            line.append((i, word, wl))
        lines.append(line)

        asc, desc = font.getmetrics()
        lh = asc + desc + 12
        pad = sw + 10
        img = Image.new("RGBA", (self.w, lh * len(lines) + pad * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for li, ln in enumerate(lines):
            lw = sum(x[2] for x in ln) + space * (len(ln) - 1)
            x, y = (self.w - lw) / 2, pad + li * lh
            for i, word, wl in ln:
                color = (hl or c["highlight_color"]) if i == hi else c["color"]
                d.text((x + 4, y + 6), word, font=font, fill=(0, 0, 0, 140),
                       stroke_width=sw, stroke_fill=(0, 0, 0, 140))           # soft shadow
                d.text((x, y), word, font=font, fill=color,
                       stroke_width=sw, stroke_fill=c["stroke_color"])
                x += wl + space
        return img

    def frame(self, words: list[str], hi: int | None, scale: float = 1.0,
              hl: str | None = None) -> Image.Image:
        band = self._band(words, hi, hl)
        if scale != 1.0:
            band = band.resize((max(1, int(band.width * scale)), max(1, int(band.height * scale))),
                               Image.LANCZOS)
        canvas = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        cy = int(self.h * self.c["position"])
        top = min(max(cy - band.height // 2, 0), self.h - band.height)
        canvas.alpha_composite(band, ((self.w - band.width) // 2, top))
        return canvas


def build(words: list[dict], cfg: dict, width: int, height: int, fps: int,
          total: float, workdir: Path) -> tuple[Path, int]:
    """Render caption images and a concat list. Returns (list path, number of phrases)."""
    workdir.mkdir(parents=True, exist_ok=True)
    groups = group_words(words, max(1, int(cfg["words_per_group"])))
    items = timeline(groups, cfg["style"], total)
    r = Renderer(cfg, width, height)
    frame_t = 1.0 / fps
    blank = workdir / "blank.png"
    Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(blank)

    entries: list[tuple[Path, float]] = []
    cursor, n = 0.0, 0

    def add(path: Path, dur: float):
        nonlocal cursor
        if dur > 0.001:
            entries.append((path, dur))
            cursor += dur

    for start, end, gi, wi in items:
        texts = [w["text"].upper() if cfg["uppercase"] else w["text"] for w in groups[gi]]
        hl = groups[gi][0].get("hl")
        start = max(start, cursor)
        add(blank, start - cursor)
        if wi in (0, None):  # phrase just appeared: a couple of growing frames first
            for s in POP_SCALES:
                if end - cursor > 3 * frame_t:
                    p = workdir / f"c{n:05d}.png"
                    r.frame(texts, wi, s, hl).save(p)
                    n += 1
                    add(p, frame_t)
        p = workdir / f"c{n:05d}.png"
        r.frame(texts, wi, hl=hl).save(p)
        n += 1
        add(p, end - cursor)
    add(blank, total - cursor)

    lst = workdir / "captions.txt"
    lines = [f"file '{p.as_posix()}'\nduration {d:.4f}" for p, d in entries]
    lines.append(f"file '{entries[-1][0].as_posix()}'")   # concat demuxer needs the last file twice
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst, len(groups)
