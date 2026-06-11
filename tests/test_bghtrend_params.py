"""
tests/test_bghtrend_params.py

Testy walidacji parametrów strategii bghtrend_pullback (tail-end cleanup
2026-06-11). Inwariant EMA monotonicity: ema_fast < ema_mid < ema_slow,
egzekwowany w ``XtrenderPullbackParams.__post_init__``.

Bez mocków — czyste konstruktory dataclass (mindset reguła #3).
"""

from __future__ import annotations

import pytest

from algo_bot.strategies.bghtrend_pullback import XtrenderPullbackParams


class TestEmaMonotonicityValidation:
    def test_defaults_pass(self):
        """Defaults 21/89/200 spełniają inwariant — konstruktor przechodzi."""
        p = XtrenderPullbackParams()
        assert (p.ema_fast, p.ema_mid, p.ema_slow) == (21, 89, 200)

    def test_inverted_raises_value_error(self):
        """Odwrócona hierarchia (200/89/21) → ValueError z nazwami pól."""
        with pytest.raises(ValueError, match=r"ema_fast=200.*ema_mid=89.*ema_slow=21"):
            XtrenderPullbackParams(ema_fast=200, ema_mid=89, ema_slow=21)

    def test_equal_fast_mid_raises(self):
        """Równość ema_fast == ema_mid też odrzucona (ostra nierówność —
        dwie identyczne EMA to degeneracja warunku trendu)."""
        with pytest.raises(ValueError, match="Inwariant EMA naruszony"):
            XtrenderPullbackParams(ema_fast=89, ema_mid=89, ema_slow=200)

    def test_equal_mid_slow_raises(self):
        with pytest.raises(ValueError, match="Inwariant EMA naruszony"):
            XtrenderPullbackParams(ema_fast=21, ema_mid=200, ema_slow=200)

    def test_sweep_config_extremes_pass(self):
        """Brzegowe kombinacje z zakresów config/bghtrend_b1..b4.yaml przechodzą.

        Dowód że sweepy Fazy 2 nie mogą trafić na ValueError: w każdym
        configu max(ema_fast) < min(ema_mid) < min(ema_slow).
        Zakresy (stan 2026-06-11):
          b1/b2: fast 13..21, mid {55,89},    slow {200}
          b3:    fast 9..15,  mid {45,55,89}, slow {200}
          b4:    fast 21..25, mid 89..110,    slow {200,220}
        """
        extremes = [
            (21, 55, 200),  # b1/b2: max fast, min mid
            (13, 89, 200),  # b1/b2: min fast, max mid
            (15, 45, 200),  # b3: max fast, min mid
            (9, 89, 200),  # b3: min fast, max mid
            (25, 89, 200),  # b4: max fast, min mid, min slow
            (21, 110, 220),  # b4: min fast, max mid, max slow
        ]
        for fast, mid, slow in extremes:
            p = XtrenderPullbackParams(ema_fast=fast, ema_mid=mid, ema_slow=slow)
            assert p.ema_fast < p.ema_mid < p.ema_slow
