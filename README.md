# Synapse

Synapse captures browsing context from the Chrome extension, sends page snapshots to the local backend, and renders the resulting comparison session in the frontend.

## Run locally

### 1. Start the backend

From the repo root:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8010 --reload
```

The backend serves:

- `GET /health`
- extension endpoints under `/api/v1/extension/*`
- session synthesis endpoints under `/api/v1/session/*`

### 2. Start the frontend

From `frontend/`:

```powershell
npm install
npm run dev
```

Open the Vite URL shown in the terminal. The app is expected to run on `http://localhost:5173`.

### 3. Load the extension

In Chrome:

1. Open `chrome://extensions`
2. Enable `Developer mode`
3. Click `Load unpacked`
4. Select the `extension/` folder

The extension popup opens Synapse in the frontend and syncs captured snapshots to the backend.

## Typical workflow

1. Start the backend
2. Start the frontend
3. Reload the unpacked extension after extension code changes
4. Browse the pages you want to compare
5. Open the extension popup and click `Run`
6. Use the constraint box in the frontend for follow-up filtering like `Only under $90`

## Notes

- Backend default URL: `http://127.0.0.1:8010`
- Frontend default URL: `http://localhost:5173`
- The frontend can re-apply constraints without re-running the full synthesis flow
- Some model providers reject structured `response_format`; the backend falls back automatically when needed

## Quick checks

```powershell
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/api/v1/extension/history/stats
```
