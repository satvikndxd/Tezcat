# Tezcat API Contract (v1)

All endpoints are JSON over REST, mounted under `/api`. The FastAPI backend serves
the built React dashboard at `/`.

## Presets

### `GET /api/presets`
```json
[
  {
    "preset_id": "stable_baseline",
    "name": "Stable Baseline",
    "description": "Normally functioning market with balanced agents.",
    "tags": ["baseline", "calm"],
    "total_steps": 1500,
    "agent_summary": {"noise_trader": 20, "market_maker": 4},
    "shock_summary": ["sentiment_shock @ step 600"]
  }
]
```

### `GET /api/presets/{preset_id}`
Full preset with `config_template` (a complete ExperimentConfig object).

### `POST /api/presets/{preset_id}/experiments`
Body: `{ "name": "optional name", "overrides": {} }` → returns Experiment (201).

## Experiments

### `POST /api/experiments`
Body: `{ "name": str, "hypothesis": str|null, "config": ExperimentConfig }` → Experiment.

### `GET /api/experiments` → `[Experiment]`
### `GET /api/experiments/{experiment_id}` → Experiment

Experiment:
```json
{
  "experiment_id": "exp_ab12cd34",
  "name": "Flash Crash",
  "hypothesis": null,
  "preset_id": "flash_crash",
  "config_hash": "9f8e...",
  "version": "0.1.0",
  "created_at": "2026-08-06T12:00:00Z",
  "status": "ready",
  "config": { ...full ExperimentConfig... }
}
```

### `GET /api/experiments/{experiment_id}/shocks`
Scheduled shock definitions from the config: `[{shock_id, shock_type, trigger: {kind: "scheduled", step}, magnitude, duration, enabled, description}]`

## Runs

### `POST /api/experiments/{experiment_id}/runs`
Body: `{ "seed": int|null, "step_delay_ms": int|null }` (null seed → random, recorded). Returns Run (201) and starts execution in background.

### `GET /api/runs` → `[Run]`  (newest first)
### `GET /api/runs/{run_id}` → Run

Run:
```json
{
  "run_id": "run_ab12cd34",
  "experiment_id": "exp_...",
  "experiment_name": "Flash Crash",
  "seed": 42,
  "status": "running",           // pending|running|paused|completed|failed|cancelled
  "steps_completed": 512,
  "current_step": 512,
  "total_steps": 2000,
  "current_regime": "crisis",    // stable|crisis|recovery
  "started_at": "...", "completed_at": null,
  "config_hash": "9f8e..."
}
```

### `POST /api/runs/{run_id}/pause` → Run
### `POST /api/runs/{run_id}/resume` → Run
### `POST /api/runs/{run_id}/cancel` → Run

## Shocks

### `POST /api/runs/{run_id}/shocks`
Inject a manual shock into a live run.
Body: `{ "shock_type": "whale_order"|"mm_withdrawal"|"sentiment_shock", "side": "buy"|"sell"|null, "magnitude": number, "duration": int|null }`
→ `{ "queued": true, "run_id": ..., "shock_type": ... }` (202)

### `GET /api/runs/{run_id}/shocks`
Fired shock events:
```json
[{
  "event_id": "shk_...", "step": 750, "shock_id": "whale_1",
  "shock_type": "whale_order", "trigger_reason": "scheduled",
  "payload": {"side": "sell", "quantity": 400},
  "market_before": {"last_price": 101.2, "spread": 0.1, "bid_depth": 900, "ask_depth": 850},
  "market_after":  {"last_price": 93.6, "spread": 1.4, "bid_depth": 120, "ask_depth": 700}
}]
```

## Regimes

### `GET /api/runs/{run_id}/regimes`
```json
[{"event_id": "rgm_...", "step": 760, "previous_regime": "stable", "new_regime": "crisis",
  "reason": "drawdown 8.2% > 6% and spread widened", "metrics": {"drawdown": 0.082}}]
```

## Market, history, trades, metrics, report

### `GET /api/runs/{run_id}/market`
```json
{
  "snapshot": {"step": 512, "last_price": 99.7, "best_bid": 99.6, "best_ask": 99.8,
               "mid_price": 99.7, "spread": 0.2, "bid_depth": 640, "ask_depth": 580,
               "volume": 35, "order_imbalance": 0.04, "regime": "stable"},
  "bids": [[99.6, 120], [99.5, 200]],   // top 10 levels, price desc
  "asks": [[99.8, 90],  [99.9, 310]]    // top 10 levels, price asc
}
```

