from __future__ import annotations
import asyncio, json, os, re, shutil, subprocess, threading, time, uuid
from pathlib import Path
from typing import Optional
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(ROOT / "outputs"))).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="DocuAuto Hindi", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()

class CreateRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=180)
    format: str = Field(default="16:9", pattern="^(16:9|9:16)$")
    duration_minutes: int = Field(default=5, ge=1, le=10)
    style: str = Field(default="cinematic", max_length=40)


def set_job(job_id: str, **kwargs):
    with LOCK:
        JOBS.setdefault(job_id, {}).update(kwargs)


def gemini_script(topic: str, minutes: int) -> dict:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Gemini API key missing. Add GEMINI_API_KEY in your hosting environment.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    prompt = f'''Create a fact-based Hindi documentary script for topic: {topic!r}. Target length: about {minutes} minutes, roughly {minutes*115} Hindi words. Use Devanagari Hindi, a strong but not misleading opening, clear narration, scene-by-scene visual search cues, and a thoughtful ending. Do not invent facts, statistics, quotes, or sources. If a fact is uncertain, phrase it cautiously. Return ONLY valid JSON with keys: title (string), description (string), narration (string), scenes (array of objects with narration (string) and visual_query (short English search query)), sources_to_verify (array of strings). The narration must be one coherent voiceover and scenes should cover it in order. Avoid copyrighted lyrics and do not imitate a living creator.'''
    r = requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json","temperature":0.55}}, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini API error ({r.status_code}): {r.text[:500]}")
    data = r.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text)
        if not result.get("narration") or not result.get("scenes"):
            raise ValueError("AI response missing narration or scenes")
        return result
    except Exception as e:
        raise RuntimeError(f"Could not parse AI script response: {e}")


def pexels_search(query: str, api_key: str) -> list[str]:
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": api_key}, params={"query":query[:100],"per_page":8,"orientation":"landscape"}, timeout=30)
        if r.status_code >= 400:
            return []
        data = r.json()
        urls=[]
        for video in data.get("videos", []):
            files=sorted(video.get("video_files", []), key=lambda x: (x.get("width",0), x.get("height",0)))
            suitable=[f for f in files if f.get("file_type") == "video/mp4" and f.get("width",0) >= 640]
            if suitable:
                urls.append(suitable[-1]["link"])
        return urls
    except Exception:
        return []


def download_file(url: str, dest: Path):
    with requests.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1024*512):
                if chunk: f.write(chunk)


def run_cmd(args: list[str], timeout: int = 300):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-1600:])

async def make_voice(text: str, out: Path):
    import edge_tts
    communicate = edge_tts.Communicate(text, "hi-IN-SwaraNeural", rate="-5%")
    await communicate.save(str(out))


def srt_time(seconds: float) -> str:
    ms = int(seconds * 1000)
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def make_srt(narration: str, duration: float, path: Path):
    # Split at sentence boundaries, then wrap each card to keep captions readable.
    pieces = [x.strip() for x in re.split(r'(?<=[।!?])\s+', narration) if x.strip()]
    if not pieces: pieces=[narration]
    total_chars=sum(len(x) for x in pieces) or 1
    t=0.0; lines=[]
    for i, piece in enumerate(pieces):
        span=max(1.3, duration*len(piece)/total_chars)
        end=min(duration, t+span)
        # Keep each caption line short
        words=piece.split(); wrapped=[]; cur=""
        for word in words:
            if len(cur)+len(word)+1 > 42 and cur:
                wrapped.append(cur); cur=word
            else: cur=(cur+" "+word).strip()
        if cur: wrapped.append(cur)
        lines.append(f"{i+1}\n{srt_time(t)} --> {srt_time(max(t+0.5,end))}\n"+"\n".join(wrapped[:3])+"\n")
        t=end
    path.write_text("\n".join(lines), encoding="utf-8")


