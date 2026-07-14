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
    MarkPriceBar / MarkPriceContext / MaintenanceMarginTier (frozen dataclasses)
    LeveragedPosition / LiquidationEvent (frozen dataclasses)
    load_mark_price_context(...) -> MarkPriceContext
    mark_price_at(ts, symbol, exchange) -> float
    liquidation_price(position, maintenance_margin_rate, ...) -> float
    liquidation_check(position, mark_price, maintenance_margin_rate) -> False | LiquidationEvent
    first_liquidation_event(position, context, start, end) -> LiquidationEvent | None
    maintenance_margin_tiers_from_bybit(rows) -> tuple[MaintenanceMarginTier, ...]
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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import numpy as np
import pandas as pd

from algo_bot.data_integrity import TF_MS, check_mark_price_integrity
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


@dataclass(frozen=True, slots=True)
class MaintenanceMarginTier:
    """Jeden próg risk-limit Bybit dla linear USDT perpetual.

    ``max_position_value=None`` oznacza ostatni, nieograniczony próg. Stawka i
    deduction pochodzą z publicznego ``/v5/market/risk-limit`` i powinny być
    zamrożone wraz z manifestem eksperymentu.
    """

    max_position_value: float | None
    maintenance_margin_rate: float
    maintenance_margin_deduction: float = 0.0

    def __post_init__(self) -> None:
        if self.max_position_value is not None and self.max_position_value <= 0:
            raise ValueError("max_position_value musi być dodatnie albo None")
        if not 0 < self.maintenance_margin_rate < 1:
            raise ValueError("maintenance_margin_rate musi należeć do (0, 1)")
        if self.maintenance_margin_deduction < 0:
            raise ValueError("maintenance_margin_deduction nie może być ujemne")


@dataclass(frozen=True, slots=True)
class MarkPriceBar:
    """Jedna ukończona świeca mark-price; timestampy są UTC."""

    open_time: pd.Timestamp
    close_time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class MarkPriceContext:
    """Przyczynowa seria mark-price i zamrożone progi maintenance margin."""

    symbol: str
    exchange: str
    timeframe: str
    bars: pd.DataFrame
    source: str
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...] = ()
    taker_fee_rate: float = 0.00055

    def __post_init__(self) -> None:
        if self.timeframe not in TF_MS:
            raise ValueError(f"Niewspierany timeframe mark-price: {self.timeframe!r}")
        if not self.symbol.strip() or not self.exchange.strip() or not self.source.strip():
            raise ValueError("symbol, exchange i source nie mogą być puste")
        if not 0 <= self.taker_fee_rate < 1:
            raise ValueError("taker_fee_rate musi należeć do [0, 1)")
        copied = self.bars.copy(deep=True).sort_index()
        report = check_mark_price_integrity(
            copied,
            self.timeframe,
            symbol=self.symbol,
            exchange=self.exchange,
        )
        if not report.ok:
            raise ValueError("MarkPriceContext wymaga kompletnej serii mark-price")
        limits = [tier.max_position_value for tier in self.maintenance_margin_tiers]
        finite_limits = [limit for limit in limits if limit is not None]
        if finite_limits != sorted(finite_limits) or limits.count(None) > 1:
            raise ValueError("maintenance margin tiers muszą być rosnące")
        if None in limits and limits[-1] is not None:
            raise ValueError("nieograniczony maintenance tier musi być ostatni")
        object.__setattr__(self, "bars", copied)

    @property
    def interval(self) -> pd.Timedelta:
        """Długość świecy mark-price."""

        return pd.Timedelta(milliseconds=TF_MS[self.timeframe])

    def completed_bar_at(self, ts: pd.Timestamp) -> MarkPriceBar:
        """Zwraca ostatni bar, którego close nie wypada po ``ts``."""

        timestamp = _ts_to_aware_utc(pd.Timestamp(ts))
        cutoff_open = timestamp - self.interval
        position = int(self.bars.index.searchsorted(cutoff_open, side="right")) - 1
        if position < 0:
            raise LookupError("Brak ukończonego mark-price bara przed timestampem")
        open_time = _ts_to_aware_utc(pd.Timestamp(self.bars.index[position]))
        row = self.bars.iloc[position]
        return MarkPriceBar(
            open_time=open_time,
            close_time=open_time + self.interval,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
        )

    def tier_for(self, position_value: float) -> MaintenanceMarginTier:
        """Wybiera pierwszy risk tier obejmujący dodatni position value."""

        if position_value <= 0:
            raise ValueError("position_value musi być dodatnie")
        for tier in self.maintenance_margin_tiers:
            if tier.max_position_value is None or position_value <= tier.max_position_value:
                return tier
        raise LookupError("Brak maintenance margin tier dla position value")