### `GET /api/runs/{run_id}/history?start=0&limit=5000`
`{ "snapshots": [MarketSnapshot], "next_start": 512 }` — snapshots have the same shape as above. Poll with `start=next_start` for increments.

### `GET /api/runs/{run_id}/trades?limit=100`
`{ "trades": [{"trade_id","step","price","quantity","buy_agent_id","sell_agent_id","buy_agent_type","sell_agent_type"}] }` (most recent last)

### `GET /api/runs/{run_id}/metrics?start=0&limit=5000`
`{ "metrics": [{"step","last_price","log_return","rolling_volatility","spread","depth","volume","order_imbalance","regime"}], "next_start": 512 }`

### `GET /api/runs/{run_id}/report`
404 until run completed. Then:
```json
{
  "report_id": "rpt_...", "run_id": "run_...",
  "final_price": 97.1, "initial_price": 100.0, "total_return": -0.029,
  "realized_volatility": 0.012, "average_spread": 0.21, "max_drawdown": 0.113,
  "total_volume": 48210, "total_trades": 3120,
  "crash_detected": true, "liquidity_crisis_detected": true,
  "agent_pnl_by_type": {
    "market_maker": {"agents": 4, "realized_pnl": 812.5, "unrealized_pnl": -40.2,
                     "total_pnl": 772.3, "final_cash": 41200.1, "final_inventory": 118}
  },
  "regime_step_share": {"stable": 0.71, "crisis": 0.17, "recovery": 0.12},
  "shock_count": 2, "regime_transition_count": 4
}
```

### `POST /api/runs/{run_id}/export`
Writes JSON artifacts (config, trades, snapshots, metrics, events, report) to the
artifact store; returns `{ "export_id", "files": [paths], "s3_prefix" }`.

## External Event-Market Intelligence (S3-A)

These additive endpoints are read-only. They discover and observe public Kalshi and Polymarket data, preserve raw and normalized snapshots, and never place orders or expose wallets, account state, private keys, arbitrage automation, or financial advice. Provider failures, schema drift, invalid probabilities, and rate limits are returned explicitly.

| Method | Route | Result |
| --- | --- | --- |
| `GET` | `/api/markets/providers` | configured observed providers and adapter versions |
| `GET` | `/api/markets/events?provider=kalshi` | normalized `OBSERVED` event discovery |
| `GET` | `/api/markets/markets?provider=polymarket` | normalized `OBSERVED` market discovery |
| `GET` | `/api/markets/{market_id}?provider=...` | normalized `OBSERVED` market detail |
| `GET` | `/api/markets/{market_id}/orderbook?provider=...` | one observed order book with provider-native levels retained |
| `GET` | `/api/markets/{market_id}/history?provider=...` | documented public history; Polymarket uses CLOB price history |
| `GET` | `/api/markets/{market_id}/trades?provider=kalshi` | Kalshi public trades; Polymarket returns an explicit unauthenticated-surface error |
| `POST` | `/api/markets/{market_id}/snapshot` | manual immutable raw + normalized dataset registration |
| `GET` | `/api/markets/datasets` | registered immutable dataset manifests |
| `GET` | `/api/markets/datasets/{dataset_id}` | manifest and normalized artifact |
| `POST` | `/api/markets/datasets/{dataset_id}/signature` | `INFERRED` observed signature |
| `POST` | `/api/markets/divergence` | descriptive cross-provider probability divergence, not arbitrage |
| `POST` | `/api/markets/research` | `HYPOTHESIS` proposal with `approved: false` |
| `POST` | `/api/markets/research/compile` | explicit approval required; returns a normal `ExperimentVersion` tagged `MODEL_RESULT` |

The compile response contains an external research manifest and persists the same dataset/signature identity in `ExperimentVersion.external_context`. The identity includes raw and normalized checksums, provider/event/market IDs, time window, adapter/schema versions, and signature checksum. Existing report and reproduce endpoints render and verify this identity.

## Errors
`{ "detail": "message" }` with appropriate 4xx status.
