"""
algo_bot.risk

Portfolio-level risk management — gates (max drawdown, daily loss, max positions)
i sizing helper (% equity per trade). Pure functions + frozen dataclasses;
stateful tylko poprzez immutable transitions w ``update_state``.

Decyzja: docs/adr/008-risk-limits-module.md.

Public API (re-exports z ``algo_bot.risk.limits``):
    Konfiguracja i state:
        RiskLimits, RiskState, RiskBreach, RiskLimitBreached

    Gates (zwracają ``RiskBreach | None``):
        check_drawdown, check_daily_loss, check_positions, check_all

    State management (immutable):
        init_state, update_state

    Sizing:
        position_size

Typowe użycie z backtester wrappera:

    from algo_bot.risk import RiskLimits, init_state, update_state, check_all, RiskLimitBreached

    limits = RiskLimits(max_drawdown_pct=0.20, daily_loss_pct=0.05)
    state = init_state(equity_start=10_000.0, ts=first_bar_ts, limits=limits)

    # per-bar (w Wrapped.next()):
    state = update_state(state, equity_now=self.equity, ts=bar_ts,
                         open_positions=int(bool(self.position)), limits=limits)
    breach = check_all(state, equity_now=self.equity, ts=bar_ts, limits=limits)
    if breach is not None:
        raise RiskLimitBreached(breach)

See also:
    docs/adr/008-risk-limits-module.md (rationale, alternatives, ordering)
    docs/reference/modules/risk-limits.md (deep reference)
    docs/concepts/risk-management.md (concept orientation)
"""

from algo_bot.risk.limits import (
    RiskBreach,
    RiskLimitBreached,
    RiskLimits,
    RiskState,
    check_all,
    check_daily_loss,
    check_drawdown,
    check_positions,
    init_state,
    position_size,
    update_state,
)

__all__ = [
    "RiskBreach",
    "RiskLimitBreached",
    "RiskLimits",
    "RiskState",
    "check_all",
    "check_daily_loss",
    "check_drawdown",
    "check_positions",
    "init_state",
    "position_size",
    "update_state",
]
