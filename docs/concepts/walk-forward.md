# Concept — Walk-forward

This is the concept-level orientation: what walk-forward is, why it's the mandatory gate before any live deployment, and how to read its output. For implementation details see [reference/modules/walkforward](../reference/modules/walkforward.md). For the design rationale see [ADR-009](../adr/009-walk-forward.md).

> A thin doc by design — Phase 1 deliverable. Will be extended in Phase 2 alongside the bghtrend_pullback walk-forward analysis (ROADMAP line 84) and parameter stability work.

## Why walk-forward

A sweep that searches a parameter grid over the entire historical window will find the parameter combination that fits that exact window the best. Part of "the best" is signal — the strategy genuinely captures something. Part of it is noise — the parameters are tuned to specific drawdowns, regimes, and outlier days. The second part is overfit, and it doesn't survive contact with new data.

Walk-forward answers a different question: *if the strategy had been deployed with these parameters at point T in the past, how would it have performed on the data that came after T?* If we slide T forward through history, we collect a sequence of out-of-sample (OOS) results — each one is a small honest backtest on data the parameters didn't see during selection.

The aggregate of those OOS results is what we trust before going live. An in-sample backtest with Sharpe 2.5 and a walk-forward Sharpe of 0.3 means the strategy is fitting noise. An in-sample Sharpe of 1.5 and a walk-forward Sharpe of 1.3 means the edge is approximately real.

Pardo (2008) and Aronson (2007) both make the case explicitly: any backtest that hasn't been walk-forwarded is statistically unreliable. We treat this as a hard gate before Phase 3 paper trading.

## What the analyzer does

For a given strategy, parameters, and configuration:

1. **Generate folds.** Slide a `(train, test)` window across the historical data, advancing by `step` bars per fold. Two modes: `rolling` (fixed-size train window slides forward) and `anchored` (train window starts at `data[0]` and grows).
2. **Execute per fold.** Run a backtest on each fold's test window with a fresh per-fold `RiskState`. Each fold is a statistically independent realisation.
3. **Aggregate.** Collect per-fold `MetricsSummary` (Sharpe, Sortino, Calmar, max drawdown, profit factor, ...) into a per-fold table and a distribution table (mean, median, std, min, max).
4. **Stitch equity.** Combine per-fold OOS equity into a continuous compounded curve — what the equity would look like if a fund had run this strategy continuously across all OOS windows.
5. **Compare against MVP criteria.** Four pass/fail booleans against the Phase 2 thresholds: mean Sharpe ≥ 1.0, mean max DD ≥ -25%, mean profit factor ≥ 1.3, mean n_trades ≥ 50.

The output is written to `results/walkforward/<wf_run_id>/`. Each fold has its own subdirectory mirroring a regular backtest output, so a single bad fold can be opened and debugged like any other backtest.

## How to read the output

Three artefacts answer three different questions.

### `walkforward_folds.csv` — fold-by-fold table

One row per fold. Tells you *which* folds went well and which didn't. A strategy with one great fold and four losing folds has a different problem than a strategy with five mediocre folds — the first is regime-dependent, the second is genuinely weak.

What to look for:

- **Variance across folds.** If `sharpe` ranges from -0.5 to +3.0 across folds, the strategy is regime-sensitive; the mean Sharpe is misleading.
- **`boundary_closes`.** When this is half or more of a fold's `n_trades`, the test window is too short relative to typical trade duration — widen `test` or shorten the strategy holding horizon.
- **`risk_breach_kind`.** Folds where the risk module halted execution. A fold that breaches `max_drawdown` is a fold where the strategy would have stopped trading in live; the next fold's "recovery" is statistical fiction (we reset per fold) — useful to know.

### `walkforward_distribution.csv` — aggregate stats

One column per metric, one row per aggregate (`mean`, `median`, `std`, `min`, `max`, `mvp_threshold`). Tells you *how the population of folds behaves*.

Key reads:

- **Mean vs median.** Big gap → distribution is skewed by an outlier fold. Median is the more honest central tendency for a small fold count (5–10).
- **`std` of Sharpe across folds.** A useful "regime sensitivity" proxy. Low std (say, < 0.5) means the strategy posts consistent Sharpe across regimes; high std (> 1.0) means it depends on what happened in each test window.
- **`min` Sharpe vs `mean` Sharpe.** If the worst fold's Sharpe is dramatically below the mean, the live path is more dangerous than the headline number suggests — sequence risk amplifies the worst fold.
- **`mvp_threshold` row.** Side-by-side with `mean`. If `mean.sharpe = 1.4` and `mvp_threshold.sharpe = 1.0`, the strategy clears the bar. The `mvp_pass` dict in `walkforward_summary.json` computes the four booleans automatically.

### `walkforward_equity.csv` — stitched OOS curve

A continuous compounded equity curve across all OOS test windows. Tells you *what the strategy would have done* if it had been deployed at the start of OOS testing and run through every fold.

