# At-Home Quant App – Phase 1 Data Layer

This repository contains the first phase of the At-Home Quant App: a robust, testable data pipeline that fetches market data with `yfinance`, normalizes it, and stores it in a relational database (SQLite by default).

## Package layout

```
at_home_quant/
  config/          # App settings (database URL, defaults)
  data/            # Ticker definitions and data fetch utilities
  db/              # SQLAlchemy models, sessions, and CRUD helpers
  etl/             # Historical load and daily update entry points
  tests/           # Pytest coverage for fetch/DB routines
```

## Quick start

1. **Create a virtual environment and install dependencies**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. **Configure (optional)**

Set `DATABASE_URL` in a `.env` file or environment variable to override the default SQLite database (`sqlite:///./data/quant.db`).

Data mode and health-gate controls:

- `DATA_MODE=research|production` (defaults to `production`; `research` allows synthetic fallback when vendor data is unavailable).
- `ENFORCE_DATA_HEALTH_GATE=true|false` (when true, portfolio/rebalance actions are blocked if required symbols are stale/missing).
- `MIN_HISTORY_DAYS_FOR_REGIME=252` (minimum observations required for regime benchmark symbols).
- `MAX_SYMBOL_STALENESS_DAYS=5` (maximum acceptable lag from requested as-of date).
- `BENCHMARK_SELECTION_TIMING=period_start|period_end` (defaults to `period_start` to reduce look-ahead bias).
- `TRANSACTION_COST_BPS=5` (one-way transaction cost applied to turnover in performance calculation).
- `SLIPPAGE_BPS=5` (one-way slippage applied to turnover in performance calculation).
- `RISK_MAX_POSITION=0.12` (max single equity position weight in portfolio construction).
- `RISK_MAX_SECTOR_WEIGHT=0.35` (max aggregate equity weight by sector).
- `RISK_MAX_TURNOVER=0.35` (max monthly turnover allowed by the risk overlay).
- `RISK_MIN_ADV_USD=5000000` (minimum average daily dollar volume for equity eligibility).
- `RISK_ADV_LOOKBACK_DAYS=20` (ADV lookback window in business days).

3. **Run the initial historical ETL**

```bash
python -m at_home_quant.etl.historical_load
```

4. **Run the idempotent daily update**

```bash
python -m at_home_quant.etl.daily_update
```

Both scripts will create the database and tables if they do not exist and upsert ticker metadata plus price history.

## Tests

Execute the test suite (requires network access for `yfinance`):

```bash
pytest
```

## Sanity checks

- After the historical load, inspect a few rows directly from SQLite (e.g., using `sqlite3 data/quant.db 'select count(*) from prices_daily;'`).
- Verify there are no duplicate `(ticker, date)` pairs; the unique constraint enforces this during upsert.
- Confirm the price series for a benchmark like `GLD` is monotonic in date and includes `adj_close` values.

## Next steps

With the data layer in place, future phases will add the regime/universe scoring engine, security selection, and portfolio construction built on top of this storage layer.

## Regime Engine (Phase 2)

The regime engine computes composite regime scores for NASDAQ 100, S&P 500, and FTSE 250 universes based on trend, momentum, realized volatility, and drawdown signals stored in the database. Use `get_current_regime(as_of_date)` to obtain the current best universe and suggested equity bands.

Example:

```bash
python -m at_home_quant.scripts.print_regime --as-of 2025-01-31
```

The call reads benchmark price history from the existing database; ensure you have run the ETL loaders so the required tickers are populated.

## Security Selection Engine (Phase 3)

Phase 3 adds a stock-ranking layer that scores individual equities within each universe using momentum, stability, low volatility, value, and shareholder yield factors. Invoke `rank_universe(universe_name, as_of_date, top_n)` to obtain the top names and their composite scores. A CLI helper mirrors the regime script:

```bash
python -m at_home_quant.scripts.print_ranking --universe NASDAQ100 --as-of 2025-01-31 --top-n 15
```

Ensure equity constituents and price history for the chosen universe exist in the database (the synthetic loaders used in tests are compatible with this flow).

Universe membership is now point-in-time aware through the `universe_memberships` table, so ranking uses constituents active on each requested `as_of_date`.

