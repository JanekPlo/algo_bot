"""Regression tests for sweep-space loading and repository configs."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from algo_bot.engine.sweep import load_space_from_any

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _args(*, space_file: str | None = None, mode: str = "random") -> Namespace:
    return Namespace(
        mode=mode,
        n_samples=5,
        seed=42,
        space_json=None,
        space_file=space_file,
    )


def test_repository_random_sweep_configs_use_mapping_specs() -> None:
    """Every non-meta parameter in the checked-in random configs is a spec."""
    config_paths = sorted((PROJECT_ROOT / "config").glob("*_b*.yaml"))
    assert config_paths

    for path in config_paths:
        mode, space, _n, _seed, _implied_tf = load_space_from_any(_args(space_file=str(path)))
        assert mode == "random", path
        assert space, path
        assert all(isinstance(spec, dict) for spec in space.values()), path


def test_load_rejects_stray_yaml_key_before_sampling(tmp_path: Path) -> None:
    """A missing YAML comment marker fails during config loading."""
    path = tmp_path / "broken.yaml"
    path.write_text(
        "git# Implied timeframe: 15m\n__mode: random\nbb_window: {type: choice, values: [20]}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"Random sweep parameter 'git# Implied timeframe' must be a mapping/spec",
    ):
        load_space_from_any(_args(space_file=str(path)))
