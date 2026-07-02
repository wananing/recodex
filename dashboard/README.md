# recodex dashboard

Vite + React dashboard for the recodex AI coding workflow profiler.

It is the primary UI for importing real AI coding sessions, configuring an LLM provider,
generating the efficiency profiling report, and reviewing reusable project-memory suggestions.
Serve it through the recodex backend for the maintained API contract:

- `POST /import/run`
- `POST /watch/add`
- `POST /watch/run`
- `POST /reports/generate`
- `GET /overview`
- `GET /sessions`
- `GET /reports`
- `GET /llm/providers`

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
