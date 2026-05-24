# Concept — Risk management

This is the concept-level orientation: what risk management means in `algo_bot`, where it lives in the pipeline, and where to read for depth. For implementation details see [reference/modules/risk-limits](../reference/modules/risk-limits.md). For the design rationale see [ADR-008](../adr/008-risk-limits-module.md).

> A thin doc by design — Phase 1 deliverable. The production-grade variant (`risk-management-production.md`) lands in Phase 4 once we have live mainnet experience to back it.

## Why a separate module

A trading strategy decides *when* to enter and exit; risk management decides *whether* the system as a whole should keep going. The two concerns belong apart:

- A strategy's per-position stop-loss caps a single trade's loss. A bug in the strategy could still bleed an entire account through a sequence of small "valid" trades.
- A risk module sits one level up, watching the **portfolio**. It halts the bot when total losses cross a configured boundary — regardless of whether each individual trade was rule-following.

In `algo_bot`, per-position stops live on `Signal.sl_pct` / `Signal.meta["sl"]` (strategy scope, [ADR-003](../adr/003-strategybase-signal-api.md)). Portfolio safety nets live in `algo_bot.risk.limits` (this module).

## What the module does

Three **gates** check portfolio state every bar:

1. **Max drawdown** — halts when equity drops more than X% from peak.
2. **Daily loss** — halts when equity drops more than Y% from the start of the current day (timezone configurable, UTC by default).
3. **Max concurrent positions** — halts when more than N positions are open at once. Single-symbol MVP typically leaves this disabled.

One **calculator** is provided for position sizing:

- `position_size(equity_now, sl_distance, risk_per_trade_pct)` returns a position size such that hitting the stop-loss costs exactly `risk_per_trade_pct` of equity. **Caller-driven** — a strategy that wants this calls it inside `on_bar` and writes the result to `Signal.size`. The backtester does *not* auto-inject sizing, deliberately ([ADR-008 §8](../adr/008-risk-limits-module.md)).

Each limit is independently configurable; `None` disables it.

## Where it fires

The risk module hooks into the backtester at the top of every bar — *before* the strategy's `on_bar` runs. If any gate breaches, the wrapper force-closes the open position at the current bar's close price and halts the run by raising `RiskLimitBreached`. `run_backtest` catches the exception, fills `stats["_risk_breach"]` with the breach details, and returns normally.

For live trading the halt semantics will be different (graceful shutdown, Telegram alert, manual restart switch) — see Phase 3 deliverable. The pure check functions are engine-agnostic and reusable; only the halt translation changes.

## How to use

From a config or CLI:

```bash
algo-backtest --symbol BTC/USDT --timeframe 4h --strategy bghtrend_pullback \
  --max_dd_pct 0.20 --daily_loss_pct 0.05
```

Or programmatically:

```python
from algo_bot.engine.backtester import run_backtest
from algo_bot.risk import RiskLimits

limits = RiskLimits(max_drawdown_pct=0.20, daily_loss_pct=0.05)
stats, equity, trades = run_backtest(
    symbol="BTC/USDT",
    timeframe="4h",
    strategy="bghtrend_pullback",
    params={...},
    risk_limits=limits,
)
if "_risk_breach" in stats:
    print(f"Run halted: {stats['_risk_breach']}")
```

A strategy that wants risk-based sizing calls the helper directly:

```python
from algo_bot.risk import position_size

class MyStrategy(StrategyBase):
    def on_bar(self, df):
        entry = df["Close"].iloc[-1]
        sl = entry * 0.98  # 2% stop
        size = position_size(
            equity_now=self._equity,  # provided by wrapper, or read from journal
            sl_distance=abs(entry - sl),
            risk_per_trade_pct=self.p.risk_per_trade_pct,
        )
        return Signal("enter", "long", size=size, sl_pct=0.02)
```

## What it doesn't do (yet)

- **Per-position caps** — that's strategy scope. The risk module won't second-guess your `Signal.sl_pct`.
- **Live trading halts** — the hook is backtester-only in Phase 1. Phase 3 will add live integration with proper graceful-shutdown semantics.
- **Multi-strategy portfolio aggregation** — single-strategy/single-symbol MVP. Post-MVP we'll layer portfolio analytics on top (see [ROADMAP "Po MVP"](../ROADMAP.md)).
- **Adaptive limits** — thresholds are static. Adaptive sizing (e.g. Kelly fraction, vol-scaled) is a Phase 2/3 question, not a Phase 1 one.

## Where to read more

- [Reference — `algo_bot.risk.limits`](../reference/modules/risk-limits.md) — full API surface, edge cases, integration
- [ADR-008 — Risk limits module](../adr/008-risk-limits-module.md) — design rationale, rejected alternatives, consequences
- [ROADMAP](../ROADMAP.md#faza-1--foundation-framework-gotowy-do-pracy) — where this fits in Phase 1
