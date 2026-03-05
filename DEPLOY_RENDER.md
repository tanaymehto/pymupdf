# Deploy on Render (PyMuPDF-only)

## What this is
- `render.yaml` tells Render how to build and run your app.
- You do not need to write commands manually after this.

## Steps
1. Push this repo to GitHub.
2. In Render: `New` -> `Blueprint`.
3. Connect your GitHub repo.
4. Render will detect `render.yaml` and create service `causelist-pymupdf`.
5. Click `Apply`.
6. Wait for build + deploy to finish.

## App settings already configured
- Root folder: `project`
- Install deps: `pip install -r requirements.txt`
- Start server: `gunicorn -w 2 -k gthread -t 180 --bind 0.0.0.0:$PORT app:app`
- Health check: `/`
- Python version: `3.11.9`

## After deploy
- Open the Render URL.
- Upload a PDF.
- Select court (Madras/Bombay).
- Run extraction.

## If build fails
- Check Render logs for missing dependency.
- Confirm repo contains:
  - `render.yaml` at repo root
  - `project/requirements.txt`
  - `project/app.py`
