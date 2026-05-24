"""
algo_bot/risk/limits.py

Portfolio-level risk limits — pure functions + frozen dataclasses. Trzy gates
(max drawdown vs peak equity, daily loss vs daily-start equity, max concurrent
positions) zwracają ``RiskBreach | None``. Sizing helper ``position_size`` jest
pure calculator (% equity / stop distance), wołany explicit przez strategie —
NIE auto-injection w wrapperze (decyzja Janka, ADR-008 §8).

Backtester wrapper tłumaczy ``RiskBreach`` na ``RiskLimitBreached`` exception
żeby halt-the-run semantyka żyła tam gdzie ma sens (engine). Pure layer jest
agnostyczny — używalny też z walk-forward (fold inspection) i live (graceful
shutdown z alertem).

Decyzja: docs/adr/008-risk-limits-module.md.

Public API:
    Konfiguracja i state (frozen dataclasses):
        RiskLimits, RiskState, RiskBreach

    Exception:
        RiskLimitBreached(breach: RiskBreach)

    Gates (pure):
        check_drawdown(state, equity_now, limits) -> RiskBreach | None
        check_daily_loss(state, equity_now, ts, limits) -> RiskBreach | None
        check_positions(state, limits) -> RiskBreach | None
        check_all(state, equity_now, ts, limits) -> RiskBreach | None
            # first-hit: drawdown → daily_loss → max_positions

    State (immutable transitions):
        init_state(equity_start, ts, limits) -> RiskState
        update_state(state, equity_now, ts, open_positions, limits) -> RiskState

    Sizing (pure helper, caller-driven):
        position_size(equity_now, sl_distance, risk_per_trade_pct) -> float

Konwencje:
    - ``None`` w polach RiskLimits wyłącza dany limit.
    - ``RiskState`` jest immutable; ``update_state`` zwraca nową instancję.
    - Daily reset porównuje znormalizowane dni w ``daily_reset_tz`` (default "UTC").
    - Edge cases (zero/negative sl_distance, nieznane tz, single-bar series)
      emitują ``logger.warning`` zamiast cichego błędu.
    - Pure funkcje nie logują same — logging breach robi caller (wrapper),
      żeby pure layer nie miał side-effectów.

See also:
    docs/adr/008-risk-limits-module.md (rationale, alternatives, ordering)
    docs/reference/modules/risk-limits.md (deep reference)
    algo_bot/metrics.py (max_drawdown helper consumed konceptualnie)
    algo_bot/log.py (get_logger dla edge-case warningów)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from algo_bot.log import get_logger

logger = get_logger(__name__)

BreachKind = Literal["max_drawdown", "daily_loss", "max_positions"]


# ============================================================================
# Konfiguracja i state
# ============================================================================


@dataclass(frozen=True)
class RiskLimits:
    """Konfiguracja limitów ryzyka portfolio-level.

    Każde pole jest opcjonalne — ``None`` wyłącza dany limit (gate zwraca None
    natychmiast). Pozwala to skomponować np. "tylko max DD" bez wprowadzania
    sentineli typu 0.0/inf, które byłyby ambiguous (czy 0.0 to "disabled" czy
    "halt-on-any-loss"?).

    Attributes:
        max_drawdown_pct: Próg DD od peak equity, np. 0.20 = 20%. Breach gdy
            ``(equity_peak - equity_now) / equity_peak >= max_drawdown_pct``.
            ``None`` → wyłączone.
        daily_loss_pct: Próg straty od equity na początku dnia (w ``daily_reset_tz``),
            np. 0.05 = 5%. Breach gdy ``(daily_start_equity - equity_now) /
            daily_start_equity >= daily_loss_pct``. ``None`` → wyłączone.
        max_concurrent_positions: Maks. liczba otwartych pozycji jednocześnie.
            Na MVP single-symbol typowo nieużywane (None). Breach gdy
            ``open_positions > max_concurrent_positions``. ``None`` → wyłączone.
        risk_per_trade_pct: Procent equity ryzykowany na pojedynczy trade
            (używany przez ``position_size``). Nie auto-injected w wrapperze
            (ADR-008 §8) — strategia woła ``position_size`` explicit i wpisuje
            wynik do ``Signal.size``. ``None`` → wyłączone / strategia robi
            własny sizing.
        daily_reset_tz: IANA timezone string definiująca granicę dnia dla
            ``daily_loss_pct``. Default ``"UTC"`` (industry standard,
            Binance/Bybit funding cycle). Configurable do np. ``"Europe/Warsaw"``
            jeśli user chce alignment z lokalnym TZ.
    """

    max_drawdown_pct: float | None = None
    daily_loss_pct: float | None = None
    max_concurrent_positions: int | None = None
    risk_per_trade_pct: float | None = None
    daily_reset_tz: str = "UTC"


@dataclass(frozen=True)
class RiskState:
    """Per-bar state utrzymywany przez caller (backtester wrapper).

    Immutable — ``update_state`` zwraca nową instancję, nie modyfikuje miejscowo.
    Caller (typowo ``Wrapped(BTStrategy).next()``) trzyma najnowszą instancję
    w lokalnym atrybucie.

    Attributes:
        equity_peak: Najwyższe equity zaobserwowane od początku runu. Używane
            przez ``check_drawdown``.
        daily_start_equity: Equity na początku bieżącego dnia (w
            ``RiskLimits.daily_reset_tz``). Używane przez ``check_daily_loss``.
        daily_start_day: Znormalizowany dzień (``Timestamp`` o godz. 00:00 bez
            tz-info) odpowiadający bieżącemu okienku daily_loss. ``update_state``
            porównuje z bieżącym dniem żeby wykryć reset.
        open_positions: Liczba otwartych pozycji w momencie tego stanu.
            Backtester wrapper aktualizuje tę wartość per bar (typowo
            ``int(bool(self.position))`` dla single-symbol).
    """

    equity_peak: float
    daily_start_equity: float
    daily_start_day: pd.Timestamp
    open_positions: int


@dataclass(frozen=True)
class RiskBreach:
    """Reprezentacja naruszonego limitu — czysta dana, bez side-effectów.

    Pure funkcje ``check_*`` zwracają instancję tej klasy gdy próg jest
    przekroczony, albo ``None`` gdy wszystko OK. Backtester wrapper bierze
    instancję i pakuje w ``RiskLimitBreached`` exception (halt-the-run).
    Walk-forward (Decision F) może agregować ``RiskBreach`` per fold bez
    halt — to dlatego separujemy data od control flow.

    Attributes:
        kind: Który limit się rozszczelnił.
        value: Zaobserwowana wartość (np. -0.25 dla -25% DD, lub 3 dla
            ``open_positions=3`` przy limicie 2).
        threshold: Skonfigurowany próg z ``RiskLimits``.
        ts: Timestamp bara na którym breach wystąpił (UTC, z tz-info).
        message: Czytelny dla człowieka opis — używany w logach i raportach.
    """

    kind: BreachKind
    value: float
    threshold: float
    ts: pd.Timestamp
    message: str


class RiskLimitBreached(Exception):
    """Exception podnoszony przez backtester wrapper na breach limitu ryzyka.

    Niesie pełną instancję ``RiskBreach`` — caller (``run_backtest``) łapie ten
    konkretny typ i serializuje breach do ``stats["_risk_breach"]``. Wszystkie
    inne wyjątki są bug-i i propagują dalej.
    """

    def __init__(self, breach: RiskBreach) -> None:
        super().__init__(breach.message)
        self._breach = breach

    @property
    def breach(self) -> RiskBreach:
        """Zwraca instancję RiskBreach przekazaną przy konstrukcji."""
        return self._breach


# ============================================================================
# Helpers — daily reset day normalization
# ============================================================================


def _normalize_day(ts: pd.Timestamp, tz_name: str) -> pd.Timestamp:
    """Zwraca znormalizowany dzień (00:00 bez tz-info) w podanej strefie.

    Args:
        ts: Timestamp wejściowy (tz-aware albo tz-naive — naive traktowany
            jako UTC, zgodnie z konwencją projektu).
        tz_name: IANA timezone string (np. "UTC", "Europe/Warsaw").

    Returns:
        ``pd.Timestamp`` o godzinie 00:00:00 odpowiadającej dacie ``ts`` w
        strefie ``tz_name``, bez tz-info (naive). Format ułatwia porównanie
        równościowe w ``update_state``.

    Raises:
        ValueError: Gdy ``tz_name`` nie jest poprawną IANA strefą. Mapuje
            ``ZoneInfoNotFoundError`` na ``ValueError`` dla spójności
            interfejsu z resztą modułu.
    """
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        logger.error(
            "Nieznana strefa czasowa w RiskLimits.daily_reset_tz",
            extra={"tz_name": tz_name},
        )
        raise ValueError(f"Nieznana IANA timezone: {tz_name!r}") from e

    # tz-naive ts traktujemy jako UTC (konwencja projektu: pandas DatetimeIndex
    # z parse_dates jest typowo naive, ale semantycznie UTC).
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")

    local = ts.tz_convert(tz)
    return local.normalize().tz_localize(None)


# ============================================================================
# State management — immutable transitions
# ============================================================================


def init_state(equity_start: float, ts: pd.Timestamp, limits: RiskLimits) -> RiskState:
    """Inicjalizuje ``RiskState`` na początku runu.

    Wołany jednorazowo przez backtester wrapper w ``init()`` (dla
    ``backtesting.py`` to pierwszy bar). ``equity_peak`` i ``daily_start_equity``
    startują z ``equity_start``; dzień bieżący wynika z ``ts`` i
    ``limits.daily_reset_tz``.

    Args:
        equity_start: Początkowe equity portfela (cash + ewentualne otwarte
            pozycje przy starcie; typowo po prostu cash).
        ts: Timestamp pierwszego bara (z DatetimeIndex df-a).
        limits: Konfiguracja limitów — używana do ``daily_reset_tz``.

    Returns:
        ``RiskState`` z polami ustawionymi na startowe wartości i
        ``open_positions=0``.

    Raises:
        ValueError: Gdy ``limits.daily_reset_tz`` jest nieznaną strefą.
    """
    day = _normalize_day(ts, limits.daily_reset_tz)
    return RiskState(
        equity_peak=float(equity_start),
        daily_start_equity=float(equity_start),
        daily_start_day=day,
        open_positions=0,
    )


def update_state(
    state: RiskState,
    equity_now: float,
    ts: pd.Timestamp,
    open_positions: int,
    limits: RiskLimits,
) -> RiskState:
    """Zwraca nowy ``RiskState`` z aktualizacjami per bar.

    - ``equity_peak`` rośnie monotonicznie (max(stary peak, equity_now)).
    - ``daily_start_equity`` resetuje się gdy dzień bieżący != ``daily_start_day``
      (porównanie po normalizacji do ``limits.daily_reset_tz``).
    - ``open_positions`` zawsze nadpisywany wartością z parametru.

    Args:
        state: Poprzedni stan.
        equity_now: Bieżące equity (cash + mark-to-market open positions).
            W backtesting.py to ``self.equity`` na ``Wrapped``.
        ts: Timestamp bieżącego bara.
        open_positions: Liczba otwartych pozycji na bieżącym barze (typowo
            ``int(bool(self.position))`` dla single-symbol setup).
        limits: Konfiguracja — używana do ``daily_reset_tz``.

    Returns:
        Nowa instancja ``RiskState``. Stara instancja pozostaje niezmieniona.
    """
    today = _normalize_day(ts, limits.daily_reset_tz)
    if today != state.daily_start_day:
        # Reset dziennej — nowy dzień, nowe daily_start
        new_daily_start_equity = float(equity_now)
        new_daily_start_day = today
    else:
        new_daily_start_equity = state.daily_start_equity
        new_daily_start_day = state.daily_start_day

    new_peak = max(state.equity_peak, float(equity_now))

    return RiskState(
        equity_peak=new_peak,
        daily_start_equity=new_daily_start_equity,
        daily_start_day=new_daily_start_day,
        open_positions=int(open_positions),
    )


# ============================================================================
# Gates — pure checks
# ============================================================================


def check_drawdown(state: RiskState, equity_now: float, limits: RiskLimits) -> RiskBreach | None:
    """Sprawdza czy drawdown od peak equity przekroczył próg.

    Drawdown liczony jako ``(equity_peak - equity_now) / equity_peak``. Wartość
    dodatnia oznacza spadek od peak; zero = na peak; ujemna niemożliwa jeśli
    state jest aktualizowany przez ``update_state``.

    Args:
        state: Bieżący stan (po ``update_state``).
        equity_now: Bieżące equity. Przekazywane osobno (nie czytane ze state)
            żeby check było pure względem peak'a w state i bieżącej wartości
            — symetrycznie do innych gates.
        limits: Konfiguracja. Gdy ``max_drawdown_pct is None`` → zwraca None
            natychmiast.

    Returns:
        ``RiskBreach(kind="max_drawdown", ...)`` gdy próg przekroczony, w
        przeciwnym razie ``None``. ``value`` w breach to faktyczny DD (ujemny,
        bo strata: np. -0.25 dla -25%), ``threshold`` to próg z konfiguracji.
    """
    if limits.max_drawdown_pct is None:
        return None
    if state.equity_peak <= 0:
        # Edge case: peak nieustalony / zerowy. Bez sensu liczyć DD; brak breach.
        return None

    dd = (state.equity_peak - float(equity_now)) / state.equity_peak
    if dd >= limits.max_drawdown_pct:
        return RiskBreach(
            kind="max_drawdown",
            value=-dd,  # ujemny dla narracji "strata X%"
            threshold=limits.max_drawdown_pct,
            ts=pd.Timestamp.now(tz="UTC"),  # nadpisywane przez caller jeśli zna bar_ts
            message=(
                f"Max drawdown breached: {dd:.2%} >= {limits.max_drawdown_pct:.2%} "
                f"(equity_now={equity_now:.2f}, peak={state.equity_peak:.2f})"
            ),
        )
    return None


def check_daily_loss(
    state: RiskState,
    equity_now: float,
    ts: pd.Timestamp,
    limits: RiskLimits,
) -> RiskBreach | None:
    """Sprawdza czy strata od ``daily_start_equity`` przekroczyła próg.

    ``ts`` przekazywany żeby breach niósł poprawny timestamp; logika sprawdzenia
    używa ``state.daily_start_equity`` (caller musi zawołać ``update_state``
    najpierw, żeby ewentualny reset dziennej był uwzględniony).

    Args:
        state: Bieżący stan (po ``update_state`` — daily reset musi być
            ogarnięty zanim sprawdzimy próg).
        equity_now: Bieżące equity.
        ts: Timestamp bieżącego bara — wkładany do ``RiskBreach.ts``.
        limits: Konfiguracja. Gdy ``daily_loss_pct is None`` → zwraca None.

    Returns:
        ``RiskBreach(kind="daily_loss", ...)`` gdy próg przekroczony, w
        przeciwnym razie ``None``.
    """
    if limits.daily_loss_pct is None:
        return None
    if state.daily_start_equity <= 0:
        return None

    loss = (state.daily_start_equity - float(equity_now)) / state.daily_start_equity
    if loss >= limits.daily_loss_pct:
        return RiskBreach(
            kind="daily_loss",
            value=-loss,
            threshold=limits.daily_loss_pct,
            ts=ts,
            message=(
                f"Daily loss breached: {loss:.2%} >= {limits.daily_loss_pct:.2%} "
                f"(equity_now={equity_now:.2f}, daily_start={state.daily_start_equity:.2f}, "
                f"day={state.daily_start_day.date()} {limits.daily_reset_tz})"
            ),
        )
    return None


def check_positions(state: RiskState, limits: RiskLimits) -> RiskBreach | None:
    """Sprawdza czy liczba otwartych pozycji przekroczyła ``max_concurrent_positions``.

    Args:
        state: Bieżący stan (``state.open_positions`` musi być aktualne —
            ustawione przez ``update_state``).
        limits: Konfiguracja. Gdy ``max_concurrent_positions is None`` → None.

    Returns:
        ``RiskBreach(kind="max_positions", ...)`` gdy próg przekroczony,
        w przeciwnym razie ``None``. Wartością value jest ``open_positions``,
        threshold to limit.
    """
    if limits.max_concurrent_positions is None:
        return None

    if state.open_positions > limits.max_concurrent_positions:
        return RiskBreach(
            kind="max_positions",
            value=float(state.open_positions),
            threshold=float(limits.max_concurrent_positions),
            ts=pd.Timestamp.now(tz="UTC"),
            message=(
                f"Max concurrent positions breached: {state.open_positions} > "
                f"{limits.max_concurrent_positions}"
            ),
        )
    return None


def check_all(
    state: RiskState,
    equity_now: float,
    ts: pd.Timestamp,
    limits: RiskLimits,
) -> RiskBreach | None:
    """First-hit check w kolejności: drawdown → daily_loss → max_positions.

    Gdy więcej niż jeden limit naruszony na tym samym barze, raportujemy
    pierwszy — DD jest najbardziej egzystencjalnym safety netem (hard stop
    na total exposure), więc pojawia się jako pierwszy. Pozostałe nie są
    actionable dla zatrzymanego runu.

    Args:
        state: Bieżący stan po ``update_state``.
        equity_now: Bieżące equity.
        ts: Timestamp bieżącego bara — nadpisuje ``RiskBreach.ts`` dla
            drawdown i max_positions (które inaczej mają ``pd.Timestamp.now(tz='UTC')``).
        limits: Konfiguracja.

    Returns:
        Pierwsza znaleziona ``RiskBreach`` albo ``None`` gdy nic się nie pali.
    """
    breach = check_drawdown(state, equity_now, limits)
    if breach is not None:
        # Nadpisz ts na bar_ts — żeby caller dostał deterministyczny timestamp
        return RiskBreach(
            kind=breach.kind,
            value=breach.value,
            threshold=breach.threshold,
            ts=ts,
            message=breach.message,
        )

    breach = check_daily_loss(state, equity_now, ts, limits)
    if breach is not None:
        return breach

    breach = check_positions(state, limits)
    if breach is not None:
        return RiskBreach(
            kind=breach.kind,
            value=breach.value,
            threshold=breach.threshold,
            ts=ts,
            message=breach.message,
        )

    return None


# ============================================================================
# Sizing — pure helper (caller-driven, no auto-injection — ADR-008 §8)
# ============================================================================


def position_size(equity_now: float, sl_distance: float, risk_per_trade_pct: float) -> float:
    """Liczy rozmiar pozycji żeby strata przy hicie SL = ``risk_per_trade_pct *
    equity_now``.

    Wzór: ``size = (equity_now * risk_per_trade_pct) / sl_distance``.

    Pure helper — strategia woła ten helper explicit w ``on_bar`` i wpisuje
    wynik do ``Signal.size``. Backtester wrapper NIE wstrzykuje sizingu
    automatycznie (ADR-008 §8 — nie wszystkie strategie mają SL przed entry,
    a Signal ma ``sl_pct``/``meta["sl"]`` zamiast ``sl_price``).

    Args:
        equity_now: Bieżące equity portfela.
        sl_distance: Odległość ceny od entry do SL, w jednostkach kwotowanych
            (typowo USDT). Strategia liczy: ``abs(entry_price - sl_price)``
            albo ``entry_price * abs(sl_pct)``.
        risk_per_trade_pct: Procent equity ryzykowany na trade (np. 0.01 = 1%).

    Returns:
        Rozmiar pozycji w jednostkach base asset (np. BTC dla BTC/USDT).
        Gdy ``sl_distance <= 0`` zwraca ``0.0`` i emituje ``logger.warning`` —
        strategia może wtedy interpretować jako "skip this entry, sizing
        niedeterministyczny".
    """
    if sl_distance <= 0:
        logger.warning(
            "position_size: nie-dodatnie sl_distance — zwracam 0.0",
            extra={
                "equity_now": equity_now,
                "sl_distance": sl_distance,
                "risk_per_trade_pct": risk_per_trade_pct,
            },
        )
        return 0.0
    if equity_now <= 0:
        logger.warning(
            "position_size: nie-dodatnie equity — zwracam 0.0",
            extra={"equity_now": equity_now},
        )
        return 0.0

    return (float(equity_now) * float(risk_per_trade_pct)) / float(sl_distance)
