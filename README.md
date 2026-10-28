# DocuAuto Hindi — deployable documentary generator

A mobile-friendly personal web app that generates a Hindi documentary package: AI script, Hindi voice-over, stock-video montage, subtitles, final MP4, and YouTube description. Supports 16:9 landscape and 9:16 vertical output up to 10 minutes.

## Important scope and limitations

- This is a deployable application codebase, not a hosted live website. It must be deployed to a server with FFmpeg and configured API credentials.
- Gemini API is used for script generation; a Google AI Studio API key is required. Free quota/availability can change and is not guaranteed.
- Pexels API is used for stock video footage; a Pexels API key is required. It uses stock clips, not AI-generated original footage. Respect Pexels terms and attribution/licensing requirements.
- Hindi narration uses Edge TTS (`hi-IN-SwaraNeural`) over the internet; provider availability can change.
- YouTube upload is intentionally excluded because the user will upload the finished MP4 manually.
- The research script includes suggested sources to verify, but it is not a substitute for fact-checking. No system can guarantee zero errors.
- Background jobs are stored in process memory. For serious multi-user/production scale, add a persistent job queue/database and authentication. This single-user app should be deployed privately.

## Deploy with Docker

1. Install Docker on a server/VPS that can run FFmpeg.
2. Copy `.env.example` to `.env` and set `GEMINI_API_KEY` and `PEXELS_API_KEY`.
3. Run:

```bash
docker build -t docuauto-hindi .
docker run -d --name docuauto -p 8000:8000 --env-file .env -v docuauto_outputs:/app/outputs docuauto-hindi
```

4. Open `http://YOUR_SERVER_IP:8000`. Use HTTPS and authentication before exposing it publicly.

## Local run

Requires Python 3.11+, FFmpeg and ffprobe.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env  # edit .env with API keys
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API
- `GET /api/health`
- `POST /api/create` JSON: `{"topic":"...", "format":"16:9", "duration_minutes":5}`
- `GET /api/status/{job_id}`
- `GET /download/{job_id}` (MP4)
- `GET /download/{job_id}?file=script.txt|captions.srt|description.txt|script.json`

## Security before public deployment

Keep API keys in environment variables, not browser code. Add login/authentication, HTTPS, rate limiting, persistent queue, cleanup policy, and resource limits before public internet exposure. Do not commit `.env` or API keys to source control.
