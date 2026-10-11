import os, tempfile, subprocess, secrets
from pathlib import Path
from typing import List
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="CineMusic FFmpeg Renderer", version="1.0.0")

# Configure these in your hosting dashboard, never in frontend code.
API_TOKEN = os.getenv("RENDER_API_TOKEN", "")
MAX_CLIPS = int(os.getenv("MAX_CLIPS", "40"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "500"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/tmp/cinemusic-jobs"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

allowed_origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins != ["*"] else ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

def check_auth(authorization: str | None):
    if not API_TOKEN:
        raise HTTPException(503, "Renderer is not configured: set RENDER_API_TOKEN.")
    expected = f"Bearer {API_TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Invalid renderer token.")

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3600)
    if p.returncode:
        raise RuntimeError((p.stderr or "FFmpeg failed")[-4000:])

@app.get("/")
def root():
    return {"service": "CineMusic FFmpeg Renderer", "status": "online", "health": "/health"}

@app.get("/health")
def health():
    try:
        p = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        ffmpeg_ok = p.returncode == 0
    except Exception:
        ffmpeg_ok = False
    return {"ok": ffmpeg_ok, "ffmpeg_available": ffmpeg_ok}

@app.post("/render")
async def render(
    clips: List[UploadFile] = File(..., description="Video clips in timeline order"),
    audio: UploadFile = File(..., description="Original audio file"),
    aspect_ratio: str = Form("9:16"),
    authorization: str | None = Header(default=None),
):
    check_auth(authorization)
    if not clips or len(clips) > MAX_CLIPS:
        raise HTTPException(400, f"Upload between 1 and {MAX_CLIPS} clips.")
    if aspect_ratio not in ("9:16", "16:9", "1:1"):
        raise HTTPException(400, "aspect_ratio must be 9:16, 16:9, or 1:1.")

    job_id = secrets.token_hex(12)
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    try:
        clip_paths = []
        total_bytes = 0
        for i, clip in enumerate(clips):
            suffix = Path(clip.filename or "").suffix.lower()
            if suffix not in (".mp4", ".mov", ".m4v", ".webm", ".mkv"):
                raise HTTPException(400, f"Unsupported video format for clip {i+1}.")
            dest = job_dir / f"clip_{i:03d}{suffix}"
            data = await clip.read()
            total_bytes += len(data)
            if total_bytes > MAX_FILE_MB * 1024 * 1024:
                raise HTTPException(413, f"Combined upload exceeds {MAX_FILE_MB} MB.")
            dest.write_bytes(data)
            clip_paths.append(dest)

        audio_suffix = Path(audio.filename or "").suffix.lower()
        if audio_suffix not in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"):
            raise HTTPException(400, "Unsupported audio format.")
        audio_path = job_dir / f"original_audio{audio_suffix}"
        audio_data = await audio.read()
        total_bytes += len(audio_data)
        if total_bytes > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"Combined upload exceeds {MAX_FILE_MB} MB.")
        audio_path.write_bytes(audio_data)

        dimensions = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
        width, height = dimensions[aspect_ratio]
        # Normalize all clips to identical size, frame rate, pixel format and timestamps.
        inputs = []
        filters = []
        for i, path in enumerate(clip_paths):
            inputs += ["-i", str(path)]
            filters.append(
                f"[{i}:v]setpts=PTS-STARTPTS,"
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,fps=30,format=yuv420p[v{i}]"
            )
        joined = "".join(f"[v{i}]" for i in range(len(clip_paths)))
        filters.append(f"{joined}concat=n={len(clip_paths)}:v=1:a=0[vout]")
        audio_input_index = len(clip_paths)
        inputs += ["-i", str(audio_path)]
        output = job_dir / "final_music_video.mp4"
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", f"{audio_input_index}:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-t", "3600", "-movflags", "+faststart", str(output)
        ]
        run(cmd)
        # Preserve original audio content as the master soundtrack; output duration follows audio.
        # Re-mux to exact source duration and retain AAC compatibility.
        duration_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)]
        probe = subprocess.run(duration_cmd, capture_output=True, text=True, timeout=30)
        if probe.returncode == 0:
            try:
                duration = float(probe.stdout.strip())
                temp_output = job_dir / "final_exact.mp4"
                run(["ffmpeg", "-y", "-i", str(output), "-i", str(audio_path),
                     "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                     "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.3f}",
                     "-movflags", "+faststart", str(temp_output)])
                output = temp_output
            except Exception:
                pass
        return FileResponse(
            str(output), media_type="video/mp4",
            filename="cinemusic-final.mp4",
            headers={"X-Render-Job-ID": job_id}
        )
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Rendering exceeded the worker time limit. Try fewer or shorter clips.")
    except Exception as exc:
        raise HTTPException(500, f"Rendering failed: {str(exc)[:1000]}")
    finally:
        # Response file delivery may still be in progress, so deployment should use object storage
        # for production jobs. This starter retains files in /tmp until container cleanup.
        pass
