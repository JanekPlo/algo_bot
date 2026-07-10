"""
algo_bot/strategies/mean_reversion_bb_stoch.py

Kontrariańska strategia mean-reversion na wstęgach Bollingera + Stochastic.
Kandydatka MVP Fazy 2 po pivocie z bghtrend (ADR-012). Prior metodyczny:
Mastermind MMS (mastermindzx.pl) — patrz docs/reference/modules/
strategy-mean-reversion-bb-stoch.md.

Teza ekonomiczna:
Gdy cena dociera do wstęgi Bollingera, jest statystycznie "rozciągnięta"
względem swojej lokalnej średniej (num_std odchyleń). W braku fundamentalnego
powodu do trwałego wybicia rynek wraca do średniej. Wchodzimy KONTRARIAŃSKO:
przy dolnej wstędze — long; przy górnej — short. Nie łapiemy jednak spadającego
noża: czekamy aż świeca dotknie wstęgi, zamknie się, i dopiero NASTĘPNA świeca
pokaże reakcję w przeciwną stronę (byczy/niedźwiedzi korpus). Opcjonalnie
Stochastic potwierdza, że dotknięcie wstęgi zbiega się z ekstremum momentum
(wyprzedanie < oversold / wykupienie > overbought).

Mechanika wejścia (both-directions, symetryczna) — "armed → reaction":
1. ARMED: świeca S dotyka wstęgi knotem (Low ≤ dolna dla long / High ≥ górna
   dla short). W trybie `bb_stoch` dotknięcie musi ZBIEGAĆ SIĘ z ekstremum
   Stocha (%K < oversold dla long / > overbought dla short) — oscylator
   potwierdza wyprzedanie/wykupienie w punkcie dotknięcia. Uzbrajamy kierunek
   na `arm_expiry_bars` kolejnych barów.
2. ENTRY: pierwsza uzbrojona świeca reakcyjna R (korpus w stronę przeciwną:
   long → Close>Open; short → Close<Open). Opcjonalnie `require_reclaim` żąda
   dodatkowo powrotu Close do środka wstęgi. Wejście po Close bara R
   (trade_on_close). Brak reakcji w oknie → rozbrojenie.

   Uwaga projektowa: gate Stocha jest przy UZBROJENIU, nie na barze reakcji.
   Świeca reakcyjna z definicji zawraca i podbija %K, więc gate na barze R
   strukturalnie prawie nigdy by nie odpalał (odkryte przy testach Bety) —
   ekstremum oscylatora jest tam gdzie cena dotyka wstęgi.

Mechanika wyjścia (BEZ trail / BE / timeout — świadomie, patrz nota MVP):
- TP = PRZECIWNA, ŻYWA wstęga (long → bieżąca górna; short → bieżąca dolna).
  Przeliczana co bar — cel dynamiczny, goni cenę w kierunku powrotu do średniej.
- SL = STAŁE `sl_pct` (domyślnie 2%) od ceny wejścia.
- Same-bar TP&SL: `tp_has_priority` (domyślnie False → SL wygrywa; konserwatywnie
  nie zawyżamy edge'u przy świecy przebijającej oba poziomy).
Wyjście egzekwowane po Close bara trafienia (trade_on_close) — konserwatywnie
względem wyidealizowanego fill'u limit/stop dokładnie na poziomie.

Kauzalność / wydajność:
- `precompute()` liczy BB i Stochastic RAZ wektorowo; `on_bar` czyta prefiks
  (O(1)/bar). Kontrakt kauzalności jak w StrategyBase.precompute — bbands i
  stochastic używają tylko danych <= t. Ekwiwalencję pilnuje test.

NOTA MVP — GOŁY RDZEŃ:
Ta strategia testuje TYLKO bazę metodyki (both-dir BB + reakcja + opcjonalny
Stoch, stały SL, TP=przeciwna wstęga). Właściwy edge Mastermind — piramidowanie
(dokładki) i sekwencyjna redukcja lewara (anti-martingale) — jest state machine
PONAD transakcjami i NIE mieści się w single-position backtesting.py. Odłożony
do osobnego ADR (możliwy trigger wcześniejszej migracji silnika). Sizing również
odłożony: strategia zwraca goły Signal bez `size` — runner używa domyślnego
sizingu. Wyniki Bety zaznaczać jako "baza, nie pełny system".

NOTA FUNDING (ADR-011):
Bez timeout pozycja może być trzymana długo → realny koszt/przychód funding.
Uwaga: kontrariańska MR statystycznie stoi po stronie ODBIERAJĄCEJ funding
(short w euforii, long w kapitulacji) — potencjalny wiatr w plecy, nie tylko
koszt. NIE mechanizujemy tego w MVP; mierzymy przez overlay `--microstructure
full` (raw vs post). Nieograniczony hold-time × funding → do przetestowania
w przyszłym ADR (razem z piramidowaniem/lewarem).

Parametry (ParamSchema = MeanReversionBBStochParams): patrz dataclass niżej.
Tuning (sweep): config/mr_b1..b3.yaml (b1 H1 strict, b2 H1 relaxed, b3 15m).

See also:
- algo_bot/indicators/core.py (bbands, stochastic — kauzalne)
- algo_bot/strategies/bghtrend_pullback.py (wzorzec precompute + StrategyBase)
- docs/adr/003-strategybase-signal-api.md, docs/adr/011-microstructure-adjustments.md
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from algo_bot.indicators import bbands, stochastic
from algo_bot.strategy_base import Signal, StrategyBase


# =========================
# Paramy
# =========================
@dataclass(frozen=True)
class MeanReversionBBStochParams:
    # Bollinger Bands (środek = SMA, wstęgi = ± num_std·std populacyjne)
    bb_window: int = 20
    bb_num_std: float = 2.0

    # Stochastic "slow" (14/3/3), progi kontrariańskie 20/80
    stoch_k: int = 14
    stoch_d: int = 3
    stoch_smooth: int = 3
    stoch_oversold: float = 20.0
    stoch_overbought: float = 80.0

    # Mechanika wejścia (armed → reaction)
    entry_mode: str = "bb_stoch"  # 'bb_only' | 'bb_stoch'
    arm_expiry_bars: int = 2  # ile barów po dotknięciu czekamy na reakcję
    require_reclaim: bool = False  # dodatkowo: Close reakcji wraca do środka wstęgi

    # Wyjście
    sl_pct: float = 0.02  # stały SL jako frakcja ceny wejścia (2%)
    tp_has_priority: bool = False  # same-bar TP&SL: False = SL wygrywa (konserwatywnie)

    # Zakres kierunku
    side: str = "both"  # 'long' | 'short' | 'both'

    # Konwencja silnika (trade_on_close jak w bghtrend)
    trade_on_close: bool = True

    def __post_init__(self) -> None:
        """Fail-fast walidacja inwariantów (ADR-006: naruszenie → raise).

        Frozen dataclass — czytamy pola, nie modyfikujemy. Hook łapie każdą
        ścieżkę konstrukcji paramów (algo-backtest/sweep/walkforward przez
        ``coerce_params`` → ``schema(**clean)``), więc zły config pada głośno
        zamiast po cichu robić zero trades.
        """
        if self.bb_window < 2:
            raise ValueError(f"bb_window musi być ≥ 2 (odchylenie std), dostałem {self.bb_window}.")
        if self.bb_num_std <= 0:
            raise ValueError(f"bb_num_std musi być > 0, dostałem {self.bb_num_std}.")
        if self.stoch_k < 1 or self.stoch_d < 1 or self.stoch_smooth < 1:
            raise ValueError(
                "stoch_k / stoch_d / stoch_smooth muszą być ≥ 1, dostałem "
                f"k={self.stoch_k}, d={self.stoch_d}, smooth={self.stoch_smooth}."
            )
        if not (0.0 <= self.stoch_oversold < self.stoch_overbought <= 100.0):
            raise ValueError(
                "Wymagane 0 ≤ stoch_oversold < stoch_overbought ≤ 100, dostałem "
                f"oversold={self.stoch_oversold}, overbought={self.stoch_overbought}."
            )
        if self.entry_mode not in ("bb_only", "bb_stoch"):
            raise ValueError(
                f"entry_mode musi być 'bb_only' lub 'bb_stoch', dostałem {self.entry_mode!r}."
            )
        if self.arm_expiry_bars < 1:
            raise ValueError(f"arm_expiry_bars musi być ≥ 1, dostałem {self.arm_expiry_bars}.")
        if self.side not in ("long", "short", "both"):
            raise ValueError(f"side musi być 'long'/'short'/'both', dostałem {self.side!r}.")
        if not (0.0 < self.sl_pct < 1.0):
            raise ValueError(f"sl_pct musi być w (0, 1), dostałem {self.sl_pct}.")


# =========================
# Strategia
# =========================
class Strategy(StrategyBase):
    name = "mean_reversion_bb_stoch"
    ParamSchema = MeanReversionBBStochParams

    # Zawężenie typu self.p (StrategyBase.__init__ ustawia instancję ParamSchema).
    # Bez tego mypy widzi self.p jako Any i strict warn_return_any zapala się na
    # helperach zwracających porównania z polami paramów.
    p: MeanReversionBBStochParams

    # --- stan pozycji ---
    _pos_side: str | None = None
    _entry_price: float | None = None
    _sl: float | None = None
    _tp: float | None = None

    # --- stan "armed" (oczekiwanie na świecę reakcyjną) ---
    _armed_side: str | None = None
    _armed_bars: int = 0

    # --- cache precompute (backtest); None → ścieżka live/fallback ---
    _pre: dict[str, pd.Series] | None = None
    _pre_len: int = 0

    @staticmethod
    def required_features() -> set[str]:
        return {"Open", "High", "Low", "Close"}

    # ---------- precompute (perf hook) ----------
    def precompute(self, df: pd.DataFrame) -> None:
        """Wektorowe, jednorazowe policzenie BB + Stochastic na pełnej historii.

        Oba wskaźniki są kauzalne (rolling bez center), więc prefiks
        ``.iloc[:m]`` == policzenie na prefiksie — dowód w
        tests/test_mean_reversion_bb_stoch.py (precompute equivalence).
        """
        upper, _mid, lower = bbands(df["Close"], self.p.bb_window, self.p.bb_num_std)
        pct_k, _pct_d = stochastic(df, self.p.stoch_k, self.p.stoch_d, self.p.stoch_smooth)
        self._pre = {
            "bb_upper": upper,
            "bb_lower": lower,
            "stoch_k": pct_k,
        }
        self._pre_len = len(df)

    # ---------- helpers ----------
    def _reaction_ok(self, side: str, o: float, c: float, lower: float, upper: float) -> bool:
        """Świeca reakcyjna: korpus w stronę przeciwną do dotknięcia.

        Opcjonalny ``require_reclaim`` dokłada warunek powrotu Close do środka
        wstęgi (dla long: nad dolną; dla short: pod górną).
        """
        if side == "long":
            if not (c > o):  # byczy korpus
                return False
            return (not self.p.require_reclaim) or (c > lower)
        # short
        if not (c < o):  # niedźwiedzi korpus
            return False
        return (not self.p.require_reclaim) or (c < upper)

    def _stoch_gate_ok(self, side: str, k_now: float) -> bool:
        """Gate Stocha — aktywny tylko w entry_mode='bb_stoch'."""
        if self.p.entry_mode == "bb_only":
            return True
        if math.isnan(k_now):
            return False
        if side == "long":
            return k_now < self.p.stoch_oversold
        return k_now > self.p.stoch_overbought

    def _hit_exit(self, side: str, high: float, low: float) -> str | None:
        """Wykrycie trafienia TP (żywa przeciwna wstęga) / SL (stały 2%)."""
        tp, sl = self._tp, self._sl
        hit_tp = False
        hit_sl = False
        if side == "long":
            if tp is not None and not math.isnan(tp):
                hit_tp = high >= tp
            if sl is not None:
                hit_sl = low <= sl
        else:  # short
            if tp is not None and not math.isnan(tp):
                hit_tp = low <= tp
            if sl is not None:
                hit_sl = high >= sl

        if hit_tp and hit_sl:
            return "tp" if self.p.tp_has_priority else "sl"
        if hit_tp:
            return "tp"
        if hit_sl:
            return "sl"
        return None

    def _set_pos(self, side: str, entry: float, tp_now: float) -> None:
        self._pos_side = side
        self._entry_price = entry
        if side == "long":
            self._sl = entry * (1.0 - self.p.sl_pct)
        else:
            self._sl = entry * (1.0 + self.p.sl_pct)
        self._tp = tp_now
        self._armed_side = None
        self._armed_bars = 0

    def _reset_pos(self) -> None:
        self._pos_side = None
        self._entry_price = None
        self._sl = None
        self._tp = None

    # ---------- main ----------
    def on_bar(self, df: pd.DataFrame) -> Signal:
        need = max(self.p.bb_window, self.p.stoch_k + self.p.stoch_smooth + self.p.stoch_d)
        if len(df) < need + 2:
            return Signal()

        m = len(df)
        if self._pre is not None and m <= self._pre_len:
            upper = self._pre["bb_upper"].iloc[:m]
            lower = self._pre["bb_lower"].iloc[:m]
            pct_k = self._pre["stoch_k"].iloc[:m]
        else:
            upper, _mid, lower = bbands(df["Close"], self.p.bb_window, self.p.bb_num_std)
            pct_k, _pct_d = stochastic(df, self.p.stoch_k, self.p.stoch_d, self.p.stoch_smooth)

        o_now = float(df["Open"].iloc[-1])
        h_now = float(df["High"].iloc[-1])
        l_now = float(df["Low"].iloc[-1])
        c_now = float(df["Close"].iloc[-1])
        upper_now = float(upper.iloc[-1])
        lower_now = float(lower.iloc[-1])
        k_now = float(pct_k.iloc[-1])

        # Wstęgi jeszcze się rozgrzewają → nic nie rób.
        if math.isnan(upper_now) or math.isnan(lower_now):
            return Signal()

        # ===== ZARZĄDZANIE POZYCJĄ (pierwszeństwo) =====
        if self._pos_side is not None:
            side = self._pos_side
            # TP = żywa przeciwna wstęga (przeliczana co bar)
            tp_live = upper_now if side == "long" else lower_now
            if not math.isnan(tp_live):
                self._tp = tp_live

            hit = self._hit_exit(side, h_now, l_now)
            if hit is not None:
                reason = "tp_band" if hit == "tp" else "sl_fixed"
                self._reset_pos()
                return Signal("exit", side, meta={"reason": reason})

            # brak wyjścia — raportuj poziomy (journal); egzekucja przy trafieniu
            return Signal(
                "hold",
                side,
                meta={"sl": self._sl, "tp": self._tp, "tp_has_priority": self.p.tp_has_priority},
            )

        # ===== WEJŚCIE: armed → reaction =====
        if self._armed_side is not None:
            side = self._armed_side
            if self._reaction_ok(side, o_now, c_now, lower_now, upper_now):
                entry = c_now
                tp_now = upper_now if side == "long" else lower_now
                self._set_pos(side, entry, tp_now)
                return Signal(
                    "enter",
                    side,
                    meta={
                        "sl": self._sl,
                        "tp": self._tp,
                        "tp_has_priority": self.p.tp_has_priority,
                    },
                )
            # brak reakcji w tym barze → postarz uzbrojenie
            self._armed_bars -= 1
            if self._armed_bars <= 0:
                self._armed_side = None
            return Signal()

        # Nie uzbrojeni → szukamy dotknięcia wstęgi (knot) + potwierdzenia Stocha.
        # Gate Stocha przy UZBROJENIU (nie przy reakcji): ekstremum oscylatora
        # jest na świecy dotknięcia; reakcja z definicji zawraca i podbija %K.
        touch_long = (
            self.p.side in ("long", "both")
            and (l_now <= lower_now)
            and self._stoch_gate_ok("long", k_now)
        )
        touch_short = (
            self.p.side in ("short", "both")
            and (h_now >= upper_now)
            and self._stoch_gate_ok("short", k_now)
        )
        if touch_long and not touch_short:
            self._armed_side = "long"
            self._armed_bars = self.p.arm_expiry_bars
        elif touch_short and not touch_long:
            self._armed_side = "short"
            self._armed_bars = self.p.arm_expiry_bars
        # touch obu wstęg naraz (gigantyczny bar) → brak jednoznacznego kierunku, nie uzbrajamy

        return Signal()
