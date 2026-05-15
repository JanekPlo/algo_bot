# src/telemetry/journal.py
import csv, json
from pathlib import Path
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo    # py>=3.9
except Exception:
    from backports.zoneinfo import ZoneInfo  # pip install backports.zoneinfo tzdata

class Journal:
    """
    Prosty dzienniczek live:
    - trades.csv  : wejścia/wyjścia z PnL
    - equity.csv  : snapshoty equity/pozycji/wyceny w czasie
    """
    def __init__(self, run_id: str, base_dir: str = "results/live"):
        self.run_id = run_id
        self.dir = Path(base_dir) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trades_path = self.dir / "trades.csv"
        self.equity_path = self.dir / "equity.csv"
        if not self.trades_path.exists():
            with open(self.trades_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "ts_utc","ts_local","run_id","trade_id","symbol","timeframe",
                    "strategy","params","side","qty","entry_price",
                    "exit_price","reason","realized_pnl_usdt"
                ])
        if not self.equity_path.exists():
            with open(self.equity_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow([
                    "ts_utc","ts_local","run_id","symbol","timeframe",
                    "last_price","position","equity_usdt","wallet_usdt","unrealized_usdt"
                ])

    @staticmethod
    def _now_fields():
        ts_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        ts_loc = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S")
        return ts_utc, ts_loc

    def log_entry(self, trade_id: str, symbol: str, timeframe: str,
                  strategy: str, params: dict, side: str, qty: float, entry_price: float):
        ts_utc, ts_loc = self._now_fields()
        with open(self.trades_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                ts_utc, ts_loc, self.run_id, trade_id, symbol, timeframe,
                strategy, json.dumps(params, sort_keys=True),
                side, qty, entry_price,
                "", "", ""  # exit fields wypełnimy przy wyjściu
            ])

    def log_exit(self, trade_id: str, symbol: str, timeframe: str,
                 strategy: str, params: dict, side: str, qty: float,
                 entry_price: float, exit_price: float, reason: str, realized_pnl_usdt: float):
        ts_utc, ts_loc = self._now_fields()
        with open(self.trades_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                ts_utc, ts_loc, self.run_id, trade_id, symbol, timeframe,
                strategy, json.dumps(params, sort_keys=True),
                side, qty, entry_price,
                exit_price, reason, realized_pnl_usdt
            ])

    def snapshot_equity(self, symbol: str, timeframe: str, last_price: float,
                        position: float, equity_usdt=None, wallet_usdt=None, unrealized_usdt=None):
        ts_utc, ts_loc = self._now_fields()
        with open(self.equity_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                ts_utc, ts_loc, self.run_id, symbol, timeframe,
                last_price, position, equity_usdt, wallet_usdt, unrealized_usdt
            ])
