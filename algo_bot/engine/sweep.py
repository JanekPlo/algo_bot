#!/usr/bin/env python3
"""
algo_bot/engine/sweep.py

Sweep parametrów strategii — grid search albo random search. Wywołuje
run_backtest per kombinacja parametrów, agreguje wyniki w results/experiments/index.csv.

Public API:
- expand_param_space(mode, space, n_samples, seed) -> Iterable[dict]
    Generator kombinacji parametrów. mode='grid' lub 'random'.
- gen_walk_forward_windows(df, train_bars, test_bars, step_bars) -> List[(start, end)]
    Generator okien testowych dla walk-forward analysis (opcjonalny dla CLI).
- extract_metrics(stats: dict) -> dict
    Wyciąga podzbiór metryk z stats (Sharpe, Calmar, ...) do agregacji.
- append_index_row(row: dict) -> None
    Dopisuje wiersz do results/experiments/index.csv (csv.DictWriter).

Konwencja przestrzeni parametrów (YAML/JSON):
- grid mode: {"short":[5,9,13], "long":[21,34], ...}
- random mode: {"short":{"type":"int","min":5,"max":25}, "long":{"type":"float","min":0.1,"max":2.0,"step":0.1}}

CLI:
- python -m algo_bot.engine.sweep --help
- algo-sweep --help (po pip install -e .)

See also:
- docs/adr/005-backtesting-py-mvp-engine.md
- docs/guides/running-sweep.md (TBD)
- ROADMAP fasa 2 (walk-forward MVP)
"""

from __future__ import annotations

import csv
import importlib
import itertools
import json
import os
import random
from collections.abc import Iterable
from dataclasses import is_dataclass
from datetime import datetime
from typing import Any

import pandas as pd

try:
    import yaml  # optional
except ImportError:
    yaml = None  # type: ignore[assignment]

# używamy istniejącego backtestera jako silnika
from algo_bot.engine.backtester import run_backtest, run_id as make_run_id, save_outputs

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
EXP_DIR = os.path.join(PROJECT_ROOT, "results", "experiments")
INDEX_CSV = os.path.join(EXP_DIR, "index.csv")


# ------------------------------
# Strategy loader / ParamSchema
# ------------------------------
def resolve_strategy_class(name: str):
    m = importlib.import_module(f"algo_bot.strategies.{name}")
    if hasattr(m, "Strategy"):
        return m.Strategy
    # legacy CamelCase fallback
    cls_name = "".join(p.capitalize() for p in name.split("_"))
    if hasattr(m, cls_name):
        return getattr(m, cls_name)
    raise AttributeError(f"algo_bot.strategies.{name} must expose Strategy (or {cls_name})")


def coerce_params(StratClass, params_dict: dict[str, Any] | None) -> dict[str, Any]:
    """
    Zwraca dict przefiltrowany do pól ParamSchema (jeśli jest),
    żeby przypadkowe klucze nie wysadzały strategii.
    """
    if params_dict is None:
        return {}
    schema = getattr(StratClass, "ParamSchema", None)
    if schema and is_dataclass(schema):
        allowed = {f.name for f in schema.__dataclass_fields__.values()}
        return {k: v for k, v in params_dict.items() if k in allowed}
    return params_dict


# ------------------------------
# Param space parsing
# ------------------------------
def _product_dict(items: dict[str, list[Any]]) -> Iterable[dict[str, Any]]:
    keys = list(items.keys())
    for values in itertools.product(*[items[k] for k in keys]):
        yield dict(zip(keys, values, strict=True))


def _sample_from_spec(spec: dict[str, Any], rng: random.Random) -> Any:
    """
    Random search sampler. Spec elementy:
      {"type":"int","min":5,"max":25}
      {"type":"float","min":0.1,"max":2.0,"step":0.1} (opcjonalny step → snapping)
      {"type":"choice","values":[...]}
    """
    typ = spec.get("type")
    if typ == "int":
        return rng.randint(int(spec["min"]), int(spec["max"]))
    if typ == "float":
        lo, hi = float(spec["min"]), float(spec["max"])
        x = rng.random() * (hi - lo) + lo
        step = spec.get("step")
        if step:
            s = float(step)
            x = round(round(x / s) * s, 10)
        return x
    if typ == "choice":
        vals = list(spec["values"])
        return rng.choice(vals)
    raise ValueError(f"Unsupported random spec: {spec}")


def expand_param_space(
    mode: str, space: dict[str, Any], n_samples: int = 50, seed: int = 42
) -> Iterable[dict[str, Any]]:
    """
    mode='grid':  space = {"short":[5,9,13], "long":[21,34], ...}
    mode='random': space = {"short":{"type":"int","min":5,"max":25}, ...}
    """
    if mode == "grid":
        return _product_dict(space)
    elif mode == "random":
        rng = random.Random(seed)
        keys = list(space.keys())
        out = []
        for _ in range(int(n_samples)):
            d = {}
            for k in keys:
                d[k] = _sample_from_spec(space[k], rng)
            out.append(d)
        return out
    else:
        raise ValueError("mode must be 'grid' or 'random'")


