#!/usr/bin/env python3
"""algo_bot/data_integrity.py — walidacja integralności danych OHLCV.

Single source of truth dla sanity-checków na danych PROCESSED (i RAW, jeśli
trzeba). Faza 2 Sesja 2 (Decyzja 7: nowy moduł + gated pytest, bez dotykania
``fetch_data``/``process_data``).

Filozofia (Faza 2 Sesja 2, Decyzja 6):
    - Monotoniczność i niezmienniki OHLCV to **twarde** warunki integralności
      (``IntegrityReport.ok``). Ich naruszenie oznacza zepsute dane.
    - Gap'y (przerwy w siatce czasu) są **miękkie** — logujemy WARNING, ale nie
      zawalają raportu. ``process_data`` wypełnia małe gap'y i abortuje duże,
      więc na danych PROCESSED przerwy zwykle nie wystąpią; detektor zachowuje
      jednak wartość dla danych RAW oraz jako guard, gdyby kiedyś pojawił się
      tryb no-fill.

Publiczne API:
    - ``check_monotonic`` — duplikaty i odwrócona kolejność timestampów.
    - ``check_ohlcv_invariants`` — high ≥ max(open, close), low ≤ min(open, close),
      high ≥ low, volume ≥ 0, brak NaN w OHLCV.
    - ``detect_gaps`` — przerwy w siatce czasu dłuższe niż ``threshold_mult × TF``.
    - ``check_integrity`` — orkiestrator: uruchamia wszystkie checki, loguje
      (WARNING dla naruszeń i gapów, INFO gdy czysto) i zwraca ``IntegrityReport``.

Konwencja logowania (zgodnie z ADR-006 i preferencjami): INFO dla milestone
("integrity OK"), WARNING dla każdego naruszenia niezmiennika i każdego gapa.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from algo_bot.log import get_logger

logger = get_logger(__name__)

# === Konfiguracja kroków czasowych (ms) ===
TF_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

#: Kolumny OHLCV wymagane do walidacji niezmienników.
OHLCV_COLS: tuple[str, ...] = ("Open", "High", "Low", "Close", "Volume")

#: Domyślny próg detekcji gapa: przerwa > 3 × TF (np. 45 min dla 15m).
DEFAULT_GAP_THRESHOLD_MULT: int = 3

_NS_PER_MS: int = 1_000_000


# === Modele wyników (frozen — niezmienne raporty) ===
@dataclass(frozen=True)
class MonotonicResult:
    """Wynik checku monotoniczności indeksu czasu.

    Attributes:
        is_monotonic_increasing: Czy indeks jest niemalejący (pandas semantyka,
            dopuszcza równe sąsiednie wartości — duplikaty łapane osobno).
        n_duplicates: Liczba zduplikowanych timestampów.
        n_out_of_order: Liczba pozycji, gdzie ``ts[i] < ts[i-1]`` (twarda inwersja).
    """

    is_monotonic_increasing: bool
    n_duplicates: int
    n_out_of_order: int

    @property
    def ok(self) -> bool:
        """``True`` gdy indeks ściśle rosnący: niemalejący, bez duplikatów, bez inwersji."""
        return self.is_monotonic_increasing and self.n_duplicates == 0 and self.n_out_of_order == 0


@dataclass(frozen=True)
class InvariantResult:
    """Wynik checku niezmienników OHLCV.

    Attributes:
        n_rows: Liczba wierszy poddanych walidacji.
        n_high_violations: Wiersze gdzie ``High < max(Open, Close)``.
        n_low_violations: Wiersze gdzie ``Low > min(Open, Close)``.
        n_high_low_violations: Wiersze gdzie ``High < Low``.
        n_negative_volume: Wiersze gdzie ``Volume < 0``.
        n_nan_rows: Wiersze z NaN w którejkolwiek kolumnie OHLCV.
    """

    n_rows: int
    n_high_violations: int
    n_low_violations: int
    n_high_low_violations: int
    n_negative_volume: int
    n_nan_rows: int

    @property
    def ok(self) -> bool:
        """``True`` gdy wszystkie niezmienniki OHLCV spełnione i brak NaN."""
        return (
            self.n_high_violations == 0
            and self.n_low_violations == 0
            and self.n_high_low_violations == 0
            and self.n_negative_volume == 0
            and self.n_nan_rows == 0
        )


@dataclass(frozen=True)
class Gap:
    """Pojedyncza przerwa w siatce czasu (dłuższa niż próg).

    Attributes:
        prev_ts: Timestamp ostatniej świecy przed przerwą.
        next_ts: Timestamp pierwszej świecy po przerwie.
        gap_ms: Długość przerwy w milisekundach.
        missing_bars: Liczba brakujących świec TF w przerwie (``gap // TF - 1``).
    """

    prev_ts: pd.Timestamp
    next_ts: pd.Timestamp
    gap_ms: int
    missing_bars: int


@dataclass(frozen=True)
class IntegrityReport:
    """Zbiorczy raport integralności pojedynczej serii OHLCV.

    Attributes:
        symbol: Symbol (informacyjnie, np. 'BTC/USDT'); może być ``None``.
        timeframe: Timeframe serii (klucz z ``TF_MS``).
        n_rows: Liczba wierszy.
        start: Pierwszy timestamp (``None`` gdy pusto).
        end: Ostatni timestamp (``None`` gdy pusto).
        monotonic: Wynik checku monotoniczności.
        invariants: Wynik checku niezmienników OHLCV.
        gaps: Krotka wykrytych gapów (miękkie ostrzeżenia, nie zawalają ``ok``).
        gap_threshold_mult: Użyty mnożnik progu detekcji gapów.
    """

    symbol: str | None
    timeframe: str
    n_rows: int
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    monotonic: MonotonicResult
    invariants: InvariantResult
    gaps: tuple[Gap, ...]
    gap_threshold_mult: int

    @property
    def ok(self) -> bool:
        """Twarda integralność: monotoniczność + niezmienniki OHLCV (gapy nie liczą się)."""
        return self.monotonic.ok and self.invariants.ok

    @property
    def n_gaps(self) -> int:
        """Liczba wykrytych gapów."""
        return len(self.gaps)


# === Utils ===
def _require_datetime_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    """Waliduje i zwraca ``DatetimeIndex`` ramki.

    Args:
        df: Ramka OHLCV.

    Returns:
        Indeks jako ``pd.DatetimeIndex``.

    Raises:
        TypeError: Gdy indeks nie jest ``DatetimeIndex``.
    """
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError("Indeks DataFrame musi być pd.DatetimeIndex (UTC).")
    return idx


def _index_ns_utc(idx: pd.DatetimeIndex) -> npt.NDArray[np.int64]:
    """Zwraca timestampy indeksu jako ns od epoki (UTC).

    Zdejmuje strefę czasową przed konwersją: ``to_numpy()`` na tz-aware indeksie
    zwraca w niektórych wersjach pandas tablicę obiektów ``Timestamp`` (nie
    ``datetime64``), więc najpierw normalizujemy do naive UTC.

    Args:
        idx: Indeks czasu (tz-aware UTC lub naive).

    Returns:
        Tablica ``int64`` z nanosekundami od epoki.
    """
    naive = idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx
    return naive.to_numpy(dtype="datetime64[ns]").astype(np.int64)


# === Checki ===
def check_monotonic(df: pd.DataFrame) -> MonotonicResult:
    """Sprawdza monotoniczność indeksu czasu: duplikaty i odwróconą kolejność.

    Args:
        df: Ramka OHLCV z indeksem ``DatetimeIndex``.

    Returns:
        ``MonotonicResult`` z licznikami duplikatów i inwersji.
    """
    idx = _require_datetime_index(df)
    is_incr = bool(idx.is_monotonic_increasing)
    n_dup = int(idx.duplicated().sum())

    if idx.size > 1:
        vals = _index_ns_utc(idx)
        n_ooo = int((vals[1:] < vals[:-1]).sum())
    else:
        n_ooo = 0

    return MonotonicResult(
        is_monotonic_increasing=is_incr,
        n_duplicates=n_dup,
        n_out_of_order=n_ooo,
    )


def check_ohlcv_invariants(df: pd.DataFrame) -> InvariantResult:
    """Sprawdza niezmienniki OHLCV na każdym wierszu.

    Niezmienniki:
        - ``High >= max(Open, Close)``
        - ``Low <= min(Open, Close)``
        - ``High >= Low``
        - ``Volume >= 0``
        - brak NaN w kolumnach OHLCV

    Args:
        df: Ramka z kolumnami ``Open, High, Low, Close, Volume``.

    Returns:
        ``InvariantResult`` z liczbą naruszeń per typ.

    Raises:
        ValueError: Gdy brakuje którejś kolumny OHLCV.
    """
    for col in OHLCV_COLS:
        if col not in df.columns:
            raise ValueError(f"Brak kolumny OHLCV '{col}' w ramce.")

    open_ = df["Open"]
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    volume = df["Volume"]

    oc = pd.concat([open_, close], axis=1)
    oc_max = oc.max(axis=1)
    oc_min = oc.min(axis=1)

    n_high = int((high < oc_max).sum())
    n_low = int((low > oc_min).sum())
    n_high_low = int((high < low).sum())
    n_neg_vol = int((volume < 0).sum())
    n_nan = int(df.loc[:, list(OHLCV_COLS)].isna().any(axis=1).sum())

    return InvariantResult(
        n_rows=(len(df)),
        n_high_violations=n_high,
        n_low_violations=n_low,
        n_high_low_violations=n_high_low,
        n_negative_volume=n_neg_vol,
        n_nan_rows=n_nan,
    )


def detect_gaps(
    df: pd.DataFrame,
    timeframe: str,
    threshold_mult: int = DEFAULT_GAP_THRESHOLD_MULT,
) -> tuple[Gap, ...]:
    """Wykrywa przerwy w siatce czasu dłuższe niż ``threshold_mult × TF``.

    Ramka jest sortowana po indeksie przed analizą (defensywnie). Detekcja
    operuje na różnicach kolejnych timestampów w milisekundach.

    Args:
        df: Ramka OHLCV z indeksem ``DatetimeIndex``.
        timeframe: Timeframe serii (klucz z ``TF_MS``).
        threshold_mult: Mnożnik progu; gap raportowany gdy
            ``delta > threshold_mult × TF_ms``. Domyślnie 3.

    Returns:
        Krotka ``Gap`` w kolejności chronologicznej (pusta gdy brak gapów).

    Raises:
        ValueError: Gdy ``timeframe`` nie jest wspierany.
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Niewspierany timeframe: {timeframe!r}")

    step = TF_MS[timeframe]
    threshold = threshold_mult * step

    idx = _require_datetime_index(df).sort_values()
    if idx.size < 2:
        return ()

    ts_ms = _index_ns_utc(idx) // _NS_PER_MS
    deltas = ts_ms[1:] - ts_ms[:-1]

    gaps: list[Gap] = []
    for i in range(deltas.size):
        delta = int(deltas[i])
        if delta > threshold:
            gaps.append(
                Gap(
                    prev_ts=idx[i],
                    next_ts=idx[i + 1],
                    gap_ms=delta,
                    missing_bars=delta // step - 1,
                )
            )
    return tuple(gaps)


def check_integrity(
    df: pd.DataFrame,
    timeframe: str,
    *,
    symbol: str | None = None,
    gap_threshold_mult: int = DEFAULT_GAP_THRESHOLD_MULT,
) -> IntegrityReport:
    """Uruchamia komplet checków i loguje wynik.

    Loguje WARNING dla każdego naruszenia monotoniczności/niezmienników oraz dla
    każdego wykrytego gapa; INFO gdy seria jest czysta i bez gapów.

    Args:
        df: Ramka OHLCV z indeksem ``DatetimeIndex`` (np. wynik
            ``data_loader.load_processed``).
        timeframe: Timeframe serii (klucz z ``TF_MS``).
        symbol: Opcjonalny symbol — tylko do logów i raportu.
        gap_threshold_mult: Mnożnik progu detekcji gapów (domyślnie 3 × TF).

    Returns:
        ``IntegrityReport`` z wynikami wszystkich checków.

    Raises:
        ValueError: Gdy ``timeframe`` nie jest wspierany lub brakuje kolumn OHLCV.
        TypeError: Gdy indeks nie jest ``DatetimeIndex``.
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Niewspierany timeframe: {timeframe!r}")

    # Monotoniczność liczymy na ORYGINALNEJ kolejności (sortowanie zamaskowałoby
    # duplikaty i inwersje). Niezmienniki są per-wiersz, więc kolejność nie zmienia
    # liczników. Gapy i start/end liczymy na posortowanej kopii.
    _require_datetime_index(df)
    monotonic = check_monotonic(df)
    invariants = check_ohlcv_invariants(df)

    sorted_df = df.sort_index()
    idx = _require_datetime_index(sorted_df)
    gaps = detect_gaps(sorted_df, timeframe, gap_threshold_mult)

    start = idx[0] if idx.size else None
    end = idx[-1] if idx.size else None

    report = IntegrityReport(
        symbol=symbol,
        timeframe=timeframe,
        n_rows=(len(df)),
        start=start,
        end=end,
        monotonic=monotonic,
        invariants=invariants,
        gaps=gaps,
        gap_threshold_mult=gap_threshold_mult,
    )

    log_ctx = {"symbol": symbol, "timeframe": timeframe, "n_rows": report.n_rows}

    if not monotonic.ok:
        logger.warning(
            "Monotonic check failed",
            extra={
                **log_ctx,
                "n_duplicates": monotonic.n_duplicates,
                "n_out_of_order": monotonic.n_out_of_order,
                "is_monotonic_increasing": monotonic.is_monotonic_increasing,
            },
        )

    if not invariants.ok:
        logger.warning(
            "OHLCV invariant violations",
            extra={
                **log_ctx,
                "n_high_violations": invariants.n_high_violations,
                "n_low_violations": invariants.n_low_violations,
                "n_high_low_violations": invariants.n_high_low_violations,
                "n_negative_volume": invariants.n_negative_volume,
                "n_nan_rows": invariants.n_nan_rows,
            },
        )

    for gap in gaps:
        logger.warning(
            "Gap detected",
            extra={
                **log_ctx,
                "prev_ts": str(gap.prev_ts),
                "next_ts": str(gap.next_ts),
                "gap_ms": gap.gap_ms,
                "missing_bars": gap.missing_bars,
            },
        )

    if report.ok and not gaps:
        logger.info(
            "Integrity OK",
            extra={**log_ctx, "start": str(start), "end": str(end)},
        )

    return report