This is the closest thing to a "would I have made money" answer that a backtest can give. The shape of the curve matters as much as the endpoint: a curve that ends up but spent half the time underwater is a different deployment experience than one that grows smoothly. Eyeball:

- **Sharp drawdowns.** Compound losses early in WF pull the entire curve down disproportionately — visually correct (that's live behaviour) but means the early folds matter the most.
- **Plateau periods.** Long flat stretches across multiple folds indicate a strategy that doesn't trade enough to compound. Compare against the `n_trades` distribution.

## Rolling vs anchored — a sanity check

Both modes process the same data; only the train window construction differs. Running both is cheap (one extra walk-forward) and gives a robustness signal:

- **Agreement** (rolling Sharpe ≈ anchored Sharpe): the edge is robust to how much history you train on. Good sign.
- **Disagreement** (anchored Sharpe ≫ rolling Sharpe, or vice versa): the strategy's behaviour depends on either the *length* of training data or the *recency* of training data. This is regime sensitivity — flag for deeper investigation before live.

Default is `rolling` (Pardo's standard). `anchored` is the second opinion.

## Why reset RiskState per fold

Each fold gets a fresh `RiskState` — `equity_peak`, `daily_start_equity`, `open_positions` are all initialised from the fold's starting capital. This is the **literature convention** (Pardo, Aronson) and is the right statistical treatment: folds are independent realisations, and conditional behaviour across folds (the next fold "recovering" the previous fold's drawdown) is fiction.

It is also the **wrong realistic-live treatment.** In live trading there is exactly one continuous equity trajectory; a max-DD halt in real life means the bot is off until manually restarted. A `risk_mode="continuous"` variant is planned for Phase 4 (realistic-live simulation) — out of scope for ADR-009.

For Phase 2 MVP we accept the statistical convention: the answer to "would this strategy clear the MVP criteria across many independent OOS windows" is *exactly* what we want, even if it's not the answer to "would I have made money if I had deployed this two years ago".

## Parameter stability — what walk-forward does NOT do

Walk-forward with a single parameter set tells you whether *those specific parameters* are robust across regimes. It does not tell you whether *the strategy's parameter space* is robust — whether nearby parameter values produce nearby walk-forward results.

That second question is **parameter stability** (ROADMAP line 81), and it requires a parameter sweep wrapped around walk-forward: for each parameter combination, run a full walk-forward, then check that neighbouring parameter combinations produce similar walk-forward Sharpe / max DD. A strategy where one parameter combination clears the MVP criteria but every nearby combination fails is fitting noise, not signal.

Parameter stability is a Phase 2 deliverable. It will land in its own ADR (likely ADR-010 or later) alongside the bghtrend_pullback analysis. For now, walk-forward on a single param set is the gate.

## When walk-forward isn't enough

Walk-forward checks parameter robustness across time. It does *not* check:

- **Survivor bias.** If the data only includes assets that survived to today, walk-forward on them looks better than reality. Mitigated for `algo_bot` by restricting MVP to BTC/USDT and ETH/USDT, both of which existed throughout the backtest window.
- **Microstructure realism.** Slippage, funding, execution latency. The backtester applies bps slippage and a microstructure adjustment, but live can still diverge — Phase 3 paper trading is the real check here.
- **Regime change.** Walk-forward assumes the future will look statistically similar to the past. A crypto market that fundamentally changes (regulation, ETF flow, exchange dominance shift) breaks that assumption. Mitigated operationally by re-walk-forwarding quarterly during live and by Phase 5 alerting on backtest-vs-live drift.

Walk-forward is necessary but not sufficient. Phase 2 → 3 → 4 → 5 each add one more layer of "does this work in reality" before substantial capital is at risk.

## How to use

CLI:

```bash
algo-walkforward \
    --symbol BTC/USDT --timeframe 1h --strategy bghtrend_pullback \
    --params '{"ema_fast": 21, "ema_slow": 55}' \
    --train 365d --test 90d --step 90d \
    --mode rolling \
    --max_dd_pct 0.25
```

Python:

```python
from algo_bot.engine.walkforward import WalkForwardConfig, walk_forward
from algo_bot.risk.limits import RiskLimits
import pandas as pd

report = walk_forward(
    symbol="BTC/USDT",
    timeframe="1h",
    strategy="bghtrend_pullback",
    params={"ema_fast": 21, "ema_slow": 55},
    config=WalkForwardConfig(
        train=pd.Timedelta(days=365),
        test=pd.Timedelta(days=90),
        mode="rolling",
        risk_limits=RiskLimits(max_drawdown_pct=0.25),
    ),
)

if all(report.mvp_pass.values()):
    print("strategy clears Phase 2 MVP criteria — proceed to paper trading")
else:
    failed = [k for k, v in report.mvp_pass.items() if not v]
    print(f"strategy fails on: {failed}")
```

See [reference/modules/walkforward](../reference/modules/walkforward.md) for the full API surface, output structure, edge cases, and limitations.