@dataclass(frozen=True, slots=True)
class LeveragedPosition:
    """Minimalny engine-independent opis isolated linear USDT position."""

    position_id: str
    side: Literal["long", "short"]
    quantity: float
    entry_price: float
    leverage: float
    extra_margin: float = 0.0

    def __post_init__(self) -> None:
        if not self.position_id.strip():
            raise ValueError("position_id nie może być pusty")
        if self.side not in ("long", "short"):
            raise ValueError("side musi być long albo short")
        if self.quantity <= 0 or self.entry_price <= 0 or self.leverage <= 0:
            raise ValueError("quantity, entry_price i leverage muszą być dodatnie")
        if self.extra_margin < 0:
            raise ValueError("extra_margin nie może być ujemny")

    @property
    def entry_notional(self) -> float:
        """Wartość pozycji przy wejściu w quote currency."""

        return self.quantity * self.entry_price


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    """Audytowalny crossing mark-price przez próg isolated liquidation."""

    position_id: str
    side: Literal["long", "short"]
    observed_at: pd.Timestamp | None
    mark_price: float
    liquidation_price: float
    maintenance_margin_rate: float
    maintenance_margin_deduction: float
    source: str | None = None

    def to_dict(self) -> dict[str, str | float | None]:
        """Stabilna reprezentacja do manifestu ``BacktestResult``."""

        return {
            "position_id": self.position_id,
            "side": self.side,
            "observed_at": None if self.observed_at is None else self.observed_at.isoformat(),
            "mark_price": self.mark_price,
            "liquidation_price": self.liquidation_price,
            "maintenance_margin_rate": self.maintenance_margin_rate,
            "maintenance_margin_deduction": self.maintenance_margin_deduction,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> LiquidationEvent:
        """Odtwarza event zapisany w manifeście wyniku."""

        observed = raw.get("observed_at")
        side = str(raw["side"])
        if side not in ("long", "short"):
            raise ValueError(f"Nieprawidłowa strona liquidation event: {side!r}")
        typed_side = cast("Literal['long', 'short']", side)
        return cls(
            position_id=str(raw["position_id"]),
            side=typed_side,
            observed_at=None if observed is None else pd.Timestamp(str(observed)),
            mark_price=float(str(raw["mark_price"])),
            liquidation_price=float(str(raw["liquidation_price"])),
            maintenance_margin_rate=float(str(raw["maintenance_margin_rate"])),
            maintenance_margin_deduction=float(str(raw["maintenance_margin_deduction"])),
            source=None if raw.get("source") is None else str(raw["source"]),
        )


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


def _ts_to_aware_utc(ts: pd.Timestamp) -> pd.Timestamp:
    """Normalizuje pojedynczy timestamp do tz-aware UTC."""

    if ts.tz is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


# ============================================================================
# Mark-price basis i isolated liquidation
# ============================================================================


def _mark_price_path(symbol: str, exchange: str, timeframe: str) -> Path:
    normalized = symbol.upper().split(":", maxsplit=1)[0].replace("/", "").replace("_", "")
    return (
        Path(__file__).resolve().parents[1]
        / "bot_data"
        / "processed"
        / f"{exchange.lower()}_{normalized}_mark_{timeframe}.csv"
    )


def load_mark_price_context(
    symbol: str,
    exchange: str,
    *,
    timeframe: str = "1h",
    maintenance_margin_tiers: tuple[MaintenanceMarginTier, ...] = (),
    taker_fee_rate: float = 0.00055,
    path: Path | None = None,
) -> MarkPriceContext:
    """Ładuje i twardo waliduje przetworzoną serię mark-price."""

    source_path = path or _mark_price_path(symbol, exchange, timeframe)
    frame = pd.read_csv(source_path, parse_dates=["datetime"])
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = frame.set_index("datetime").loc[:, ["Open", "High", "Low", "Close"]]
    return MarkPriceContext(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        bars=frame,
        source=str(source_path),
        maintenance_margin_tiers=maintenance_margin_tiers,
        taker_fee_rate=taker_fee_rate,
    )


def maintenance_margin_tiers_from_bybit(
    rows: Sequence[Mapping[str, object]],
) -> tuple[MaintenanceMarginTier, ...]:
    """Normalizuje publiczną odpowiedź Bybit risk-limit do zamrożonych tierów."""

    tiers: list[MaintenanceMarginTier] = []
    for row in rows:
        try:
            limit = float(str(row["riskLimitValue"]))
            rate = float(str(row["maintenanceMargin"]))
            raw_deduction = row.get("mmDeduction")
            deduction = (
                0.0
                if raw_deduction is None or str(raw_deduction).strip() == ""
                else float(str(raw_deduction))
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Nieprawidłowy Bybit risk tier: {row!r}") from exc
        tiers.append(MaintenanceMarginTier(limit, rate, deduction))
    tiers.sort(
        key=lambda tier: (
            float("inf") if tier.max_position_value is None else tier.max_position_value
        )
    )
    if not tiers:
        raise ValueError("Bybit risk-limit response nie może być pusta")
    return tuple(tiers)


def mark_price_at(ts: pd.Timestamp, symbol: str, exchange: str) -> float:
    """Zwraca Close ostatniego **ukończonego** H1 mark-price bara.

    Plik przechowuje timestamp otwarcia. Odjęcie interwału w
    :meth:`MarkPriceContext.completed_bar_at` zapobiega odczytowi close aktualnie
    formującej się świecy.
    """

    return load_mark_price_context(symbol, exchange).completed_bar_at(pd.Timestamp(ts)).close


def liquidation_price(
    position: LeveragedPosition,
    maintenance_margin_rate: float,
    *,
    maintenance_margin_deduction: float = 0.0,
    taker_fee_rate: float = 0.00055,
) -> float:
    """Liczy aktualny Bybit UTA isolated LP dla linear USDT contract.

    Formuła po zmianie margin calculation z 2025 r. używa entry notional dla
    initial margin, MMR i maintenance-margin deduction oraz korekty extra margin
    o szacowaną opłatę zamknięcia.
    """

    if not 0 < maintenance_margin_rate < 1:
        raise ValueError("maintenance_margin_rate musi należeć do (0, 1)")
    if maintenance_margin_deduction < 0:
        raise ValueError("maintenance_margin_deduction nie może być ujemne")
    if not 0 <= taker_fee_rate < 1:
        raise ValueError("taker_fee_rate musi należeć do [0, 1)")

    quantity = position.quantity
    entry_notional = position.entry_notional
    initial_margin = entry_notional / position.leverage
    if position.side == "long":
        numerator = (
            entry_notional
            - initial_margin
            - position.extra_margin / (1 - taker_fee_rate)
            - maintenance_margin_deduction
        )
        denominator = quantity * (1 - maintenance_margin_rate)
    else:
        numerator = (
            entry_notional
            + initial_margin
            + position.extra_margin / (1 + taker_fee_rate)
            + maintenance_margin_deduction
        )
        denominator = quantity * (1 + maintenance_margin_rate)
    price = numerator / denominator
    if price <= 0 or not np.isfinite(price):
        raise ValueError("Wyliczony liquidation price musi być dodatni i skończony")
    return float(price)


def liquidation_check(
    position: LeveragedPosition,
    mark_price: float,
    maintenance_margin_rate: float,
    *,
    maintenance_margin_deduction: float = 0.0,
    taker_fee_rate: float = 0.00055,
    observed_at: pd.Timestamp | None = None,
    source: str | None = None,
) -> Literal[False] | LiquidationEvent:
    """Zwraca event, gdy mark osiągnął isolated LP, w przeciwnym razie ``False``."""

    if mark_price <= 0 or not np.isfinite(mark_price):
        raise ValueError("mark_price musi być dodatni i skończony")
    threshold = liquidation_price(
        position,
        maintenance_margin_rate,
        maintenance_margin_deduction=maintenance_margin_deduction,
        taker_fee_rate=taker_fee_rate,
    )
    crossed = mark_price <= threshold if position.side == "long" else mark_price >= threshold
    if not crossed:
        return False
    return LiquidationEvent(
        position_id=position.position_id,
        side=position.side,
        observed_at=None if observed_at is None else _ts_to_aware_utc(observed_at),
        mark_price=float(mark_price),
        liquidation_price=threshold,
        maintenance_margin_rate=maintenance_margin_rate,
        maintenance_margin_deduction=maintenance_margin_deduction,
        source=source,
    )


def first_liquidation_event(
    position: LeveragedPosition,
    context: MarkPriceContext,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> LiquidationEvent | None:
    """Skanuje ukończone mark OHLC i zwraca pierwszy crossing w ``(start, end]``.

    Long używa Low, short używa High, więc H1 mark zachowuje intrabar crossing
    bez pobierania M5 mark-price. Brak eventu oznacza bezpieczeństwo wyłącznie
    przy kompletnej serii, co gwarantuje konstruktor ``MarkPriceContext``.
    """

    start_utc = _ts_to_aware_utc(pd.Timestamp(start))
    end_utc = _ts_to_aware_utc(pd.Timestamp(end))
    if end_utc < start_utc:
        raise ValueError("end nie może poprzedzać start")
    tier = context.tier_for(position.entry_notional)
    for open_time, row in context.bars.iterrows():
        open_utc = _ts_to_aware_utc(pd.Timestamp(str(open_time)))
        close_time = open_utc + context.interval
        if close_time <= start_utc:
            continue
        if close_time > end_utc:
            break
        adverse_mark = float(row["Low"] if position.side == "long" else row["High"])
        event = liquidation_check(
            position,
            adverse_mark,
            tier.maintenance_margin_rate,
            maintenance_margin_deduction=tier.maintenance_margin_deduction,
            taker_fee_rate=context.taker_fee_rate,
            observed_at=close_time,
            source=context.source,
        )
        if event is not False:
            return event
    return None


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