def generate_job(job_id: str, req: CreateRequest):
    work = OUTPUT_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    try:
        set_job(job_id, status="working", progress=5, message="Hindi documentary ki research aur script ban rahi hai…")
        script = gemini_script(req.topic, req.duration_minutes)
        (work/"script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
        (work/"script.txt").write_text(script["narration"], encoding="utf-8")
        set_job(job_id, progress=20, message="Hindi voice-over ban raha hai…", title=script.get("title", req.topic))
        audio = work/"voice.mp3"
        asyncio.run(make_voice(script["narration"], audio))
        # Get actual narration duration.
        probe = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(audio)], capture_output=True, text=True, timeout=30)
        duration = max(10.0, float(probe.stdout.strip()))
        pexels_key=os.getenv("PEXELS_API_KEY", "").strip()
        clips=[]
        scenes=script.get("scenes", [])[:12]
        set_job(job_id, progress=32, message="Documentary ke visuals dhoondhe ja rahe hain…")
        if pexels_key:
            for idx, scene in enumerate(scenes):
                candidates=pexels_search(scene.get("visual_query") or req.topic, pexels_key)
                if candidates:
                    clip=work/f"clip_{idx:02}.mp4"
                    try:
                        download_file(candidates[0], clip); clips.append(clip)
                    except Exception: pass
                set_job(job_id, progress=32+int(30*(idx+1)/max(1,len(scenes))), message=f"Visuals taiyar ho rahe hain… ({idx+1}/{len(scenes)})")
        if not clips:
            raise RuntimeError("Koi video visual download nahi hua. PEXELS_API_KEY set karein aur internet/API access check karein.")
        # Normalize clips to requested aspect ratio and concat.
        width,height=(1280,720) if req.format=="16:9" else (720,1280)
        normalized=[]
        for i, clip in enumerate(clips):
            out=work/f"norm_{i:02}.mp4"
            vf=f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=25,setsar=1"
            run_cmd(["ffmpeg","-y","-i",str(clip),"-t","12","-vf",vf,"-an","-c:v","libx264","-preset","veryfast","-crf","24","-pix_fmt","yuv420p",str(out)], timeout=180)
            normalized.append(out)
        concatfile=work/"concat.txt"
        concatfile.write_text("\n".join("file '"+p.name.replace("'","\\'")+"'" for p in normalized), encoding="utf-8")
        silent=work/"visuals.mp4"
        run_cmd(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concatfile),"-c","copy",str(silent)], timeout=180)
        # Loop visuals to narration duration and burn in captions if subtitle filter works.
        srt=work/"captions.srt"; make_srt(script["narration"], duration, srt)
        final=work/("documentary_16x9.mp4" if req.format=="16:9" else "documentary_9x16.mp4")
        subpath=str(srt).replace("\\","/").replace(":","\\:").replace("'","\\'")
        vf=f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},subtitles='{subpath}':force_style='FontName=DejaVu Sans,FontSize=22,Outline=2,Shadow=1,Alignment=2,MarginV=35'"
        try:
            run_cmd(["ffmpeg","-y","-stream_loop","-1","-i",str(silent),"-i",str(audio),"-t",str(duration),"-vf",vf,"-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","160k","-shortest","-movflags","+faststart",str(final)], timeout=900)
        except Exception:
            # Fallback without burned subtitles; SRT is still supplied separately.
            run_cmd(["ffmpeg","-y","-stream_loop","-1","-i",str(silent),"-i",str(audio),"-t",str(duration),"-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","160k","-shortest","-movflags","+faststart",str(final)], timeout=900)
        (work/"description.txt").write_text(script.get("description", ""), encoding="utf-8")
        set_job(job_id, status="complete", progress=100, message="Video ready hai! Download kar sakte ho.", video_url=f"/download/{job_id}", script_url=f"/download/{job_id}?file=script.txt", captions_url=f"/download/{job_id}?file=captions.srt", description_url=f"/download/{job_id}?file=description.txt", title=script.get("title", req.topic), duration=round(duration))
    except Exception as e:
        set_job(job_id, status="error", progress=100, message=str(e), error=str(e))

@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT/"app"/"templates"/"index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    return {"ok": True, "gemini_configured": bool(os.getenv("GEMINI_API_KEY")), "pexels_configured": bool(os.getenv("PEXELS_API_KEY")), "ffmpeg": shutil.which("ffmpeg") is not None}

@app.post("/api/create")
def create(req: CreateRequest, bg: BackgroundTasks):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise HTTPException(500, "FFmpeg server par install nahi hai.")
    job_id=uuid.uuid4().hex[:12]
    JOBS[job_id]={"status":"queued","progress":0,"message":"Queue mein hai…","topic":req.topic,"format":req.format}
    bg.add_task(generate_job, job_id, req)
    return {"job_id":job_id}

@app.get("/api/status/{job_id}")
def status(job_id: str):
    if job_id not in JOBS: raise HTTPException(404, "Job nahi mila")
    return JOBS[job_id]

@app.get("/download/{job_id}")
def download(job_id: str, file: Optional[str] = None):
    if job_id not in JOBS: raise HTTPException(404, "Job nahi mila")
    folder=OUTPUT_DIR/job_id
    if file:
        # Allow only known sidecar files, no path traversal.
        if file not in {"script.txt","script.json","captions.srt","description.txt"}: raise HTTPException(400,"Invalid file")
        path=folder/file
    else:
        path=folder/("documentary_16x9.mp4" if JOBS[job_id].get("format")=="16:9" else "documentary_9x16.mp4")
    if not path.exists(): raise HTTPException(404,"File abhi ready nahi hai")
    return FileResponse(path, filename=path.name)
