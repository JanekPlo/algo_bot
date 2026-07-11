"""
tests/test_mean_reversion_bb_stoch.py

Testy strategii mean_reversion_bb_stoch (MR-Session Beta). Bez mocków:
- walidacja parametrów (__post_init__ frozen dataclass),
- helpery egzekucji jako niezależnie weryfikowalne jednostki (SL math, exit
  precedence, reaction, stoch gate),
- entry gates both-dir na deterministycznym OHLCV (wymusza long i short),
- precompute equivalence: ścieżka live (per-prefiks) vs precompute, bar po barze.

Konwencja: OHLCV budowany z injekcją głębokich knotów (dotknięcie wstęgi) przy
spokojnym Close (wstęgi stabilne), plus jawne świece reakcyjne i spike'i
wymuszające wyjścia. Wartości band nie liczymy ręcznie — asertujemy własności
zachowania; dokładna arytmetyka SL/precedence pokryta w testach helperów.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from algo_bot.strategies.mean_reversion_bb_stoch import (
    MeanReversionBBStochParams,
    Strategy,
)


# =====================================================================
# 1. Walidacja parametrów (__post_init__)
# =====================================================================
class TestParamsValidation:
    def test_defaults_construct(self):
        p = MeanReversionBBStochParams()
        assert p.bb_window == 20 and p.bb_num_std == 2.0
        assert p.entry_mode == "bb_stoch" and p.side == "both"
        assert p.sl_pct == 0.02 and p.tp_has_priority is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"bb_window": 1},  # < 2
            {"bb_num_std": 0.0},  # ≤ 0
            {"bb_num_std": -1.0},
            {"stoch_k": 0},
            {"stoch_d": 0},
            {"stoch_smooth": 0},
            {"stoch_oversold": 80.0, "stoch_overbought": 20.0},  # odwrócone
            {"stoch_oversold": -1.0},  # < 0
            {"stoch_overbought": 101.0},  # > 100
            {"entry_mode": "bb_rsi"},  # nieznany tryb
            {"arm_expiry_bars": 0},  # < 1
            {"side": "up"},  # nielegalny
            {"sl_pct": 0.0},  # poza (0,1)
            {"sl_pct": 1.0},
        ],
    )
    def test_invalid_raises(self, kwargs):
        with pytest.raises(ValueError):
            MeanReversionBBStochParams(**kwargs)

    def test_strategybase_filters_and_constructs(self):
        """StrategyBase filtruje dict do pól ParamSchema i buduje instancję."""
        strat = Strategy({"bb_window": 10, "side": "long", "junk_key": 123})
        assert isinstance(strat.p, MeanReversionBBStochParams)
        assert strat.p.bb_window == 10 and strat.p.side == "long"

    def test_bad_params_raise_through_strategybase(self):
        with pytest.raises(ValueError):
            Strategy({"stoch_oversold": 90.0, "stoch_overbought": 10.0})


# =====================================================================
# 2. Helpery egzekucji — niezależna weryfikacja arytmetyki
# =====================================================================
class TestExecutionHelpers:
    def test_set_pos_sl_math_long(self):
        strat = Strategy({"sl_pct": 0.02})
        strat._set_pos("long", entry=100.0, tp_now=105.0)
        assert strat._pos_side == "long"
        assert strat._sl == pytest.approx(98.0)  # 100·(1-0.02)
        assert strat._tp == 105.0
        assert strat._armed_side is None and strat._armed_bars == 0

    def test_set_pos_sl_math_short(self):
        strat = Strategy({"sl_pct": 0.02})
        strat._set_pos("short", entry=100.0, tp_now=95.0)
        assert strat._sl == pytest.approx(102.0)  # 100·(1+0.02)
        assert strat._tp == 95.0

    def test_hit_exit_long_tp_only(self):
        strat = Strategy({})
        strat._pos_side = "long"
        strat._tp, strat._sl = 110.0, 98.0
        assert strat._hit_exit("long", high=111.0, low=100.0) == "tp"

    def test_hit_exit_long_sl_only(self):
        strat = Strategy({})
        strat._pos_side = "long"
        strat._tp, strat._sl = 110.0, 98.0
        assert strat._hit_exit("long", high=105.0, low=97.0) == "sl"

    def test_hit_exit_none(self):
        strat = Strategy({})
        strat._pos_side = "long"
        strat._tp, strat._sl = 110.0, 98.0
        assert strat._hit_exit("long", high=105.0, low=99.0) is None

    def test_hit_exit_same_bar_precedence_sl_default(self):
        """Domyślnie tp_has_priority=False → same-bar TP&SL rozstrzyga SL."""
        strat = Strategy({})  # tp_has_priority default False
        strat._pos_side = "long"
        strat._tp, strat._sl = 110.0, 98.0
        assert strat._hit_exit("long", high=111.0, low=97.0) == "sl"

    def test_hit_exit_same_bar_precedence_tp_when_set(self):
        strat = Strategy({"tp_has_priority": True})
        strat._pos_side = "short"
        strat._tp, strat._sl = 90.0, 102.0
        # short: hit_tp gdy low≤tp (89≤90), hit_sl gdy high≥sl (103≥102) → oba
        assert strat._hit_exit("short", high=103.0, low=89.0) == "tp"

    def test_hit_exit_short_mirror(self):
        strat = Strategy({})
        strat._pos_side = "short"
        strat._tp, strat._sl = 90.0, 102.0
        assert strat._hit_exit("short", high=100.0, low=89.0) == "tp"
        assert strat._hit_exit("short", high=103.0, low=95.0) == "sl"
        assert strat._hit_exit("short", high=100.0, low=95.0) is None

    def test_reaction_body_direction(self):
        strat = Strategy({"require_reclaim": False})
        assert strat._reaction_ok("long", o=100.0, c=101.0, lower=98.0, upper=103.0) is True
        assert strat._reaction_ok("long", o=100.0, c=99.5, lower=98.0, upper=103.0) is False
        assert strat._reaction_ok("short", o=100.0, c=99.0, lower=98.0, upper=103.0) is True
        assert strat._reaction_ok("short", o=100.0, c=100.5, lower=98.0, upper=103.0) is False

    def test_reaction_require_reclaim(self):
        strat = Strategy({"require_reclaim": True})
        # byczy korpus ale Close nadal poniżej dolnej wstęgi → brak reclaim
        assert strat._reaction_ok("long", o=96.0, c=97.0, lower=98.0, upper=103.0) is False
        # byczy korpus i Close wrócił nad dolną wstęgę → ok
        assert strat._reaction_ok("long", o=97.5, c=98.5, lower=98.0, upper=103.0) is True

    def test_stoch_gate_bb_only_always_true(self):
        strat = Strategy({"entry_mode": "bb_only"})
        assert strat._stoch_gate_ok("long", k_now=55.0) is True
        assert strat._stoch_gate_ok("short", k_now=55.0) is True
        assert strat._stoch_gate_ok("long", k_now=float("nan")) is True

    def test_stoch_gate_bb_stoch_thresholds(self):
        strat = Strategy(
            {"entry_mode": "bb_stoch", "stoch_oversold": 20.0, "stoch_overbought": 80.0}
        )
        assert strat._stoch_gate_ok("long", k_now=15.0) is True
        assert strat._stoch_gate_ok("long", k_now=25.0) is False
        assert strat._stoch_gate_ok("short", k_now=85.0) is True
        assert strat._stoch_gate_ok("short", k_now=75.0) is False
        assert strat._stoch_gate_ok("long", k_now=float("nan")) is False  # NaN → brak wejścia


# =====================================================================
# Fixture OHLCV — spokojny Close + injekcje knotów/reakcji/spike'ów
# =====================================================================
_LONG_SETUPS = (30, 70, 110, 150, 190)  # deep low-wick → arm long
_SHORT_SETUPS = (50, 90, 130, 170, 210)  # high-wick → arm short


def _mr_ohlcv(n: int = 240, seed: int = 11) -> pd.DataFrame:
    """Deterministyczny OHLCV wymuszający oba kierunki + wyjścia.

    Close oscyluje spokojnie wokół 100 (wstęgi stabilne). Co ~40 barów
    wstrzykiwany jest GENUINE dip Close (nie sam knot): duża czerwona świeca
    z Close daleko pod dolną wstęgą → dotknięcie + Stochastic oversold →
    arm long; następny bar bycza reakcja → wejście; +2 bary spike High → TP.
    Symetrycznie duży zielony spike Close → arm short + reakcja + spike Low → TP.
    Genuine dip (a nie sam knot) jest konieczny, bo gate Stocha przy uzbrojeniu
    patrzy na Close: %K jest ekstremalny tylko gdy Close jest przy krańcu range.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    close = 100.0 + 1.5 * np.sin(2.0 * np.pi * t / 25.0) + rng.normal(0.0, 0.2, n)

    # Tylko setupy mieszczące się w n (i+3 potrzebne na spike wyjścia).
    long_setups = [i for i in _LONG_SETUPS if i + 3 < n]
    short_setups = [i for i in _SHORT_SETUPS if i + 3 < n]

    # Genuine ekstrema Close na barach dotknięcia + jawne świece reakcyjne.
    for i in long_setups:
        close[i] = close[i] - 7.0  # duży spadek → Close pod dolną wstęgą + oversold
        close[i + 1] = close[i] + 1.5  # bycza reakcja
    for i in short_setups:
        close[i] = close[i] + 7.0  # duży wzrost → Close nad górną wstęgą + overbought
        close[i + 1] = close[i] - 1.5  # niedźwiedzia reakcja

    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]  # open = poprzedni close (po sfinalizowaniu Close)
    high = np.maximum(open_, close) + 0.3
    low = np.minimum(open_, close) - 0.3

    # Spike'i wymuszające wyjścia (tylko na knotach High/Low, nie ruszają Close/Open).
    for i in long_setups:
        high[i + 3] += 9.0  # High spike → TP long (żywa górna wstęga)
    for i in short_setups:
        low[i + 3] -= 9.0  # Low spike → TP short (żywa dolna wstęga)

    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1.0},
        index=idx,
    )


