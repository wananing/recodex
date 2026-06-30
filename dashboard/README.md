# recodex dashboard

Vite + React dashboard copied from ContextSeek and adapted for recodex v0.2.0.

It is a frontend shell for the recodex import, watch, session, and skill export
workflows. The current recodex backend is still CLI-first, so the dashboard uses
the planned local API contract:

- `POST /import/run`
- `POST /watch/add`
- `POST /watch/run`
- `POST /skills/export`
- `GET /overview`
- `GET /sessions`
- `GET /settings`

## Development

```bash
npm install
npm run build
```

Preview the production build without the recodex API:

```bash
npm run preview
```

Serve the built dashboard with the recodex API:

```bash
cd ..
make dashboard-serve
```

The Vite dev proxy sends dashboard API calls to `http://127.0.0.1:8000`.
Set `VITE_RECODEX_API_BASE` when the API is hosted elsewhere.
