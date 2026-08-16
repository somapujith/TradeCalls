# SLM/LLM Candidate Explanation — Design Spec

Status: approved, not yet implemented. Implements the `ai/` module deferred in
[backend.md's Future work](../../backend.md#future-work-v2-out-of-scope-for-detail-here)
("SLM/LLM candidate explanation, planning doc section 46"), scoped down from the
planning doc's live-intraday framing to fit v1's EOD-batch, no-scheduler-change reality.

## Why this shape

The original planning doc ([§36](../../../AI_Intraday_Breakout_Research_Planning.md#36-slm-role),
[§37](../../../AI_Intraday_Breakout_Research_Planning.md#37-llm-role)) designed SLM screening as a
speed optimization in front of a live per-minute intraday scanner: cheap fast model rejects
obviously-bad candidates before the expensive model looks at them, because volume is high and
latency matters. v1 has no live scanner — `breakout/scoring.py` already hard-rejects bad
candidates before a `trade_setups` row exists, so daily candidate volume is small and speed
pressure is low. The two-stage SLM→LLM pipeline is kept anyway (explicit choice, not the
speed-driven default) to stay faithful to the planning doc's separation of concerns: SLM as a
final sanity-check gate, LLM as the explanation/reasoning layer that never invents prices.

This is an **on-demand, best-effort annotation layer** — triggered per-`trade_setup` via API,
not wired into the EOD scheduler chain. It must never block or alter the deterministic engine
output (`trade_setups` stays untouched); a failure here degrades to a stored error state, not
a broken request.

Telegram alerting (`notifications/` module) is explicitly out of scope — separate spec, per
backend.md's own module split.

## Local models (LM Studio, OpenAI-compatible endpoint)

Both already downloaded and loaded by the user:

| Stage | Model | Notes |
|---|---|---|
| SLM | `qwen2.5-1.5b-instruct` | small/fast, structured JSON-in / short-verdict-out |
| LLM | `qwen2.5-coder-7b-instruct` | Coder variant — tuned for code, not general prose; expect terser/more technical explanation text than a general-instruct model would produce. Swap later if needed; config takes the model id as a plain string either way. |

Endpoint: LM Studio's local server, exposed to this backend via a Cloudflare quick tunnel
(`https://<random>.trycloudflare.com/v1`). **The trycloudflare URL is not stable** — it changes
whenever `cloudflared` restarts, so `LM_STUDIO_BASE_URL` in `.env` will need updating each time
that happens. Not a blocker for this spec; noted as an operational fact.

## Module layout

```
backend/app/ai/
  __init__.py
  lm_studio_client.py    — thin HTTP wrapper over LM Studio's /v1/chat/completions
  slm_screener.py         — builds structured JSON prompt, parses VALID_CANDIDATE/REJECT
  llm_explainer.py        — builds explanation prompt (+ news context), returns prose
  candidate_pipeline.py   — orchestrates screen -> (news fetch) -> explain -> persist
```

### `lm_studio_client.py`

```python
class LMStudioError(Exception): ...

def complete(model: str, messages: list[dict], *, timeout: float) -> str:
    """POST {base_url}/chat/completions. Returns choices[0].message.content.
    Raises LMStudioError on connection failure, timeout, non-2xx, or a response
    missing the expected shape. Never raises anything else — callers only need
    to catch LMStudioError."""
```

Uses `httpx` (already a transitive dependency via `fastapi`/`starlette`'s stack — confirm at
implementation time whether it needs adding directly to `requirements`/`pyproject`) with an
explicit timeout from `settings.lm_studio_timeout_seconds`. No streaming — single blocking
completion per call, since both stages need the full text before proceeding.

### `slm_screener.py`

Builds the structured JSON payload per planning doc §36's shape (symbol, price, breakout_level,
rvol, atr, candle_body_pct, close_position_pct, market_regime, sector_regime, breakout_score —
`vwap`/`news_severity`/`news_sentiment` omitted, not computed anywhere in v1 per engine.md's
scoring exclusions) as the user message, with a system prompt instructing the model to respond
with exactly one token: `VALID_CANDIDATE` or `REJECT`.

```python
def build_prompt(context: CandidateContext) -> list[dict]: ...
def screen(context: CandidateContext, *, timeout: float) -> str:
    """Returns "VALID_CANDIDATE" or "REJECT". Anything else in the model's
    response (extra text, wrong casing/whitespace handled by strip+upper,
    but any other content) is treated as REJECT — fail-closed, per
    engine.md's principle that the deterministic engine decides what's real,
    not the LLM. Propagates LMStudioError to the caller unchanged."""
```

### `llm_explainer.py`

Builds a prompt asking for the six deliverables from planning doc §37: why the breakout is
meaningful, conflicting signals, news summary, risk, invalidation conditions, and an internal
consistency check against the quantitative score — explicitly instructed not to invent prices
or indicator values, only reason over the ones supplied.

```python
def build_prompt(context: CandidateContext, news_items: list[NewsItem]) -> list[dict]: ...
def explain(context: CandidateContext, news_items: list[NewsItem], *, timeout: float) -> str:
    """Returns the LLM's explanation text. Propagates LMStudioError unchanged."""
```

News context: up to 5 most recent items from `app/news/aggregator.fetch_all()` +
`app/news/tagger.tag_items()`, filtered to `symbol_hint == context.symbol` and
`published_at >= now - AI_NEWS_LOOKBACK_DAYS days`. Raw headline/summary text goes into the
prompt — there is no severity/sentiment score anywhere in this codebase to pass instead (that
field is explicitly unbuilt, per engine.md's scoring exclusions). News fetch failure (any
individual source, or all of them — `aggregator.fetch_all()` already degrades per-source
internally) results in an empty `news_items` list, not a raised exception; the LLM prompt
simply omits the news section rather than blocking the explanation.

### `candidate_pipeline.py`

```python
def run(db: Session, trade_setup_id: int) -> AiExplanation:
    """
    1. Load TradeSetup + Stock (404-equivalent ValueError if not found — caller/API maps this).
    2. Build CandidateContext from trade_setup fields + score_breakdown (already-stored JSON).
    3. Call slm_screener.screen(). On LMStudioError: persist slm_verdict="ERROR",
       error_message=str(exc), llm_explanation=None. Return early.
    4. If verdict == "REJECT": persist slm_verdict="REJECT", llm_explanation=None. Return.
    5. If verdict == "VALID_CANDIDATE": fetch news (best-effort, see above), call
       llm_explainer.explain(). On LMStudioError: persist slm_verdict="VALID_CANDIDATE",
       llm_explanation=None, error_message=str(exc). Return.
    6. On success: persist slm_verdict="VALID_CANDIDATE", llm_explanation=<text>,
       error_message=None.
    Every path upserts one row in ai_explanations keyed by trade_setup_id (overwrite on re-run —
    this is a "explain again" action, not an append-only log).
    """
```

## Database

New table, following existing `db/models.py` conventions (see `TradeOutcome` for the
one-row-per-`trade_setup_id` FK pattern):

```python
class AiExplanation(Base):
    """SLM verdict + LLM explanation for a trade_setup, best-effort/on-demand —
    docs/superpowers/specs/2026-08-17-slm-llm-candidate-explanation-design.md."""

    __tablename__ = "ai_explanations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    trade_setup_id: Mapped[int] = mapped_column(ForeignKey("trade_setups.id"), unique=True, nullable=False)
    slm_verdict: Mapped[str] = mapped_column(String(20), nullable=False)  # VALID_CANDIDATE | REJECT | ERROR
    slm_model: Mapped[str] = mapped_column(String(128), nullable=False)
    llm_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    trade_setup: Mapped["TradeSetup"] = relationship()
```

`trade_setups` gets no new columns — the deterministic engine's output stays untouched, per the
approved design.

## Config (`config.py`)

```python
# LM Studio (local SLM/LLM, free — docs/superpowers/specs/2026-08-17-...-design.md)
lm_studio_base_url: str = ""  # e.g. https://<tunnel>.trycloudflare.com/v1 — no default, must be set
lm_studio_slm_model: str = "qwen2.5-1.5b-instruct"
lm_studio_llm_model: str = "qwen2.5-coder-7b-instruct"
lm_studio_timeout_seconds: float = 30.0
ai_news_lookback_days: int = 7
```

`.env.example` gets the same keys (blank/placeholder values, matching existing convention for
`ANGEL_ONE_*`/`TELEGRAM_*`).

## API

New router `backend/app/api/ai_explanations.py`, mounted in `main.py` alongside the existing
routers:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/trade-setups/{trade_setup_id}/explain` | POST | Run the pipeline (fresh SLM screen + LLM explain), upsert, return the result |
| `/api/trade-setups/{trade_setup_id}/explain` | GET | Return the cached result if one exists; 404 if never requested |

Response schema (`schemas/ai_explanations.py`):

```python
class AiExplanationOut(BaseModel):
    trade_setup_id: int
    slm_verdict: str
    slm_model: str
    llm_explanation: str | None
    llm_model: str | None
    error_message: str | None
    updated_at: datetime
```

404 (unknown `trade_setup_id`, or GET with no prior POST) uses the existing
`HTTPException`/global exception-handler pattern already in `main.py` — no new error-handling
scaffolding needed. LM Studio being unreachable is **not** a 5xx from this endpoint: the pipeline
catches `LMStudioError` internally and returns 200 with `slm_verdict="ERROR"` — the AI layer's
own failure is a normal, representable result, not a server error.

## Testing (TDD, ≤2 mocks/test per project convention)

- `lm_studio_client`: mock the HTTP boundary only (success, timeout, non-2xx, malformed JSON
  body) — one mock (the transport), verifying `complete()`/`LMStudioError` contract.
- `slm_screener` / `llm_explainer` prompt builders and verdict parsing: pure functions, no
  mocks — exact-match, whitespace/casing, and garbage-input cases for the parser.
- `candidate_pipeline`: mock `lm_studio_client.complete` and use a real (SQLite, per existing
  test suite convention) DB session — covers REJECT-skips-LLM, ERROR-on-SLM-failure,
  ERROR-on-LLM-failure-after-VALID, and the full happy path, asserting the persisted
  `ai_explanations` row each time.
- News filtering (lookback window, symbol match, top-5 cap): pure function test against a
  fixed `datetime`, no mocks.
- API endpoint: mock `candidate_pipeline.run`, assert 200/404 status and response shape for
  POST (fresh run) and GET (cached vs. never-requested).

## Explicit non-goals (this spec)

- Not wired into the EOD scheduler — on-demand only.
- Does not touch `trade_setups` or `breakout_candidates` — `ai_explanations` is a separate table.
- SLM `REJECT` never blocks, deletes, or flags the underlying `trade_setup` — informational only.
- No Telegram delivery of the explanation — separate spec (`notifications/` module).
- No retry/backoff on LM Studio calls — a failure is surfaced once, re-running is a manual
  "explain again" POST.
- No streaming responses from LM Studio.
