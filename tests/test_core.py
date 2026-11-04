from reelforge import captions, visuals, voice
from reelforge.config import load_config


def w(text, s, e):
    return {"text": text, "start": s, "end": e}


def test_align_keeps_script_spelling_and_fills_gaps():
    script = voice.script_words("It costs $1,000 today, honestly.")
    heard = [w("It", 0.0, 0.2), w("costs", 0.2, 0.5), w("one", 0.5, 0.7),
             w("thousand", 0.7, 1.0), w("dollars", 1.0, 1.3), w("today", 1.3, 1.6)]
    words, matched = voice.align(script, heard, duration=2.4)
    assert [x["text"] for x in words] == ["It", "costs", "$1,000", "today,", "honestly."]
    assert matched == 3
    assert words[2]["start"] == 0.5 and words[2]["end"] == 1.3      # replaced span
    assert 1.6 <= words[4]["start"] < words[4]["end"] <= 2.4        # unheard word interpolated
    starts = [x["start"] for x in words]
    assert starts == sorted(starts)


def test_caption_groups_break_on_size_and_punctuation():
    ws = [w(t, i, i + 1) for i, t in enumerate("one two, three four five six".split())]
    groups = captions.group_words(ws, 3)
    assert [[x["text"] for x in g] for g in groups] == [["one", "two,"], ["three", "four", "five"], ["six"]]


def test_highlight_timeline_covers_each_word_without_overlap():
    ws = [w("a", 0.0, 0.3), w("b", 0.3, 0.6), w("c", 0.6, 0.9), w("d", 2.5, 2.8)]
    items = captions.timeline(captions.group_words(ws, 3), "highlight", total=3.2)
    assert [(i[2], i[3]) for i in items] == [(0, 0), (0, 1), (0, 2), (1, 0)]
    assert items[2][1] == 1.2          # long pause: phrase clears 0.3s after its last word
    for a, b in zip(items, items[1:]):
        assert a[1] <= b[0]


def test_beats_tile_the_timeline_and_keywords_skip_stopwords():
    ws = [w("Paris", 0.2, 0.6), w("hides", 0.6, 0.9), w("tunnels.", 0.9, 1.4),
          w("Bones", 1.6, 2.0), w("everywhere.", 2.0, 2.6)]
    bs = visuals.beats(ws, total=3.0, max_len=6)
    assert [(b["start"], b["end"]) for b in bs] == [(0.0, 1.6), (1.6, 3.0)]
    assert visuals.keywords("They were really in the ancient catacombs of Paris") == ["catacombs", "ancient"]


def test_caption_frame_is_full_size_and_transparent(tmp_path):
    cfg = load_config()
    ws = [w("Hello", 0.0, 0.4), w("world.", 0.4, 0.9)]
    lst, phrases = captions.build(ws, cfg["captions"], 540, 960, 30, 1.2, tmp_path)
    assert phrases == 1
    body = lst.read_text()
    assert body.count("duration") >= 3
    from PIL import Image
    img = Image.open(tmp_path / "c00002.png")
    assert img.size == (540, 960) and img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0 and img.getbbox() is not None


def test_scene_plan_falls_back_to_keywords_without_ollama():
    cfg = {"director_model": "llama3.2:3b", "ollama_url": "http://127.0.0.1:9"}  # nothing listens
    logs = []
    scenes = visuals.plan_scenes(["Glowing fish live in the deepest trenches."], "ocean", cfg,
                                 logs.append)
    assert scenes == [{"search": "trenches glowing", "image": "trenches glowing ocean"}]
    assert "using keywords" in logs[0]


def test_claude_prompt_numbers_one_scene_per_sentence():
    from reelforge.animate import claude_prompt, sentences
    script = "A robot woke up. It looked around! Then it flew home"
    assert sentences(script) == ["A robot woke up.", "It looked around!", "Then it flew home"]
    prompt = claude_prompt(script, "space")
    assert "1. A robot woke up.\n2. It looked around!\n3. Then it flew home" in prompt
    assert "window.renderFrame" in prompt and "REEL_READY" in prompt
