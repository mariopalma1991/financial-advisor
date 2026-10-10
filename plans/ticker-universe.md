# Ticker Universe — Design & Starter Proposal

**Purpose:** define which tickers the platform tracks, how they are specified, and the criteria for adding or removing them.
**Source of truth (Phase 1):** `config/tickers.yaml` — the platform reads it on every ingestion cycle. This document is the human-facing companion: design rationale, criteria, and the starter list.

---

## 1. How It Works

- **Add a ticker** → one YAML entry → the next GitHub Actions cycle ingests it. First add triggers a **historical backfill** (~3 years of daily bars + available EDGAR filings) so indicators and 200-day averages work immediately.
- **Remove a ticker** → set `enabled: false` rather than deleting. History is retained for backtesting; the ticker simply stops being refreshed and disappears from `list_tickers()`.
- Every entry feeds the `dim_ticker` table (see Appendix A of the platform plan) with bitemporal tracking — the universe itself is point-in-time queryable ("what were we tracking on March 3rd?").

## 2. Entry Structure

```yaml
tickers:
  - symbol: AAPL
    name: Apple Inc.
    exchange: NASDAQ        # SIC-traded US stock; use BMV for Mexican listings
    currency: USD           # MXN for BMV — drives the FX leg of the cost model
    sector: Technology
    benchmark: SPY          # relative-strength reference (EWW or ^MXX for BMV names)
    enabled: true
    tags: [mega-cap, services-transition]
    notes: "Watching services margin trend and buyback pace"

  - symbol: WALMEX
    name: Walmart de Mexico
    exchange: BMV
    currency: MXN           # no FX cost — round trip ~0.68% instead of ~1.68%
    sector: Consumer Staples
    benchmark: EWW
    enabled: true
    tags: [defensive, mx-domestic]
    notes: ""
```

**Why each field exists:**

| Field | Used by |
|---|---|
| `exchange`, `currency` | Cost model — decides whether the FX spread applies to the round trip |
| `sector` | Diversification checks; sector relative-strength in the price feed |
| `benchmark` | Relative-strength computation (`relative_strength_vs_*` in the package) |
| `enabled` | Pause without deleting; keeps history for backtests |
| `tags` | Future agent filtering ("only analyze tag:ai names this week") |
| `notes` | Human context; optionally injected into the judge's prompt later |

## 3. Selection Criteria (swing-trading specific)

A ticker earns its place only if it clears these bars — each one ties back to a design decision:

1. **Liquidity** — tight bid/ask spread. Wide spreads break the cost model (the 0.68%/1.68% round trips assume ~0.05% spread). Rule of thumb: large-caps and heavily traded BMV names only.
2. **Volatility** — daily ATR ≥ ~1.5% of price. Below that, a multi-day move can't clear the 3× min-edge rule; the judge would keep rejecting everything.
3. **News flow** — enough coverage for the sentiment feed to have signal (a ticker with 2 articles/month gives the Sentiment Analyst nothing).
4. **Known earnings calendar** — the catalyst feed depends on it.
5. **Sector diversification** — 8 correlated tech names is one bet, not a universe. Cap any sector at ~30% of the list.
6. **Your own interest** — you'll read the judge's rationales; track names you understand.

## 4. Starter Proposal

**US large-caps (SIC — higher round trip, deeper news coverage):**

| Symbol | Sector | Why |
|---|---|---|
| AAPL | Technology | Mega-cap liquidity, heavy news flow |
| MSFT | Technology | Cloud/AI trend, steady swing ranges |
| NVDA | Semiconductors | High ATR — clears cost hurdles easily |
| AMZN | Consumer Disc. | Multi-driver story (retail + AWS) |
| JPM | Financials | Rate-sensitive, sector diversifier |
| XOM | Energy | Oil-driven, low correlation to tech |
| UNH | Healthcare | Defensive ballast |

**Mexican stocks (BMV — no FX leg, ~0.68% round trip):**

| Symbol | Sector | Why |
|---|---|---|
| WALMEX | Consumer Staples | Most liquid BMV name, defensive |
| AMX | Telecom | High liquidity, news coverage |
| GFNORTE | Financials | Mexican rate cycle exposure |
| FEMSA | Consumer/Retail | KOF + OXXO drivers |

**Benchmarks (not traded — reference only):** SPY (US), EWW or ^MXX (Mexico).

Suggested Phase 1 start: **5–8 tickers** mixing both markets (e.g., AAPL, MSFT, NVDA, JPM + WALMEX, GFNORTE) — enough to exercise every feed and the dual-currency cost model, small enough to inspect every output by hand.

## 5. Sizing Guidance

| Stage | Universe | Rationale |
|---|---|---|
| Phase 1 | 5–8 | Validate pipeline end-to-end; eyeball every package |
| Steady state | 10–30 | Each ticker multiplies news volume, storage, and per-analysis LLM cost |
| Hard cap (free tiers) | ~50 | yfinance politeness, GitHub Actions minutes, Supabase 500 MB |

## 6. Open Questions

1. **Market mix** — US-only, Mexico-only, or the mixed list above? (Mixed exercises the FX cost model from day one.)
2. **Sectors of interest** — any you want overweighted or excluded?
3. **Starting count** — accept the 5–8 suggestion, or go straight to 10–15?
