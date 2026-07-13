"""Beta-0 runtime and binary dependency smoke tests."""

from __future__ import annotations

import sys
from importlib.metadata import version

import numpy as np
import talib


def test_runtime_and_engine_pins() -> None:
    """The supported runtime and engine-critical packages are exact."""
    import nautilus_trader

    assert sys.version_info[:2] == (3, 12)
    assert version("nautilus-trader") == "1.230.0"
    assert version("TA-Lib") == "0.7.0"
    assert nautilus_trader.__version__ == "1.230.0"


def test_talib_binary_wheel_executes_c_library() -> None:
    """Import success alone is insufficient: execute a deterministic SMA."""
    actual = talib.SMA(np.array([1.0, 2.0, 3.0, 4.0]), timeperiod=2)
    np.testing.assert_allclose(actual[1:], np.array([1.5, 2.5, 3.5]))
