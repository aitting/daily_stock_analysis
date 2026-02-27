# GitHub Copilot Instructions

## Project Overview

AI-powered stock analysis system for A-share/HK/US markets. Fetches market data, runs LLM analysis, and pushes "decision dashboards" to WeChat Work / Feishu / Telegram / email. Stack: Python 3.10+, FastAPI, SQLAlchemy (SQLite), multiple data-provider SDKs.

## Architecture — Major Components

```
main.py          → CLI / scheduled batch entry point
server.py        → FastAPI server (uses api/)
webui.py         → Gradio web UI entry point
bot/             → Messaging-platform bot (WeChat Work, Feishu, Telegram, DingTalk)
api/             → FastAPI app factory + v1 routers
src/core/pipeline.py   → StockAnalysisPipeline: master orchestrator
src/analyzer.py        → GeminiAnalyzer: LLM call + response parsing
src/agent/executor.py  → ReAct agent loop (tool-calling, multi-turn)
src/agent/tools/       → Agent tools: data, analysis, market, search
data_provider/         → DataFetcherManager: strategy-pattern multi-source fetcher
src/storage.py         → SQLAlchemy ORM + SQLite singleton (get_db())
src/notification.py    → Multi-channel push (WeCom/Feishu/Telegram/Email/Pushover/Discord)
src/config.py          → Singleton Config loaded from .env (get_config())
strategies/            → YAML strategy files loaded at agent startup (no code needed)
```

**Data flow (batch analysis):**
`main.py` → `StockAnalysisPipeline` → `DataFetcherManager` (fetches OHLCV + chips + news) → `StockTrendAnalyzer` + `GeminiAnalyzer` → `NotificationService` → push channels

**LLM fallback chain** (configured in `.env`):
Gemini → Anthropic Claude → OpenAI-compatible (AIHUBMIX_KEY takes priority over OPENAI_API_KEY within the compatible layer)

## Critical Conventions

### Environment & Config
- All config comes from `.env` (copy `.env.example`). Never hardcode secrets.
- **Every entry-point file must call `setup_env()` before any other import that touches config:**
  ```python
  from src.config import setup_env
  setup_env()
  ```
- Singleton access: `from src.config import get_config; cfg = get_config()`

### Stock Code Normalization
- Always call `canonical_stock_code(code)` (from `data_provider.base`) before passing codes into `DataFetcherManager`. It strips exchange prefixes/suffixes (`SH600519` → `600519`) while preserving HK (`HK00700`) and US tickers (`AAPL`).

### Data Provider (Strategy Pattern)
- `DataFetcherManager` auto-selects and fails-over across AkShare / Tushare / Baostock / YFinance / Pytdx. Do **not** instantiate individual fetchers directly in business logic — always go through the manager.
- US-market history and real-time quotes use YFinance exclusively for adjusted-price consistency.

### Agent & Strategies
- Agent uses a ReAct loop in `src/agent/executor.py`. Tools are registered in `src/agent/tools/registry.py`.
- Trading strategies are pure YAML in `strategies/`. Add a new `.yaml` file there — zero code required. See `strategies/README.md` for the schema.

### Report Types
- `ReportType.SIMPLE` → `generate_single_stock_report()` (concise)
- `ReportType.FULL` → `generate_dashboard_report()` (full decision dashboard)
- Always use `ReportType.from_str(value)` for user input; falls back to `SIMPLE` on invalid input.

### Proxy
- Local dev only: set `USE_PROXY=true` + `PROXY_HOST`/`PROXY_PORT` in `.env`.
- In GitHub Actions (`GITHUB_ACTIONS=true`) proxy is **always skipped**.

## Developer Workflows

### Syntax & Lint (run before every commit)
```bash
python -m py_compile main.py src/*.py data_provider/*.py
flake8 main.py src/ --max-line-length=120
```

### Tests
```bash
./test.sh syntax          # syntax check only
./test.sh quick           # single-stock smoke test
./test.sh market          # market-review only
pytest tests/             # unit tests
```

### Running Locally
```bash
python main.py                    # full batch run
python main.py --dry-run          # fetch data only, no LLM/push
python main.py --debug            # verbose debug logging
python server.py                  # start FastAPI server
```

## Code Style
- Line width: **120**; formatter: `black` + `isort`; linter: `flake8`
- **All code comments must be in English** (inline, docstrings, log messages)
- **All commit messages must be in English**; no `Co-Authored-By` trailers
- Structural comments in existing files use Chinese — preserve them; new additions in English

## Git & Release Rules
- Do **not** `git commit` without explicit user confirmation.
- Tag commits with `#patch` / `#minor` / `#major` / `#skip` to control semantic versioning.
- PRs fixing an issue must include `Fixes #xxx` or `Closes #xxx` in the description.
- After any feature/fix: update `README.md` and `docs/CHANGELOG.md`.