def _run_sequence(strat: Strategy, df: pd.DataFrame) -> list[dict]:
    """Odtwarza silnik: on_bar na rosnących prefiksach. Zwraca listę zdarzeń
    z akcją, stroną i OHLC ostatniego bara (do weryfikacji inwariantów)."""
    events = []
    for m in range(1, len(df) + 1):
        sig = strat.on_bar(df.iloc[:m])
        last = df.iloc[m - 1]
        events.append(
            {
                "m": m,
                "action": sig.action,
                "side": sig.side,
                "reason": sig.meta.get("reason") if sig.meta else None,
                "open": float(last["Open"]),
                "close": float(last["Close"]),
            }
        )
    return events


# =====================================================================
# 3. Entry gates both-dir
# =====================================================================
class TestEntryGatesBothDir:
    _P: ClassVar[dict] = {
        "bb_window": 10,
        "bb_num_std": 1.5,
        "stoch_k": 5,
        "stoch_d": 3,
        "stoch_smooth": 3,
        "entry_mode": "bb_only",  # izolujemy mechanikę BB od gate'u Stocha
        "arm_expiry_bars": 2,
        "side": "both",
        "sl_pct": 0.02,
    }

    def test_both_directions_enter(self):
        strat = Strategy(dict(self._P))
        events = _run_sequence(strat, _mr_ohlcv())
        enters_long = [e for e in events if e["action"] == "enter" and e["side"] == "long"]
        enters_short = [e for e in events if e["action"] == "enter" and e["side"] == "short"]
        assert enters_long, "brak wejścia long — sprawdź injekcję knotów/reakcji"
        assert enters_short, "brak wejścia short"

    def test_enter_respects_reaction_body(self):
        """Każde wejcie long ma byczy korpus (Close>Open) na barze wejścia;
        każde short — niedźwiedzi. To dowód że gate reakcji faktycznie bramkuje."""
        strat = Strategy(dict(self._P))
        events = _run_sequence(strat, _mr_ohlcv())
        for e in events:
            if e["action"] == "enter" and e["side"] == "long":
                assert e["close"] > e["open"], f"long entry bez byczego korpusu @ bar {e['m']}"
            if e["action"] == "enter" and e["side"] == "short":
                assert e["close"] < e["open"], (
                    f"short entry bez niedźwiedziego korpusu @ bar {e['m']}"
                )

    def test_no_enter_while_in_position(self):
        """Po wejściu kolejne akcje to hold/exit — nigdy drugie enter przed exit
        (single-position; piramidowanie odłożone poza MVP)."""
        strat = Strategy(dict(self._P))
        events = _run_sequence(strat, _mr_ohlcv())
        in_pos = False
        for e in events:
            if e["action"] == "enter":
                assert not in_pos, f"drugie enter w otwartej pozycji @ bar {e['m']}"
                in_pos = True
            elif e["action"] == "exit":
                assert in_pos
                in_pos = False

    def test_exits_occur(self):
        strat = Strategy(dict(self._P))
        events = _run_sequence(strat, _mr_ohlcv())
        exits = [e for e in events if e["action"] == "exit"]
        assert exits, "żadne wyjście nie wystąpiło — spike'i TP/SL nie zadziałały"

    def test_side_long_only_no_shorts(self):
        strat = Strategy({**self._P, "side": "long"})
        events = _run_sequence(strat, _mr_ohlcv())
        assert not any(e["action"] == "enter" and e["side"] == "short" for e in events)
        assert any(e["action"] == "enter" and e["side"] == "long" for e in events)


