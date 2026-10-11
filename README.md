# CineMusic FFmpeg Renderer

A starter FFmpeg rendering API for CineMusic AI. It concatenates uploaded video clips in the order received and adds the uploaded original audio as the master soundtrack.

## Deploy on Render

1. Put these files in a GitHub repository.
2. In Render, choose **New → Web Service** and connect the repository.
3. Select **Docker** runtime. Render will build the included Dockerfile.
4. In Environment, set `RENDER_API_TOKEN` to a long random secret. If you use `render.yaml`, Render can generate one for you.
5. Deploy and wait for the service to become live.
6. Open `https://YOUR-SERVICE.onrender.com/health`. It should return `"ok": true`.
7. Copy the service base URL, e.g. `https://your-service.onrender.com`, into CineMusic AI's `FFMPEG_SERVICE_URL` setting.
8. Configure CineMusic AI to send `Authorization: Bearer YOUR_RENDER_API_TOKEN` when calling `/render`, and submit multipart form data with repeated `clips` files, one `audio` file, and `aspect_ratio`.

## API

- `GET /health` — checks FFmpeg availability.
- `POST /render` — multipart form:
  - `clips`: repeat once per video clip, in timeline order
  - `audio`: original audio file
  - `aspect_ratio`: `9:16`, `16:9`, or `1:1`
  - Header: `Authorization: Bearer YOUR_RENDER_API_TOKEN`

Successful `/render` returns an MP4 response.

## Important production notes

This starter performs a synchronous render and returns the MP4 directly. For long full-length videos, use a background queue and object storage (S3-compatible or equivalent), persist job status, and return a job ID with a download URL. Configure CORS to your app's exact origin when possible. Keep the token private and only call this endpoint from your backend. Rendering is CPU-intensive and hosting may cost money. Do not expose this service publicly without authentication and rate limits.