# ------------------------------
# Walk-forward splitter (opcjonalny)
# ------------------------------
def gen_walk_forward_windows(
    df: pd.DataFrame, train_bars: int, test_bars: int, step_bars: int | None = None
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """
    Zwraca listę (start, end) dla okien TEST (to te, które testujemy).
    Train przyjmujemy: [start_train, end_train], ale do run_backtest podajemy tylko zakres TEST.
    Uproszczenie: to caller decyduje, czy parametry stałe dla wszystkich okien, czy robi osobną optymalizację.
    """
    if step_bars is None:
        step_bars = test_bars
    idx = df.index
    windows = []
    i = train_bars
    while i + test_bars <= len(idx):
        start_test = idx[i]
        end_test = idx[i + test_bars - 1]
        windows.append((start_test, end_test))
        i += step_bars
    return windows


# ------------------------------
# Metrics pick
# ------------------------------
KEEP_KEYS = [
    "Start",
    "End",
    "Duration",
    "Return [%]",
    "Return (Ann.) [%]",
    "Sharpe Ratio",
    "Sortino Ratio",
    "Max. Drawdown [%]",
    "SQN",
    "Win Rate [%]",
    "Avg. Trade",
    "Trades",
    "Exposure Time [%]",
]


def extract_metrics(stats: dict[str, Any]) -> dict[str, Any]:
    d = {}
    for k in KEEP_KEYS:
        if k in stats:
            d[k] = stats[k]
    # mikrostruktura/funding info jeśli jest
    if "_microstructure" in stats:
        d.update({f"ms:{k}": v for k, v in stats["_microstructure"].items()})
    if "_funding" in stats:
        d.update({f"fund:{k}": v for k, v in stats["_funding"].items()})
    return d


# ------------------------------
# Index writer
# ------------------------------
def append_index_row(row: dict[str, Any]) -> None:
    os.makedirs(EXP_DIR, exist_ok=True)
    write_header = not os.path.exists(INDEX_CSV)
    with open(INDEX_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
        w.writerow(row)


# ------------------------------
# CLI
# ------------------------------
def parse_args():
    import argparse

    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    ap.add_argument(
        "--strategy", required=True, help="nazwa modułu w strategies/, np. bghtrend_pullback"
    )
    ap.add_argument("--symbols", nargs="+", required=True, help="np. BTC/USDT ETH/USDT")
    ap.add_argument("--timeframes", nargs="+", required=True, help="np. 5m 15m 1h")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")

    # param space – dwie drogi
    ap.add_argument(
        "--space_json",
        default=None,
        help="JSON z definicją przestrzeni (patrz README). Jeśli podasz, nadpisuje --space_file.",
    )
    ap.add_argument(
        "--space_file", default=None, help="Ścieżka do .json/.yaml z przestrzenią parametrów."
    )

    ap.add_argument("--mode", choices=["grid", "random"], default="grid")
    ap.add_argument("--n_samples", type=int, default=50, help="dla random")
    ap.add_argument("--seed", type=int, default=42)

    # backtest engine params (wspólne)
    ap.add_argument("--cash", type=float, default=200_000.0)
    ap.add_argument("--commission", type=float, default=0.0004)
    ap.add_argument("--trade_on_close", action="store_true")
    ap.add_argument("--spread_bps", type=float, default=None)
    ap.add_argument("--slippage_bps", type=float, default=None)
    ap.add_argument("--funding_csv", default=None)
    ap.add_argument("--funding_mode", choices=["events", "accrual"], default=None)

    # walk-forward (opcjonalnie)
    ap.add_argument(
        "--wf_train_bars", type=int, default=None, help="jesli podane - wlacza walk-forward"
    )
    ap.add_argument("--wf_test_bars", type=int, default=None)
    ap.add_argument("--wf_step_bars", type=int, default=None)

    return ap.parse_args()


def load_space_from_any(args) -> tuple[str, dict[str, Any], int, int]:
    """
    Zwraca (mode, space, n_samples, seed)
    """
    mode = args.mode
    n_samples = args.n_samples
    seed = args.seed

    if args.space_json:
        space = json.loads(args.space_json)
    elif args.space_file:
        ext = os.path.splitext(args.space_file)[1].lower()
        with open(args.space_file) as f:
            if ext in (".yaml", ".yml"):
                if yaml is None:
                    raise SystemExit("Zainstaluj pyyaml lub użyj JSON")
                doc = yaml.safe_load(f)
            else:
                doc = json.load(f)
        # pozwalamy nadpisać tryb i n/seed w pliku
        mode = doc.get("__mode", mode)
        n_samples = int(doc.get("__n", n_samples))
        seed = int(doc.get("__seed", seed))
        space = {k: v for k, v in doc.items() if not k.startswith("__")}
    else:
        raise SystemExit("Podaj --space_json lub --space_file")

    return mode, space, n_samples, seed


def main():
    args = parse_args()

    StratClass = resolve_strategy_class(args.strategy)

    # wczytaj/rozszerz przestrzeń parametrów
    mode, space_raw, n_samples, seed = load_space_from_any(args)
    # oczyść klucze wg ParamSchema
    space_clean: dict[str, Any] = {}
    if mode == "grid":
        for k, lst in space_raw.items():
            # k: lista wartości
            space_clean[k] = lst
    else:  # random
        space_clean = space_raw

    # generator zestawów parametrów
    combos = list(expand_param_space(mode, space_clean, n_samples=n_samples, seed=seed))
    total_jobs = len(args.symbols) * len(args.timeframes) * len(combos)

    print(f"[SWEEP] strategy={args.strategy} mode={mode} jobs={total_jobs}")

    job_i = 0
    for sym in args.symbols:
        for tf in args.timeframes:
            # walk-forward?
            wf_windows = None
            if args.wf_train_bars and args.wf_test_bars:
                # weź dane, wyznacz okna testowe (tylko indeksy; właściwy run robi backtester)
                # backtester sam czyta CSV – tutaj tylko budujemy okna na potrzeby pętli
                # minimalny parser dat:
                data_path = os.path.join(
                    PROJECT_ROOT,
                    "bot_data",
                    "processed",
                    f"binance_{sym.replace('/', '')}_{tf}.csv",
                )
                df_tmp = (
                    pd.read_csv(data_path, parse_dates=["datetime"])
                    .set_index("datetime")
                    .sort_index()
                )
                df_tmp = df_tmp[
                    (df_tmp.index >= pd.to_datetime(args.start))
                    & (df_tmp.index <= pd.to_datetime(args.end))
                ]
                wf_windows = gen_walk_forward_windows(
                    df_tmp, args.wf_train_bars, args.wf_test_bars, args.wf_step_bars
                )

            for p in combos:
                job_i += 1
                # oczyść paramy wg ParamSchema (żeby nie przechodziły śmieci)
                p_clean = coerce_params(StratClass, p)

                # bez walk-forward
                if not wf_windows:
                    rid = make_run_id(args.strategy, sym, tf, p_clean)
                    stats, equity, trades = run_backtest(
                        symbol=sym,
                        timeframe=tf,
                        strategy=args.strategy,
                        params=p_clean,
                        start=args.start,
                        end=args.end,
                        cash=args.cash,
                        commission=args.commission,
                        trade_on_close=args.trade_on_close,
                        slippage_bps=args.slippage_bps,
                        spread_bps=args.spread_bps,
                        # funding opcjonalnie – backtester sam rozpozna None:
                        **(
                            {"funding_csv": args.funding_csv, "funding_mode": args.funding_mode}
                            if args.funding_csv
                            else {}
                        ),
                    )
                    outdir = save_outputs(
                        rid, sym, tf, args.strategy, p_clean, stats, equity, trades
                    )
                    row = {
                        "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        "run_id": rid,
                        "strategy": args.strategy,
                        "symbol": sym,
                        "timeframe": tf,
                        "mode": mode,
                        "params": json.dumps(p_clean, sort_keys=True),
                        "path": outdir,
                        **extract_metrics(dict(stats)),
                    }
                    append_index_row(row)
                else:
                    # walk-forward: lecimy po oknach TEST
                    for k, (tstart, tend) in enumerate(wf_windows, start=1):
                        rid = make_run_id(args.strategy, sym, tf, {**p_clean, "_wf": k})
                        stats, equity, trades = run_backtest(
                            symbol=sym,
                            timeframe=tf,
                            strategy=args.strategy,
                            params=p_clean,
                            start=str(pd.to_datetime(tstart).date()),
                            end=str(pd.to_datetime(tend).date()),
                            cash=args.cash,
                            commission=args.commission,
                            trade_on_close=args.trade_on_close,
                            slippage_bps=args.slippage_bps,
                            spread_bps=args.spread_bps,
                            **(
                                {"funding_csv": args.funding_csv, "funding_mode": args.funding_mode}
                                if args.funding_csv
                                else {}
                            ),
                        )
                        outdir = save_outputs(
                            rid, sym, tf, args.strategy, p_clean, stats, equity, trades
                        )
                        row = {
                            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                            "run_id": rid,
                            "strategy": args.strategy,
                            "symbol": sym,
                            "timeframe": tf,
                            "mode": f"{mode}+walkforward",
                            "wf_window": k,
                            "params": json.dumps(p_clean, sort_keys=True),
                            "path": outdir,
                            **extract_metrics(dict(stats)),
                        }
                        append_index_row(row)

                if job_i % 10 == 0 or job_i == total_jobs:
                    print(f"[{job_i}/{total_jobs}] {sym} {tf} params={p_clean}")

    print(f"[DONE] index → {INDEX_CSV}")


if __name__ == "__main__":
    main()
# BGH APPROVED