# =====================================================================
# 4. Exit precedence — integracyjnie (TP-band vs SL same bar)
# =====================================================================
class TestExitPrecedenceIntegration:
    def test_same_bar_tp_and_sl_follows_flag(self):
        """Po wejściu long konstruujemy jeden bar który przebija i SL (Low), i
        żywą górną wstęgę (High). Domyślnie (SL-first) → reason sl_fixed;
        z tp_has_priority=True → tp_band."""
        base = {
            "bb_window": 10,
            "bb_num_std": 1.5,
            "stoch_k": 5,
            "stoch_d": 3,
            "stoch_smooth": 3,
            "entry_mode": "bb_only",
            "arm_expiry_bars": 2,
            "side": "long",
            "sl_pct": 0.02,
        }
        df = _mr_ohlcv()
        # znajdź pierwszy bar wejścia long przy SL-first, potem wstrzyknij
        # bar rozpinający oba poziomy tuż po wejściu.
        strat_probe = Strategy(dict(base))
        events = _run_sequence(strat_probe, df)
        entry_bar = next(
            e["m"] for e in events if e["action"] == "enter" and e["side"] == "long"
        )  # 1-based m
        entry_idx = entry_bar - 1
        crash_idx = entry_idx + 1  # bar zaraz po wejściu
        entry_close = float(df["Close"].iloc[entry_idx])

        df2 = df.copy()
        # bar rozpinający: Low daleko pod SL(=entry·0.98), High daleko nad wstęgą
        df2.iloc[crash_idx, df2.columns.get_loc("Low")] = entry_close * 0.90
        df2.iloc[crash_idx, df2.columns.get_loc("High")] = entry_close * 1.20
        df2.iloc[crash_idx, df2.columns.get_loc("Open")] = entry_close
        df2.iloc[crash_idx, df2.columns.get_loc("Close")] = entry_close * 1.05

        # SL-first (default) → same-bar TP&SL rozstrzyga na korzyść SL
        s_sl = Strategy(dict(base))
        ev_sl = _run_sequence(s_sl, df2)
        exit_sl = next(e for e in ev_sl if e["m"] == crash_idx + 1 and e["action"] == "exit")
        assert exit_sl["side"] == "long"
        assert exit_sl["reason"] == "sl_fixed"

        # TP-first → ten sam bar zamyka się jako TP
        s_tp = Strategy({**base, "tp_has_priority": True})
        ev_tp = _run_sequence(s_tp, df2)
        exit_tp = next(e for e in ev_tp if e["m"] == crash_idx + 1 and e["action"] == "exit")
        assert exit_tp["side"] == "long"
        assert exit_tp["reason"] == "tp_band"


