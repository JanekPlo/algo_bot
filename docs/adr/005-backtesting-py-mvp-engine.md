# ADR-005: backtesting.py jako silnik backtestowy MVP

- **Status:** Accepted (retroactive)
- **Data:** pre-2026-05 (zapisane retroactive 2026-05-14)
- **Faza projektu:** 0 (legacy)
- **Autorzy:** Janek Płoński (legacy), Janek + Claude (retroactive write-up)

## Context

algo_bot potrzebuje silnika do uruchamiania strategii na historycznych danych OHLCV i raportowania: equity curve, statystyk (Sharpe, drawdown, win rate), log transakcji. Python ma kilka dojrzałych bibliotek do tego celu:

- **backtesting.py** ([kernc/backtesting.py](https://github.com/kernc/backtesting.py)) — lightweight, ~3k linii, single-asset, używa pandas, prosty API z `bt.run()` i `bt.optimize()`
- **vectorbt** — high-performance, multi-asset, używa NumPy operations vectorized (50-100x szybsze niż backtesting.py), bardziej zaawansowane analytics
- **vectorbt-pro** — komercyjna wersja vectorbt z więcej feature'ami
- **Backtrader** — ~10k linii, multi-asset, multi-broker, bardzo configurable ale złożone
- **Lean (QuantConnect)** — C# core z Python bindings, production-grade, ale ciężki w setup
- **nautilus_trader** — modern event-driven, production-grade live trading, multi-venue

Janek początkowo wybrał backtesting.py i napisał strategię/sweep wokół niej. Decyzja podjęta intuicyjnie, ale w trakcie pracy nad ROADMAP (faza 2) pytanie wraca: czy migrować na vectorbt dla szybszych sweepów? Na nautilus dla production live?

Kontekst kosztów migracji:
- `algo_bot/engine/backtester.py` (532 linie) i `algo_bot/engine/sweep.py` (352 linie) są napisane WOKÓŁ backtesting.py API
- 7 strategii (333 + 6×~50 linii) korzysta z `StrategyBase` + `Signal` (ADR-003), które są agnostyczne wobec silnika — łatwiej zmienić silnik niż się wydaje
- Adapter `make_bt_wrapper` w backtester.py (linie ~170-330) parsuje `Signal` na backtesting.py API — to ~150 linii do przepisania pod inny silnik

## Decision

**Zostajemy z backtesting.py do końca fazy 4 (Live Mainnet MVP).** Konkretnie:

1. Wszystkie nowe strategie pisane z `StrategyBase` (ADR-003), nie z `backtesting.Strategy` natywnie — pozostają silnik-agnostyczne
2. `algo_bot/engine/backtester.py` używa backtesting.py jako engine, ale to jest **adapter layer** — strategia nie wie że to backtesting.py pod spodem
3. Sweep i walk-forward (decyzja F) też używają backtesting.py jako primitive
4. **Rewizja decyzji w fazie 4-5** — gdy MVP będzie działać, ocenimy czy zysk z migracji (perf, multi-asset, live-grade) wart kosztu

### Co backtesting.py daje:
- Single-asset OHLCV → equity curve + trades + stats
- `bt.run(strategy, **params)` — pojedynczy backtest
- `bt.optimize(strategy, **param_grid)` — grid search z constraint
- Wskaźniki przez `self.I()` (lazy-eval, integrated z plotami) — ale my tego nie używamy (StrategyBase wylicza wskaźniki w `on_bar`)
- `_equity_curve`, `_trades` — pandas DataFrames do dalszej analizy
- Plot w HTML (nie używamy w batch, w notebookach przydatne)

### Czego NIE daje (i co zaadresujemy lokalnie):
- Multi-asset portfolio (na razie nie potrzebne, MVP = 1 strategy 1 symbol)
- Funding rate dla perp futures (zaadresujemy w `algo_bot/funding.py` + post-processing trades w `adjust_trades_df`)
- Realistic microstructure (slippage, spread) — adresujemy w `adjust_trades_df` (post-run adjust trades CSV)
- Walk-forward (zaadresujemy w `algo_bot/engine/walkforward.py` jako orchestrator który wywołuje backtester.py per fold)

## Consequences

**Pozytywne:**
- Działa OD ZARAZ — Janek już ma backtester + sweep + integration z live (przez StrategyBase). Brak refaktoru.
- Mały learning curve — backtesting.py ma czytelny kod ~3k linii, łatwo go zrozumieć/debugować
- Powiązanie z `StrategyBase` (ADR-003) — strategia jest agnostyczna, możemy zmienić silnik później bez przepisywania strategii
- Sweep i optimize "for free" (backtesting.py daje optimize() natywnie)
- Plot z `bt.plot()` przydatny w notebookach do wizualizacji
- Active maintenance — kernc/backtesting.py jest aktywne (commit w ostatnich miesiącach, 5.5k gwiazdek)

**Negatywne / koszty:**
- **Wydajność**: backtesting.py jest single-threaded, Python-bound, używa pandas indexing. Sweep 1000+ kombinacji parametrów na 5min dataset 2020-2025 = wiele minut (vs sekundy w vectorbt). Akceptowalne dla MVP, blokujące dla intensive research.
- **Single-asset**: portfolio strategii (np. trend BTC + funding arb ETH) musi być uruchamiane osobno per asset, agregacja PnL manualnie
- **Live**: backtesting.py NIE jest live-capable (brak interface'u event-driven). Live mamy osobno w `live/live_binance.py` (niewykorzystując backtesting.py). To dwa różne silniki, łączone tylko przez `StrategyBase`.
- **TP/SL handling**: backtesting.py natywnie obsługuje SL/TP per-order, ale my chcemy bardziej skomplikowanej logiki (trailing stop, TP-has-priority on same bar, cooldown). Robimy to w `make_bt_wrapper` adapterze (~150 linii custom logic). Złożoność.

**Ryzyka:**
- Jeśli faza 2 (research) okaże się że sweep jest za wolny dla naszych potrzeb (np. 10k+ kombinacji × 5 lat × 5min bars), będziemy musieli migrować. Plan B: vectorbt dla sweepów (gdzie liczy się szybkość), backtesting.py zachowane dla pojedynczych backtestów (gdzie liczy się plot/debug).
- Jeśli faza 4 (live mainnet) ujawni że nasz custom live runner ma problemy production-grade (recovery, multi-venue), będziemy migrować live na nautilus. Nie wpływa na backtest, ale wymaga że `StrategyBase` jest kompatybilny z nautilus API (do sprawdzenia).
- Backtesting.py może mieć subtelne różnice timing od live (np. trade execution na open vs close świecy). W fazie 3 (paper trading) systematyczne porównanie.

## Alternatives Considered

- **vectorbt** — 50-100x szybciej dla sweepów, multi-asset native, ale: większy learning curve, zupełnie inny API (vectorized, nie event-loop), 1100+ linii custom code do przepisania, brak natywnego "live trading" use-case. Odrzucone na MVP bo: koszt migracji > zysk dopóki nie mamy działającej strategii. Po MVP — kandydat na rewizję.

- **vectorbt-pro** — jeszcze więcej feature'ów (portfolio optimization, advanced metrics), ale komercyjne (płatne) i tied vendor lock-in. Odrzucone bo: nie chcemy płatnych zależności w fazie 1.

- **Backtrader** — multi-asset, multi-broker, production-grade live, ale: ~10x więcej kodu niż backtesting.py, bardziej skomplikowany API, mniej aktywnie maintained (ostatni release 2023). Odrzucone bo: złożoność nieproporcjonalna do MVP scope.

- **Lean (QuantConnect)** — production-grade, używany przez profesjonalne fundy, ale: C# core z Python bindings = trudniejszy debug, wymaga ich cloud lub lokalny Lean Engine setup, ciężki w VPS deployment. Odrzucone bo: scope creep dla single-developer projektu.

- **nautilus_trader** — modern event-driven, multi-venue, production live trading + backtest unified. Najbardziej eleganckie rozwiązanie architekturalnie. Odrzucone na MVP bo: relatywnie młody (active dev 2024+), mniej tutoriali, learning curve. **Główny kandydat na migrację po MVP** — `StrategyBase` API jest na tyle podobne że adapter byłby możliwy.

- **Custom event-driven engine** — zbudować swój silnik od zera. Odrzucone bo: scope creep, takie projekty trwają miesiące, nie tygodnie.

## References

- Plik: `algo_bot/engine/backtester.py` (532 linie, używa `from backtesting import Backtest, Strategy as BTStrategy`)
- Plik: `algo_bot/engine/sweep.py` (352 linie)
- [backtesting.py docs](https://kernc.github.io/backtesting.py/)
- [vectorbt comparison](https://vectorbt.dev/)
- [nautilus_trader](https://nautilustrader.io/)
- Powiązane: ADR-003 (StrategyBase Signal — interface niezależny od silnika, ułatwia future migration)

## Notes

- **Po fazie 4** (live mainnet MVP) zrobimy formalną rewizję — osobny ADR (np. ADR-0XX). Kryteria decyzji o migracji:
  - Czy sweep czas blokuje research (>30 min na sweep)? → migracja na vectorbt
  - Czy multi-asset portfolio jest priorytetem? → migracja na vectorbt lub nautilus
  - Czy chcemy unified backtest+live engine? → migracja na nautilus
  - Jeśli żadne z powyższych — zostajemy z backtesting.py.
