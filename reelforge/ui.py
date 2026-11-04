"""Small local web app (127.0.0.1 only): paste a script, press Make, watch the reel."""
import base64
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import ROOT, load_config, pexels_key
from .pipeline import SAMPLE_SCRIPT, find_music, make

OUT = ROOT / "output"
VOICES = ["en-US-AndrewMultilingualNeural", "en-US-AvaMultilingualNeural", "en-US-GuyNeural",
          "en-US-JennyNeural", "en-GB-RyanNeural", "en-GB-SoniaNeural", "en-IN-PrabhatNeural",
          "en-IN-NeerjaNeural", "en-AU-WilliamMultilingualNeural"]
job = {"running": False, "log": [], "file": None, "error": None}
lock = threading.Lock()


def run_job(req: dict) -> None:
    try:
        image = None
        if req.get("bg") == "image":
            raw = base64.b64decode(req["image"].split(",", 1)[-1])
            image = ROOT / "data" / "ui_image.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(raw)
        name = f"reel-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        cfg = load_config()
        cfg["captions"].update(style=req["style"], words_per_group=int(req["wpg"]),
                               highlight_color=req["hl"])
        cfg["background"]["art_style"] = req.get("art", "cartoon")
        if not req.get("music"):
            cfg["music"]["folder"] = "__none__"
        make(req["script"], OUT / name, bg=req["bg"], topic=req.get("topic", ""),
             image=str(image) if image else None, voice_name=req["voice"], cfg=cfg,
             log=lambda s: job["log"].append(s))
        job["file"] = name
    except Exception as e:
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["running"] = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj).encode(), "application/json", code)

    def do_GET(self):
        if self.path == "/":
            return self._send(PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/info":
            files = sorted(OUT.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
            reels = [p.name for p in files]
            music = find_music(load_config(), None)
            return self._json({"pexels": bool(pexels_key()), "music": music.name if music else None,
                               "voices": VOICES, "reels": reels[:12], "sample": SAMPLE_SCRIPT})
        if self.path == "/api/status":
            return self._json(job)
        m = re.fullmatch(r"/out/([\w.-]+\.mp4)", self.path)
        if m and (OUT / m[1]).exists():
            return self._video(OUT / m[1])
        self._send(b"not found", "text/plain", 404)

    def _video(self, path: Path):
        """Serve with HTTP Range support so the browser player can seek."""
        size = path.stat().st_size
        rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        start, end = 0, size - 1
        if rng:
            start = int(rng[1]) if rng[1] else max(size - int(rng[2] or 0), 0)
            end = int(rng[2]) if rng[1] and rng[2] else size - 1
        with open(path, "rb") as f:
            f.seek(start)
            body = f.read(end - start + 1)
        extra = {"Accept-Ranges": "bytes"}
        if rng:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._send(body, "video/mp4", 206 if rng else 200, extra)

    def do_POST(self):
        if self.path != "/api/make":
            return self._send(b"not found", "text/plain", 404)
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if not req.get("script", "").strip():
            return self._json({"error": "Paste a script first."}, 400)
        if req.get("bg") == "image" and not req.get("image"):
            return self._json({"error": "Choose a picture for the image background."}, 400)
        with lock:
            if job["running"]:
                return self._json({"error": "A reel is already being made."}, 409)
            job.update(running=True, log=[], file=None, error=None)
        threading.Thread(target=run_job, args=(req,), daemon=True).start()
        self._json({"ok": True})


def serve(port: int = 8770) -> None:
    url = f"http://127.0.0.1:{port}"
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"ReelForge UI running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ReelForge</title>
<style>
:root{--bg:#0d0f14;--panel:#161a22;--line:#262c38;--text:#e8ebf1;--mute:#8b93a5;--acc:#ffd60a;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,Segoe UI,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:baseline}
h1{margin:0;font-size:20px}h1 span{color:var(--acc)}header p{margin:0;color:var(--mute);font-size:13px}
main{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:20px;padding:20px 24px;max-width:1200px;margin:auto}
@media(max-width:860px){main{grid-template-columns:1fr;padding:16px}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
label{display:block;font-size:12px;color:var(--mute);margin:12px 0 4px;text-transform:uppercase;letter-spacing:.04em}
textarea,input,select{width:100%;background:#0f1218;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px;font:inherit}
textarea{min-height:190px;resize:vertical}.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0 12px}
input[type=color]{height:40px;padding:3px}.chk{display:flex;gap:8px;align-items:center;margin-top:12px;color:var(--mute)}.chk input{width:auto}
button{background:var(--acc);color:#111;border:0;border-radius:8px;padding:11px 18px;font-weight:700;font:inherit;font-weight:700;cursor:pointer;margin-top:16px}
button.ghost{background:transparent;color:var(--mute);border:1px solid var(--line);font-weight:400}button:disabled{opacity:.5;cursor:wait}
#log{background:#0a0c10;border-radius:8px;padding:10px;font:12.5px/1.5 Consolas,monospace;color:#b9c1d1;min-height:110px;max-height:240px;overflow:auto;white-space:pre-wrap;margin-top:14px}
.phone{aspect-ratio:9/16;background:#000;border-radius:14px;overflow:hidden;display:grid;place-items:center;color:var(--mute);font-size:13px;text-align:center}
video{width:100%;height:100%;display:block}.hint{color:var(--mute);font-size:13px;margin-top:6px}.err{color:var(--bad)}
#reels a{display:block;color:var(--text);font-size:13px;padding:5px 0;border-bottom:1px solid var(--line);text-decoration:none;cursor:pointer}
#reels a:hover{color:var(--acc)}a.dl{color:var(--acc);font-size:13px;display:inline-block;margin-top:8px}
</style></head><body>
<header><h1>Reel<span>Forge</span></h1><p>script in, vertical reel out &middot; runs on this computer</p></header>
<main><section class="card">
<label for="script">Script</label><textarea id="script" placeholder="Paste what the voice should say..."></textarea>
<div class="row"><div><label for="topic">Topic (optional)</label><input id="topic" placeholder="e.g. morning habits"></div>
<div><label for="voice">Voice</label><select id="voice"></select></div></div>
<div class="row"><div><label for="bg">Background</label><select id="bg">
<option value="auto">Auto: stock video, or AI pictures</option><option value="ai">AI pictures per line (free, slow)</option><option value="pexels">Pexels stock video</option>
<option value="gradient">Animated gradient (fast, offline)</option><option value="image">Pan &amp; zoom on my picture</option></select></div>
<div><label for="style">Caption style</label><select id="style"><option value="highlight">Word-by-word highlight</option><option value="pop">Phrase pop-in</option></select></div></div>
<div class="row"><div><label for="art">Picture style (AI pictures)</label><select id="art"><option value="cartoon">3D cartoon (Pixar-style)</option><option value="photo">Realistic photo</option></select></div><div></div></div>
<div class="row"><div><label for="wpg">Words per caption</label><input id="wpg" type="number" min="1" max="8" value="3"></div>
<div><label for="hl">Highlight colour</label><input id="hl" type="color" value="#ffd60a"></div></div>
<div id="imgrow" hidden><label for="img">Picture</label><input id="img" type="file" accept="image/*"></div>
<div class="chk"><input id="music" type="checkbox" checked><span id="musiclbl">Background music</span></div>
<p class="hint" id="bghint"></p>
<button id="go">Make reel</button> <button class="ghost" id="sample">Use sample script</button>
<div id="log">Ready.</div></section>
<aside class="card"><div class="phone" id="phone">Your reel will appear here</div><div id="dl"></div>
<label>Recent reels</label><div id="reels"></div></aside></main>
<script>
const $=id=>document.getElementById(id);let info={};
function show(name){$('phone').innerHTML=`<video controls autoplay playsinline src="/out/${name}"></video>`;
 $('dl').innerHTML=`<a class="dl" href="/out/${name}" download>Download ${name}</a>`}
async function load(){info=await (await fetch('/api/info')).json();
 $('voice').innerHTML=info.voices.map(v=>`<option>${v}</option>`).join('');
 $('musiclbl').textContent=info.music?`Background music (${info.music}, ducked under voice)`:'Background music (none found: drop an .mp3 in assets/music/)';
 $('music').disabled=!info.music;if(!info.music)$('music').checked=false;
 $('reels').innerHTML=info.reels.map(r=>`<a data-r="${r}">${r}</a>`).join('')||'<p class="hint">None yet.</p>';
 document.querySelectorAll('#reels a').forEach(a=>a.onclick=()=>show(a.dataset.r));hint()}
function hint(){const b=$('bg').value;$('imgrow').hidden=b!=='image';
 const ai='Free AI pictures are limited to about one every 45 s, so a 30 s reel takes ~6 min.';
 $('bghint').textContent=(b==='auto'||b==='pexels')?(info.pexels?'Pexels key found: real stock video per line.':'No Pexels key in .env. '+ai):b==='ai'?ai:''}
$('bg').onchange=hint;$('sample').onclick=()=>{$('script').value=info.sample;$('topic').value='morning habits'};
const readImg=f=>new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result);fr.readAsDataURL(f)});
$('go').onclick=async()=>{const body={script:$('script').value,topic:$('topic').value,voice:$('voice').value,bg:$('bg').value,
 style:$('style').value,art:$('art').value,wpg:$('wpg').value,hl:$('hl').value,music:$('music').checked};
 if(body.bg==='image'&&$('img').files[0])body.image=await readImg($('img').files[0]);
 const r=await fetch('/api/make',{method:'POST',body:JSON.stringify(body)});const j=await r.json();
 if(j.error){$('log').innerHTML=`<span class="err">${j.error}</span>`;return}
 $('go').disabled=true;$('log').textContent='Starting...';poll()};
async function poll(){const s=await (await fetch('/api/status')).json();
 $('log').textContent=s.log.join('\n')||'Starting...';$('log').scrollTop=1e9;
 if(s.running)return setTimeout(poll,800);$('go').disabled=false;
 if(s.error)$('log').innerHTML+=`\n<span class="err">${s.error.replace(/</g,'&lt;')}</span>`;
 if(s.file){show(s.file);load()}}
load();
</script></body></html>"""