# =====================================================================
# 4b. Szwy maszyny stanów armed→reaction (audyt MR-Session 1, 2026-07-11)
# =====================================================================
class TestAuditSeams:
    """Semantyki NIEPOKRYTE w sekcjach 3-4, wytypowane w independent-oracle
    pass audytu (wyrocznia: ręczne prześledzenie ścieżek on_bar; opis w
    docs/reference/modules/strategy-mean-reversion-bb-stoch.md):
    (a) re-touch w oknie armed nie odświeża licznika wygaśnięcia,
    (b) bar wyjścia nie może tego samego bara uzbroić kierunku,
    (c) touch obu wstęg jednym barem → brak uzbrojenia.
    """

    _P: ClassVar[dict] = {
        "bb_window": 10,
        "bb_num_std": 1.5,
        "stoch_k": 5,
        "stoch_d": 3,
        "stoch_smooth": 3,
        "entry_mode": "bb_only",
        "arm_expiry_bars": 1,
        "side": "both",
        "sl_pct": 0.02,
    }

    def test_arm_expiry_not_refreshed_by_retouch(self):
        """arm_expiry_bars=1: świeca S arm'uje; następna świeca BEZ reakcji
        (niedźwiedzia), ale z PONOWNYM dotknięciem wstęgi → rozbrojenie.
        Gdyby re-touch odświeżał licznik, bycza świeca bar później weszłaby
        w pozycję — asertujemy, że wejścia nie ma."""
        i = 30  # pierwszy long setup w _mr_ohlcv
        df = _mr_ohlcv()
        c = df["Close"].to_numpy().copy()
        c[i + 1] = c[i] - 0.5  # niedźwiedzi korpus + nadal głęboko pod wstęgą
        c[i + 2] = c[i + 1] + 1.5  # bycza reakcja JUŻ PO rozbrojeniu
        o = np.empty_like(c)
        o[0] = c[0]
        o[1:] = c[:-1]
        h = np.maximum(o, c) + 0.3
        low = np.minimum(o, c) - 0.3
        df2 = pd.DataFrame(
            {"Open": o, "High": h, "Low": low, "Close": c, "Volume": 1.0}, index=df.index
        )

        strat = Strategy(dict(self._P))
        enters = []
        for m in range(1, i + 4):  # ostatni prefiks kończy się na barze i+2
            sig = strat.on_bar(df2.iloc[:m])
            if sig.action == "enter":
                enters.append(m)
            if m == i + 1:  # po świecy dotknięcia S
                assert strat._armed_side == "long" and strat._armed_bars == 1
            if m == i + 2:  # po świecy bez reakcji: rozbrojony, NIE odświeżony
                assert strat._armed_side is None, "re-touch odświeżył uzbrojenie"
        # Wcześniejsze wejścia organiczne fixture (sinusoida + bb_only +
        # arm_expiry_bars=1) są poza zakresem tego testu — liczy się tylko
        # okno setupu: m=i+2 (świeca bez reakcji) i m=i+3 (bycza świeca,
        # która weszłaby TYLKO gdyby re-touch odświeżył licznik).
        critical = [m for m in enters if m in (i + 2, i + 3)]
        assert critical == [], f"wejście mimo rozbrojenia: bary {critical}"

    def test_exit_bar_does_not_arm(self):
        """Crash bar łamie SL i jednocześnie dotyka dolnej wstęgi. Exit ma
        pierwszeństwo i kończy przetwarzanie bara — dotknięcie z bara wyjścia
        NIE uzbraja. Bycza świeca zaraz po nim nie może więc być wejściem."""
        base = {**self._P, "side": "long", "arm_expiry_bars": 2}
        df = _mr_ohlcv()
        probe = Strategy(dict(base))
        events = _run_sequence(probe, df)
        entry_m = next(e["m"] for e in events if e["action"] == "enter")
        entry_idx = entry_m - 1
        crash_idx = entry_idx + 1
        nxt = crash_idx + 1
        entry_close = float(df["Close"].iloc[entry_idx])

        df2 = df.copy()
        cols = {k: df2.columns.get_loc(k) for k in ("Open", "High", "Low", "Close")}
        # crash: SL (2%) przebity z zapasem, Low głęboko pod dolną wstęgą
        df2.iloc[crash_idx, cols["Open"]] = entry_close
        df2.iloc[crash_idx, cols["Close"]] = entry_close * 0.97
        df2.iloc[crash_idx, cols["High"]] = entry_close
        df2.iloc[crash_idx, cols["Low"]] = entry_close * 0.90
        # następny bar: jawna bycza reakcja (byłaby wejściem, gdyby crash uzbroił)
        df2.iloc[nxt, cols["Open"]] = entry_close * 0.97
        df2.iloc[nxt, cols["Close"]] = entry_close * 0.99
        df2.iloc[nxt, cols["High"]] = entry_close * 0.995
        df2.iloc[nxt, cols["Low"]] = entry_close * 0.965

        strat = Strategy(dict(base))
        sig_exit = sig_next = None
        for m in range(1, nxt + 2):
            sig = strat.on_bar(df2.iloc[:m])
            if m == crash_idx + 1:
                sig_exit = sig
                assert strat._armed_side is None, "bar wyjścia uzbroił kierunek"
            if m == nxt + 1:
                sig_next = sig
        assert sig_exit is not None and sig_exit.action == "exit"
        assert sig_exit.meta["reason"] == "sl_fixed"
        assert sig_next is not None and sig_next.action != "enter"

    def test_both_bands_touch_no_arm(self):
        """Gigantyczny bar przebijający OBIE wstęgi naraz → brak jednoznacznego
        kierunku → brak uzbrojenia (jawna gałąź w on_bar)."""
        q = 60  # spokojna strefa fixture (między setupem short@50 a long@70)
        df = _mr_ohlcv()
        df2 = df.copy()
        c_q = float(df2["Close"].iloc[q])
        df2.iloc[q, df2.columns.get_loc("High")] = c_q + 15.0
        df2.iloc[q, df2.columns.get_loc("Low")] = c_q - 15.0

        strat = Strategy(dict(self._P))
        sig = None
        for m in range(1, q + 1):
            sig = strat.on_bar(df2.iloc[:m])
        # precondition fixture: przed barem q jesteśmy flat i rozbrojeni
        assert strat._pos_side is None and strat._armed_side is None, (
            "precondition fixture nie trzyma — dobierz inny bar q"
        )
        sig = strat.on_bar(df2.iloc[: q + 1])
        assert strat._armed_side is None, "touch obu wstęg uzbroił kierunek"
        assert sig.action not in ("enter", "exit")


