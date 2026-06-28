"""algo_bot/microstructure.py — korekty mikrostrukturalne backtestu (slippage + funding).

Czysta warstwa post-processingu nakładana na surowy wynik ``run_backtest``
(ADR-011). Silnik ``backtesting.py`` liczy equity tylko z prowizją giełdy
(``commission`` = taker fee); ten moduł dokłada dwa koszty których silnik nie
modeluje:

- **slippage** — poślizg taker ordera względem idealnej ceny fill zakładanej
  przez backtest. Stały ``slip_bps`` per side, debet cash przy entry i exit.
- **funding** — koszt trzymania perp futures, naliczany przez Binance co 8h
  (00/08/16 UTC) tylko dla pozycji otwartych w momencie settlementu.
  ``Funding Amount = Notional * Funding Rate``; long płaci gdy rate > 0.

Oba koszty są odejmowane od surowej krzywej equity → ``equity_adjusted``, oraz
od PnL per trade → ``trades_pnl_adjusted``. Caller (backtester) liczy
``summarize()`` na obu wersjach: ``_metrics_summary_raw`` (z fee silnika) vs
``_metrics_summary_post_microstructure`` (− slippage − funding).

Konwencja "raw vs post": **fee giełdy należy do raw** (siedzi w silniku jako
``commission``); slippage i funding to warstwa "post".

Aproksymacja składania: ``equity_adjusted[t] = equity_raw[t] − Σ kosztów ≤ t``
(równoległe przesunięcie rosnące w czasie). Przy kosztach rzędu kilkunastu bps
to efekt drugiego rzędu — patrz ADR-011 §15.

Wszystkie timestampy są normalizowane wewnętrznie do **tz-naive UTC**, żeby
porównania equity / trades / funding były spójne niezależnie od źródła danych.

Public API:
    MicrostructureConfig (frozen dataclass)
    TradeCost (frozen dataclass)
    MicrostructureResult (frozen dataclass)
    slippage_cost(notional, slip_bps) -> float
    settlements_in_window(entry_time, exit_time, funding_index) -> pd.DatetimeIndex
    synthetic_funding_series(start, end, rate, hours_utc) -> pd.Series
    resolve_funding(historical, start, end, config) -> pd.Series
    funding_flows_for_trade(...) -> list[tuple[pd.Timestamp, float]]
    funding_cost_for_trade(...) -> tuple[float, int]
    apply_microstructure(equity_raw, trades, ohlcv, funding, config) -> MicrostructureResult

See also:
    docs/adr/011-microstructure-adjustments.md (rationale, alternatives, defaults)
    docs/reference/modules/microstructure.md (deep reference)
    docs/concepts/microstructure.md (mechanika perp futures, dlaczego 5-10 bps)
    docs/adr/007-risk-adjusted-metrics.md (summarize() konsumuje equity_adjusted)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from algo_bot.log import get_logger

logger = get_logger(__name__)

# Konwersja bps → fraction (1 bp = 0.01% = 1e-4).
_BPS: float = 1e4


# ============================================================================
# Frozen dataclasses
# ============================================================================


@dataclass(frozen=True)
class MicrostructureConfig:
    """Konfiguracja korekt mikrostrukturalnych. Niezmienna po utworzeniu.

    Attributes:
        enabled: Master switch. ``False`` (``--microstructure none``) → equity i
            trades zwracane bez zmian, koszty zerowe.
        slip_bps: Slippage per side w basis points, na TOP of fee silnika.
            Default 1.0 bp — realistyczne dla płynnych BTC/ETH USDT-M perp przy
            size bghtrend (ADR-011 §3). Round-trip ≈ 2 * slip_bps.
        funding_source: ``"historical"`` (CSV, synthetic fallback na braki),
            ``"synthetic"`` (stała), ``"none"`` (bez funding; slippage może
            nadal działać).
        funding_rate_synthetic: Stały rate per 8h dla trybu synthetic /
            fallback. Default 0.0001 (0.01%) = interest component Binance.
        settlement_hours_utc: Godziny settlementu UTC dla syntetycznej siatki.
            Historical używa realnych ``fundingTime`` z CSV, nie tej siatki.
    """

    enabled: bool = True
    slip_bps: float = 1.0
    funding_source: Literal["historical", "synthetic", "none"] = "historical"
    funding_rate_synthetic: float = 0.0001
    settlement_hours_utc: tuple[int, ...] = (0, 8, 16)


@dataclass(frozen=True)
class TradeCost:
    """Rozbicie kosztów mikrostrukturalnych pojedynczego trade'u (audit trail).

    Attributes:
        entry_time: Timestamp wejścia (tz-naive UTC).
        exit_time: Timestamp wyjścia (tz-naive UTC).
        side: ``"long"`` albo ``"short"``.
        notional_entry: ``|size| * entry_price`` (quote).
        notional_exit: ``|size| * exit_price`` (quote).
        slip_cost_quote: ``slip_bps/1e4 * (notional_entry + notional_exit)``.
        funding_cost_quote: Σ po settlementach; dodatni = zapłacone, ujemny =
            otrzymane.
        n_settlements: Liczba settlementów funding w oknie [entry, exit).
        pnl_raw: Surowy PnL z silnika (z fee, bez slip/funding).
        pnl_post: ``pnl_raw − slip_cost_quote − funding_cost_quote``.
    """

    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: Literal["long", "short"]
    notional_entry: float
    notional_exit: float
    slip_cost_quote: float
    funding_cost_quote: float
    n_settlements: int
    pnl_raw: float
    pnl_post: float


@dataclass(frozen=True)
class MicrostructureResult:
    """Wynik nałożenia warstwy mikrostruktury na surowy backtest.

    Attributes:
        equity_adjusted: Surowa equity − skumulowane koszty (DatetimeIndex,
            tz-naive UTC). Przy ``enabled=False`` identyczne z surową equity.
        trades_pnl_adjusted: PnL per trade po korektach (``pnl_post``).
            Indeks RangeIndex (kolejność trade'ów); kolejność nieistotna dla
            ``profit_factor`` / ``win_rate``.
        per_trade: Krotka ``TradeCost`` w kolejności trade'ów.
        total_slip_quote: Suma kosztów slippage (quote).
        total_funding_quote: Suma kosztów funding (quote; netto, z znakiem).
        config: Użyta konfiguracja.
    """

    equity_adjusted: pd.Series
    trades_pnl_adjusted: pd.Series
    per_trade: tuple[TradeCost, ...]
    total_slip_quote: float
    total_funding_quote: float
    config: MicrostructureConfig


# ============================================================================
# Helpery tz
# ============================================================================


def _index_to_naive_utc(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Normalizuje ``DatetimeIndex`` do tz-naive UTC.

    tz-aware → konwersja do UTC + zdjęcie tz; tz-naive → bez zmian (zakładamy
    że już jest w UTC). Patrz ``feedback_pandas_ns_tz`` — zdejmujemy tz zanim
    cokolwiek porównujemy/rzutujemy.
    """
    if index.tz is not None:
        return index.tz_convert("UTC").tz_localize(None)
    return index


def _ts_to_naive_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """Normalizuje pojedynczy ``Timestamp`` do tz-naive UTC."""
    if ts.tz is not None:
        return ts.tz_convert("UTC").tz_localize(None)
    return ts


def _mark_at(close: pd.Series, ts: pd.Timestamp) -> float:
    """Mark price w momencie settlementu ≈ ostatni ``Close`` z indeksem ≤ ts.

    Pozycyjnie: ``searchsorted(side="right") - 1`` daje ostatni bar z indeksem
    ≤ ``ts`` (semantyka ``Series.asof``, ale bez jego szerokiego typu zwrotnego).
    Gdy ``ts`` poprzedza pierwszy bar (nie powinno się zdarzyć — settlement jest
    w oknie trade'u), clamp do pierwszego ``Close``. Aproksymacja marku przez
    Close bara — ADR-011 §5. Wymaga posortowanego indeksu (gwarantowane przez
    caller).
    """
    pos = int(close.index.searchsorted(ts, side="right")) - 1
    if pos < 0:
        pos = 0
    return float(close.to_numpy()[pos])


# ============================================================================
# Slippage
# ============================================================================


def slippage_cost(notional: float, slip_bps: float) -> float:
    """Koszt slippage jednej nogi: ``|notional| * slip_bps / 1e4``.

    Args:
        notional: Wartość nominalna nogi (quote). Bierzemy wartość bezwzględną.
        slip_bps: Slippage w basis points.

    Returns:
        Koszt w quote (zawsze >= 0).
    """
    return abs(notional) * slip_bps / _BPS


# ============================================================================
# Funding
# ============================================================================


def settlements_in_window(
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    funding_index: pd.DatetimeIndex,
) -> pd.DatetimeIndex:
    """Timestampy settlementów, w których pozycja była otwarta.

    Konwencja half-open ``(entry, exit]`` (ADR-011 §5): pozycja jest naliczana
    od entry (exclusive — wejście market fill'uje tuż PO snapshocie funding)
    do exit (inclusive — trzymamy do snapshotu w momencie zamknięcia). Przy
    coincydencji bara z settlementem (1h/4h TF) to rozstrzyga edge: settlement
    dokładnie na entry NIE jest płacony, dokładnie na exit JEST.

    Args:
        entry_time: Timestamp wejścia (tz-naive UTC).
        exit_time: Timestamp wyjścia (tz-naive UTC).
        funding_index: Indeks settlementów (tz-naive UTC, posortowany).

    Returns:
        Podzbiór ``funding_index`` w oknie ``(entry, exit]``.
    """
    mask = (funding_index > entry_time) & (funding_index <= exit_time)
    return pd.DatetimeIndex(funding_index[mask])


def synthetic_funding_series(
    start: pd.Timestamp,
    end: pd.Timestamp,
    rate: float,
    hours_utc: tuple[int, ...] = (0, 8, 16),
) -> pd.Series:
    """Syntetyczna seria stałego funding rate na siatce ``hours_utc``.

    Args:
        start: Początek zakresu (inclusive).
        end: Koniec zakresu (inclusive).
        rate: Stały funding rate per settlement (np. 0.0001 = 0.01%).
        hours_utc: Godziny UTC settlementu (default 00/08/16).

    Returns:
        ``pd.Series`` indeksowana settlementami (tz-naive UTC), wartość = rate.
        Pusta gdy zakres pusty.
    """
    start_n = _ts_to_naive_utc(start)
    end_n = _ts_to_naive_utc(end)
    if end_n < start_n:
        return pd.Series(dtype=float, name="funding_rate")

    days = pd.date_range(start_n.normalize(), end_n.normalize(), freq="D")
    stamps: list[pd.Timestamp] = []
    for day in days:
        for hour in sorted(set(hours_utc)):
            s = day + pd.Timedelta(hours=hour)
            if start_n <= s <= end_n:
                stamps.append(s)
    idx = pd.DatetimeIndex(sorted(stamps))
    return pd.Series(float(rate), index=idx, name="funding_rate")


def resolve_funding(
    historical: pd.Series | None,
    start: pd.Timestamp,
    end: pd.Timestamp,
    config: MicrostructureConfig,
) -> pd.Series:
    """Rozstrzyga źródło funding zgodnie z ``config.funding_source`` (Decyzja 6c).

    ``"none"`` → pusta seria. ``"synthetic"`` → stała siatka. ``"historical"`` →
    dane z CSV; przy braku/częściowym pokryciu zakresu ``[start, end]`` luki są
    wypełniane syntetykiem z ``logger.warning``.

    Args:
        historical: Załadowana historia funding (Series[rate], DatetimeIndex)
            albo ``None``.
        start: Początek backtestu.
        end: Koniec backtestu.
        config: Konfiguracja.

    Returns:
        ``pd.Series`` funding rate (tz-naive UTC, posortowana, bez duplikatów).
    """
    if config.funding_source == "none":
        return pd.Series(dtype=float, name="funding_rate")

    rate = config.funding_rate_synthetic
    hours = config.settlement_hours_utc

    if config.funding_source == "synthetic":
        return synthetic_funding_series(start, end, rate, hours)

    # historical (z fallbackiem)
    if historical is None or historical.empty:
        logger.warning(
            "Funding history missing — using synthetic constant",
            extra={"funding_rate_synthetic": rate},
        )
        return synthetic_funding_series(start, end, rate, hours)

    hist = historical.astype(float).copy()
    hist.index = _index_to_naive_utc(pd.DatetimeIndex(hist.index))
    hist = hist.sort_index()
    hist = hist[~hist.index.duplicated(keep="first")]

    start_n = _ts_to_naive_utc(start)
    end_n = _ts_to_naive_utc(end)
    hist_in = hist.loc[(hist.index >= start_n) & (hist.index <= end_n)]

    if hist_in.empty:
        logger.warning(
            "Funding history does not cover backtest range — using synthetic",
            extra={"start": str(start_n), "end": str(end_n)},
        )
        return synthetic_funding_series(start, end, rate, hours)

    # Pokrycie częściowe: synthetic wypełnia settlementy poza zakresem historii.
    synth = synthetic_funding_series(start, end, rate, hours)
    lo = hist_in.index.min()
    hi = hist_in.index.max()
    synth_gap = synth[(synth.index < lo) | (synth.index > hi)]
    if not synth_gap.empty:
        logger.warning(
            "Funding history partial — synthetic fills the gap",
            extra={
                "hist_coverage": [str(lo), str(hi)],
                "requested": [str(start_n), str(end_n)],
                "n_synthetic_gap": len(synth_gap),
            },
        )
    combined: pd.Series = pd.concat([hist_in, synth_gap]).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    combined.name = "funding_rate"
    return combined


def funding_flows_for_trade(
    *,
    side: Literal["long", "short"],
    size: float,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    funding: pd.Series,
    mark: pd.Series,
) -> list[tuple[pd.Timestamp, float]]:
    """Lista przepływów funding (timestamp, koszt) dla trade'u.

    Koszt per settlement: ``side_sign * |size| * mark(s) * rate(s)``, gdzie
    ``side_sign = +1`` dla long (płaci gdy rate > 0), ``-1`` dla short. Dodatni
    koszt = zapłacone (debet equity), ujemny = otrzymane (kredyt).

    Args:
        side: ``"long"`` / ``"short"``.
        size: Rozmiar pozycji (znak ignorowany — bierzemy ``|size|``).
        entry_time: Wejście (tz-naive UTC).
        exit_time: Wyjście (tz-naive UTC).
        funding: Seria funding rate (tz-naive UTC, bez duplikatów).
        mark: Seria mark price (``Close``, tz-naive UTC) do wyceny notional.

    Returns:
        Lista ``(settlement_ts, signed_cost_quote)``. Pusta gdy brak settlementów.
    """
    if funding.empty:
        return []
    side_sign = 1.0 if side == "long" else -1.0
    abs_size = abs(float(size))
    settlements = settlements_in_window(entry_time, exit_time, pd.DatetimeIndex(funding.index))
    flows: list[tuple[pd.Timestamp, float]] = []
    for s in settlements:
        rate = float(funding.loc[s])
        notional = abs_size * _mark_at(mark, s)
        flows.append((s, side_sign * notional * rate))
    return flows


def funding_cost_for_trade(
    *,
    side: Literal["long", "short"],
    size: float,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    funding: pd.Series,
    mark: pd.Series,
) -> tuple[float, int]:
    """Sumaryczny koszt funding trade'u + liczba settlementów.

    Cienki wrapper na ``funding_flows_for_trade`` — zwraca ``(suma, n)``.
    Dodatnia suma = zapłacone netto.
    """
    flows = funding_flows_for_trade(
        side=side,
        size=size,
        entry_time=entry_time,
        exit_time=exit_time,
        funding=funding,
        mark=mark,
    )
    total = float(sum(amount for _, amount in flows))
    return total, len(flows)


# ============================================================================
# Orchestrator
# ============================================================================


def _add_cost(
    costs: np.ndarray,
    index: pd.DatetimeIndex,
    ts: pd.Timestamp,
    amount: float,
) -> None:
    """Dodaje koszt ``amount`` do pozycji na osi ``index`` odpowiadającej ``ts``.

    ``searchsorted(side="left")`` zwraca pozycję bara dla dokładnego trafienia,
    albo następnego bara gdy ``ts`` wypada między barami. Poza końcem serii →
    przyklejamy do ostatniego bara.
    """
    pos = int(index.searchsorted(ts, side="left"))
    if pos >= len(costs):
        pos = len(costs) - 1
    if pos < 0:
        pos = 0
    costs[pos] += amount


def apply_microstructure(
    *,
    equity_raw: pd.Series,
    trades: pd.DataFrame,
    ohlcv: pd.DataFrame,
    funding: pd.Series | None,
    config: MicrostructureConfig,
) -> MicrostructureResult:
    """Nakłada slippage + funding na surowy equity/trades. Czysta funkcja (bez I/O).

    Args:
        equity_raw: Surowa equity z silnika (``pd.Series``, DatetimeIndex).
        trades: Surowe trade'y z silnika. Wymagane kolumny:
            ``EntryTime, ExitTime, EntryPrice, ExitPrice, Size, PnL``.
        ohlcv: OHLCV backtestu (kolumna ``Close`` jako mark do wyceny funding).
        funding: Rozstrzygnięta seria funding (z ``resolve_funding``) albo ``None``.
        config: Konfiguracja.

    Returns:
        ``MicrostructureResult`` z dopasowaną equity, PnL per trade i breakdownem.

    Edge cases (ADR-011 §12):
        ``enabled=False`` lub brak trade'ów → equity/PnL bez zmian, koszty 0.
        Trade bez settlementu w oknie → ``funding_cost_quote = 0``.
    """
    eq = equity_raw.copy()
    eq.index = _index_to_naive_utc(pd.DatetimeIndex(eq.index))

    has_trades = not trades.empty and "PnL" in trades.columns
    raw_pnl = (
        trades["PnL"].astype(float).reset_index(drop=True) if has_trades else pd.Series(dtype=float)
    )

    if not config.enabled or not has_trades:
        return MicrostructureResult(
            equity_adjusted=eq,
            trades_pnl_adjusted=raw_pnl,
            per_trade=(),
            total_slip_quote=0.0,
            total_funding_quote=0.0,
            config=config,
        )

    # Mark = Close, znormalizowany.
    close = ohlcv["Close"].astype(float).copy()
    close.index = _index_to_naive_utc(pd.DatetimeIndex(close.index))
    close = close.sort_index()

    # Funding znormalizowany (bez duplikatów).
    fund: pd.Series | None = None
    if funding is not None and not funding.empty:
        fund = funding.astype(float).copy()
        fund.index = _index_to_naive_utc(pd.DatetimeIndex(fund.index))
        fund = fund.sort_index()
        fund = fund[~fund.index.duplicated(keep="first")]

    # Kolumny trade'ów → znormalizowane wektory.
    entry_times = _index_to_naive_utc(pd.DatetimeIndex(pd.to_datetime(trades["EntryTime"])))
    exit_times = _index_to_naive_utc(pd.DatetimeIndex(pd.to_datetime(trades["ExitTime"])))
    sizes = trades["Size"].astype(float).to_numpy()
    entry_prices = trades["EntryPrice"].astype(float).to_numpy()
    exit_prices = trades["ExitPrice"].astype(float).to_numpy()
    pnls = trades["PnL"].astype(float).to_numpy()

    # slip_frac = config.slip_bps / _BPS
    costs = np.zeros(len(eq), dtype=float)
    per_trade: list[TradeCost] = []
    pnl_post_list: list[float] = []
    total_slip = 0.0
    total_funding = 0.0

    for i in range(len(sizes)):
        size = float(sizes[i])
        side: Literal["long", "short"]
        side = "long" if size >= 0 else "short"
        entry_time = entry_times[i]
        exit_time = exit_times[i]
        notional_entry = abs(size) * float(entry_prices[i])
        notional_exit = abs(size) * float(exit_prices[i])

        slip_entry = slippage_cost(notional_entry, config.slip_bps)
        slip_exit = slippage_cost(notional_exit, config.slip_bps)
        slip_cost = slip_entry + slip_exit

        flows: list[tuple[pd.Timestamp, float]] = []
        if fund is not None:
            flows = funding_flows_for_trade(
                side=side,
                size=size,
                entry_time=entry_time,
                exit_time=exit_time,
                funding=fund,
                mark=close,
            )
        funding_cost = float(sum(amount for _, amount in flows))
        n_settle = len(flows)

        pnl_raw = float(pnls[i])
        pnl_post = pnl_raw - slip_cost - funding_cost

        # Rozłożenie kosztów na osi czasu equity.
        _add_cost(costs, eq.index, entry_time, slip_entry)
        _add_cost(costs, eq.index, exit_time, slip_exit)
        for settle_ts, amount in flows:
            _add_cost(costs, eq.index, settle_ts, amount)

        total_slip += slip_cost
        total_funding += funding_cost
        pnl_post_list.append(pnl_post)
        per_trade.append(
            TradeCost(
                entry_time=entry_time,
                exit_time=exit_time,
                side=side,
                notional_entry=notional_entry,
                notional_exit=notional_exit,
                slip_cost_quote=slip_cost,
                funding_cost_quote=funding_cost,
                n_settlements=n_settle,
                pnl_raw=pnl_raw,
                pnl_post=pnl_post,
            )
        )

    cumulative = pd.Series(costs, index=eq.index).cumsum()
    equity_adjusted = eq - cumulative
    trades_pnl_adjusted = pd.Series(pnl_post_list, dtype=float)

    logger.info(
        "Microstructure applied",
        extra={
            "n_trades": len(per_trade),
            "slip_bps": config.slip_bps,
            "funding_source": config.funding_source,
            "total_slip_quote": total_slip,
            "total_funding_quote": total_funding,
        },
    )

    return MicrostructureResult(
        equity_adjusted=equity_adjusted,
        trades_pnl_adjusted=trades_pnl_adjusted,
        per_trade=tuple(per_trade),
        total_slip_quote=total_slip,
        total_funding_quote=total_funding,
        config=config,
    )