## Portfolio Construction & Rebalancing (Phase 4)

Phase 4 connects the regime and ranking engines to produce a monthly target portfolio and minimal-turnover rebalance instructions.

- Build a target mix via `build_monthly_portfolio(as_of_date)` which:
  - Chooses the best universe from the regime engine and its suggested equity band.
  - Allocates equity exposure to the top-ranked stocks (softmax weights with position caps).
  - Assigns the remaining defensive sleeve to Gold (40%) and Cash/T-Bills (60%).
- Compare snapshots with `compute_rebalance(as_of_date)` to generate buy/sell/hold deltas.
- Portfolio construction applies a risk overlay before snapshot save:
  - max single equity position,
  - max sector weight,
  - turnover cap (vs prior snapshot),
  - liquidity floor using average daily dollar volume.

A CLI helper prints the monthly rebalance plan:

```bash
python -m at_home_quant.scripts.print_rebalance --as-of 2025-02-28
```

Snapshots are stored in the `portfolio_snapshots` table for historical inspection.

## Performance & Alpha Measurement (Phase 5)

Phase 5 measures how the constructed portfolio performs versus the best-scoring universe each month.

- Monthly performance uses stored portfolio snapshots and DB price data to compute portfolio returns.
- The benchmark for each month is chosen from NASDAQ100 (QQQ), S&P500 (SPY), or FTSE250 (VMID) using configurable timing (`BENCHMARK_SELECTION_TIMING=period_start|period_end`).
- Portfolio returns include configurable implementation costs based on turnover (`TRANSACTION_COST_BPS` + `SLIPPAGE_BPS`).
- Alpha is defined as `portfolio_return(net) - benchmark_return` per month, with aggregates including CAGR, volatility, max drawdown, Sharpe, and cumulative alpha.
- Summary diagnostics also include gross return, total cost drag, tracking error, information ratio, alpha hit rate, and average monthly turnover.
- CLI helper:

```bash
python -m at_home_quant.scripts.print_performance [--csv performance.csv]
```

This prints monthly returns, benchmarks, and alpha along with summary statistics; the optional CSV flag exports the monthly series.

## Walk-Forward Run Registry

You can run and persist a walk-forward backtest artifact bundle (config, code hash, data snapshot hash, monthly series, and summary stats):

```bash
python -m at_home_quant.scripts.run_walk_forward --start 2024-01-01 --end 2025-12-31 --top-n 15
```

Runs are saved in the `backtest_runs` table for auditability and reproducibility.

## Running the Dashboard

Phase 6 adds a local Streamlit dashboard for quick inspection of the regime engine, target portfolios, and performance.

1. Install Streamlit (included in `requirements.txt`):

```bash
pip install -r requirements.txt
```

2. Launch the app:

```bash
streamlit run at_home_quant/app.py
```

The dashboard is organized into four sections:

- **Portfolio Onboarding** – paste your current live holdings (ticker + weight) and save an anchor portfolio snapshot that future rebalance recommendations use as the baseline.
- **Regime & Universe Overview** – select a date to view the best universe, composite scores, and suggested equity exposure band.
- **Current Portfolio & Rebalance** – build the monthly target portfolio, see sleeve weights, and view buy/sell/hold instructions for the selected date.
- **Stock Ranking (Equity Sleeve Detail)** – choose a universe, date, and Top-N cutoff to inspect factor scores for the leading stocks.
- **Performance & Alpha** – tune benchmark timing and implementation cost assumptions, then review monthly net/gross returns, turnover/cost history, and summary diagnostics (including tracking error and information ratio).

## Weekly Advisor Workflow (UI)

The app now defaults to a guided **Weekly Advisor** tab with repeatable steps:

1. Sync data
2. Confirm current holdings
3. Generate weekly recommendation
4. Log `follow|ignore|partial` decisions per recommendation
5. Save executed portfolio from decision log
6. Review weekly report + near-buy watchlist

Technical and debug views (ETL controls, deep diagnostics, factor tables) are moved under the **Advanced** tab.

For backend automation, you can run the weekly cycle from terminal:

```bash
python -m at_home_quant.scripts.run_weekly_cycle
```

This runs daily data sync and generates a fresh weekly recommendation batch using the latest available price date.