# =====================================================================
# 5. Precompute equivalence — live vs precompute, bar po barze
# =====================================================================
class TestPrecomputeEquivalence:
    # smooth=1 (surowy %K, bez rozcieńczenia ekstremum przez wygładzanie) +
    # progi z marginesem: na barze dotknięcia %K jest jednoznacznie ekstremalny,
    # więc bb_stoch produkuje wejścia deterministycznie (nie na granicy progu).
    _P: ClassVar[dict] = {
        "bb_window": 10,
        "bb_num_std": 1.5,
        "stoch_k": 5,
        "stoch_d": 3,
        "stoch_smooth": 1,
        "entry_mode": "bb_stoch",  # ćwiczy też ścieżkę Stocha w precompute
        "stoch_oversold": 30.0,
        "stoch_overbought": 70.0,
        "arm_expiry_bars": 3,
        "side": "both",
        "sl_pct": 0.02,
    }

    def test_signals_identical(self):
        df = _mr_ohlcv()

        strat_live = Strategy(dict(self._P))  # bez precompute → per-prefiks
        strat_pre = Strategy(dict(self._P))
        strat_pre.precompute(df)
        assert strat_pre._pre is not None

        n_enter = n_exit = 0
        for m in range(1, len(df) + 1):
            prefix = df.iloc[:m]
            a = strat_live.on_bar(prefix)
            b = strat_pre.on_bar(prefix)
            assert a.action == b.action, f"bar {m}: action {a.action} != {b.action}"
            assert a.side == b.side, f"bar {m}: side {a.side} != {b.side}"
            # meta: porównaj klucze i wartości numeryczne
            assert (a.meta is None) == (b.meta is None), f"bar {m}: meta None mismatch"
            if a.meta is not None and b.meta is not None:
                assert set(a.meta) == set(b.meta), f"bar {m}: meta keys"
                for k in a.meta:
                    va, vb = a.meta[k], b.meta[k]
                    if isinstance(va, float) and isinstance(vb, float):
                        assert np.isclose(va, vb, rtol=1e-12, atol=0.0, equal_nan=True)
                    else:
                        assert va == vb
            if a.action == "enter":
                n_enter += 1
            elif a.action == "exit":
                n_exit += 1

        assert n_enter > 0, "fixture nie wygenerowała wejścia (bb_stoch) — popraw dane/progi"
        assert n_exit > 0, "fixture nie wygenerowała wyjścia"

    def test_precompute_prefix_matches_recompute(self):
        """Bezpośredni dowód kauzalności cache'u: prefiks precomputowanych serii
        == policzenie na prefiksie (BB + Stoch)."""
        from algo_bot.indicators import bbands, stochastic

        df = _mr_ohlcv(n=120)
        strat = Strategy(dict(self._P))
        strat.precompute(df)
        for m in (40, 80, 120):
            up_p, _mid, lo_p = bbands(df["Close"].iloc[:m], 10, 1.5)
            k_p, _d = stochastic(df.iloc[:m], 5, 3, 1)  # (k, d, smooth) == _P
            pd.testing.assert_series_equal(strat._pre["bb_upper"].iloc[:m], up_p, rtol=1e-12)
            pd.testing.assert_series_equal(strat._pre["bb_lower"].iloc[:m], lo_p, rtol=1e-12)
            pd.testing.assert_series_equal(strat._pre["stoch_k"].iloc[:m], k_p, rtol=1e-12)
