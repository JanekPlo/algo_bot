# ADR-013: WF-eligibility thresholds — a pre-WF filter distinct from the go-live gate

- **Status:** Accepted
- **Date:** 2026-07-05
- **Project phase:** 2 (Research & Backtest MVP)
- **Authors:** Janek Płoński, Claude

## Context

The Session 4 sweep review and `docs/guides/running-sweep.md` used an in-line "worth walk-forward" filter with an in-sample `sharpe_post > 1.5` bar. That `1.5` was set arbitrarily — a gut number for "clearly better than the OOS target so it survives decay", never derived. Reviewing it after Session 4 (where 0/150 configs passed and the whole exercise turned on where the bar sits), two problems surfaced: (1) the threshold was a magic literal duplicated between the notebook and the guide, and (2) `1.5` conflated two different questions with the `MVP_THRESHOLDS` go-live gate (ADR-009).

The two questions are distinct. `MVP_THRESHOLDS` (Sharpe ≥ 1.0, maxDD ≥ -0.25, PF ≥ 1.3, n_trades ≥ 50) is a **post-WF go-live gate**: "is this ready for testnet/live?" — measured on out-of-sample walk-forward output. WF-eligibility is a **pre-WF filter**: "is this in-sample sweep sample worth the expensive walk-forward at all?" Using the same number, or a higher arbitrary one, for both blurs the boundary.

## Decision

**Introduce `WF_ELIGIBILITY_THRESHOLDS` in `algo_bot.engine.walkforward` as a named constant, separate from `MVP_THRESHOLDS` (which is unchanged):**

```python
WF_ELIGIBILITY_THRESHOLDS = {
    "sharpe": 1.0,
    "profit_factor": 1.3,
    "n_trades": 100.0,
    "max_drawdown_pct": -0.20,
}
```

The notebook (`03_...ipynb`) and `running-sweep.md` import this constant instead of hardcoding thresholds. It is **not** wired into `compute_mvp_pass` — it is an operator-facing filter over `index.csv`, not a post-WF gate.

**Rationale for the numbers.** Sharpe `1.0` (down from the arbitrary `1.5`): with a realistic IS→OOS decay of 0.5–0.7×, an in-sample Sharpe of 1.0 maps to ~0.5–0.7 OOS — low, but enough that entering walk-forward *to see the decay* is worthwhile (WF compute is cheap relative to discarding a real edge on a guessed bar). This does **not** conflict with the `MVP_THRESHOLDS` Sharpe of 1.0: there `1.0` is the *post-WF* go-live requirement; here `1.0` is the *pre-WF* entry ticket. `n_trades` `100` and DD `-0.20` are intentionally **stricter** than the go-live gate (50 / -0.25): in-sample it is cheap to accumulate trades, and Session 4 showed high Sharpe sitting on `n_trades ≈ 1` (statistically empty), so the pre-filter demands more statistics and a tighter drawdown before committing WF compute.

Regime robustness (rolling per-year Sharpe) is handled as a **soft judgment step** in `running-sweep.md`, deliberately *not* a constant — see that guide and the Notes below.

## Consequences

**Positive:** one source of truth for the pre-WF bar (no duplicated literals); the pre-WF vs post-WF distinction is explicit in code and docs; the bar now has a documented rationale (decay math) instead of a gut number.

**Negative:** a second thresholds constant to keep straight — mitigated by the module comment and the test asserting the two are distinct with the intended relations (`n_trades` and DD stricter than MVP).

**Risk:** `1.0` may still be too generous or too strict for a different strategy family; it is a constant precisely so a future ADR can revise it with evidence rather than editing scattered literals.

## Alternatives Considered

- **Update `MVP_THRESHOLDS` in place / reuse it as the filter.** Rejected — collapses the pre-WF vs post-WF distinction; the go-live gate must not move without an economic basis (ADR-009), and it has different n_trades/DD needs.
- **Keep the `1.5` literal, just move it to a constant.** Rejected — the value was never justified; the recalibration to `1.0` is the substance of this ADR, not just the extraction.
- **CHANGELOG-only, no ADR.** Rejected — post-Session 4 the meaning of "worth WF" is load-bearing for the pivot decision (ADR-012) and deserves a findable rationale, not just a diff.
- **Hard-code regime robustness as a fifth threshold.** Rejected — per-year Sharpe is an interpretation call (7 numbers an operator weighs), not a clean cutoff; kept soft in the guide.

## References

- Code: `algo_bot/engine/walkforward.py` (`WF_ELIGIBILITY_THRESHOLDS`, alongside unchanged `MVP_THRESHOLDS`)
- Tests: `tests/test_walkforward.py` (`TestWfEligibilityThresholds`)
- Consumers: `notebooks/03_bghtrend_sweep_and_walkforward.ipynb` §1, `docs/guides/running-sweep.md`
- Related ADRs: ADR-009 (`MVP_THRESHOLDS`, the post-WF go-live gate), ADR-012 (bghtrend no-go — the decision this filter's calibration informed)
