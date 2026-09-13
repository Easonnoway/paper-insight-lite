# Paper Insight Lite

A single-user, local-first paper library — a minimal edition trimmed from [paper_online](https://github.com/KMnO4-zx/paper_online).

## Features

- **My Papers** — a personal reading list with per-paper records (viewed / liked / favorited, plus a passive "recently opened" tracker) and a reading-activity heatmap.
- **Multiple ways to add papers** — paste an arXiv link or ID, or enter papers manually (for sources arXiv doesn't cover: older venues, book chapters, other indexes).
- **AI analysis** — streaming per-paper analysis (full text when a PDF is available, metadata-based screening otherwise), Chinese abstract translation, and chat-with-the-paper.
- **Zotero-style category tree** — nested categories with per-category colors, drag-and-drop re-parenting, and right-click management. Assigning a nested category implicitly assigns its whole ancestor chain.
- **Single-paper AI auto-categorization** — one click assigns a paper to up to 3 of your existing categories.
- **Per-paper notes** — one editable note per paper with LaTeX rendering (`$...$` inline, `$$...$$` display math), shown on cards and the detail page.
- **Library export / import** — share your library (papers, categories, marks, notes, AI analyses) as a JSON file; import merges without overwriting your local content.
- **Automatic backups** — periodic full exports to a folder of your choice (daily / every 3 days / weekly, keeps the latest 10 files), with restart catch-up and a manual "back up now".
- **Open in AI** — hand off the paper to Kimi / ChatGPT / Gemini / Dola with a pre-built prompt.

No login (a single user is pinned by `admin.email` in `config.yaml`), no multi-tenant features.

## Tech Stack

- **Backend**: Python · FastAPI · psycopg (raw SQL) · PostgreSQL
- **Frontend**: React 19 · TypeScript · Vite · Tailwind CSS · shadcn/ui
- **AI**: any OpenAI-compatible provider (DeepSeek, OpenAI, SiliconFlow, OpenRouter, StepFun, Ark, …)

## Getting Started

### Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- PostgreSQL 14+

### Setup

```bash
# 1. Install dependencies
uv sync                     # backend (repo root)
cd frontend-react && npm ci # frontend

# 2. Configure
cp config.yaml.example config.yaml
#   - set database.url to your PostgreSQL database
#   - set admin.email (pins the single user)
#   - optionally fill an LLM api_key under `llm:`

# 3. Run the backend (port 18472; migrations run automatically on startup)
cd backend && uvicorn app:app --host 127.0.0.1 --port 18472

# 4. Open the app
#    Either build the frontend and let the backend serve it:
cd frontend-react && npm run build   # → http://127.0.0.1:18472
#    Or run the Vite dev server (proxies API calls to 18472):
cd frontend-react && npm run dev     # → http://localhost:5173
```

### LLM configuration

Fill any provider's `api_key` under the `llm:` section of `config.yaml`; only providers with a key are seeded at startup. The active provider/model can then be switched in the UI (top-right badge) — the bootstrap never overrides an existing choice.

## Testing

```bash
cd backend && python -m pytest ../tests/ -q   # backend (125 tests)
cd frontend-react && npm run test             # frontend (63 tests)
```

## Project Layout

```
backend/           FastAPI app (routers in app.py, domain modules per feature)
db/migrations/     Plain SQL migrations, re-run idempotently on every startup
frontend-react/    React SPA (self-hosted router, no react-router dependency)
scripts/           Utility scripts (migrations, code-availability backfill, …)
tests/             Backend test suite (pytest, fake-connection pattern)
```

## Relationship to upstream [paper_online](https://github.com/KMnO4-zx/paper_online)

This is a trimmed single-user fork (baseline commit `c6a235f`); see git log for the trim history. The upstream deployment and this project are fully independent (different ports, databases, and directories), and upstream changes are not synced automatically — port features by cherry-picking and adapting them to the single-user auth model.

## License

See [LICENSE](LICENSE).
