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
