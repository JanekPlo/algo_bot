# ADR-003: StrategyBase + Signal — unified API dla backtest i live

- **Status:** Accepted (retroactive)
- **Data:** pre-2026-05 (zapisane retroactive 2026-05-14)
- **Faza projektu:** 0 (legacy — sprzed naszej pracy)
- **Autorzy:** Janek Płoński (legacy), Janek + Claude (retroactive write-up)

## Context

Strategie tradingowe muszą działać w dwóch trybach:
1. **Backtest** — iteracyjnie po historycznych świecach, deterministycznie, na lokalnych plikach CSV
2. **Live** — w czasie rzeczywistym, po zamknięciu każdej świecy z giełdy, z faktycznymi zleceniami

Naiwna implementacja: pisać strategię dwa razy. Raz dla backtestu (jako klasa dziedzicząca po `backtesting.Strategy` z biblioteki backtesting.py), raz dla live (jako pętla w `while True`). Konsekwencje takiego stanu:

- Duplicate logika = bugi (logika "kup gdy crossover" w dwóch miejscach, prawie identyczna, ale prawie nigdy NIE identyczna)
- Backtest może zarabiać, live może tracić — bo różnice w implementacji
- Każda zmiana parametru / progu / filtra = aktualizacja w dwóch plikach
- Trudno reprodukować live trade w backteście (są inne klasy, inny stan)

W praktyce — Janek napotkał ten problem podczas iteracji nad strategiami i postanowił zbudować pojedynczy interface który ma działać identycznie w obu trybach. Wynik to klasy `StrategyBase` + `Signal` w `algo_bot/strategy_base.py`.

## Decision

**Strategia implementuje jedną metodę `on_bar(df: pd.DataFrame) -> Signal`.** Silnik (backtest lub live) wywołuje tę metodę dla każdej zamkniętej świecy. Strategia zwraca `Signal` — co chce zrobić.

### Interface

```python
@dataclass
class Signal:
    action: Optional[str] = None         # 'enter' | 'exit' | None (hold)
    side:   Optional[str] = None         # 'long'  | 'short' | None
    size:   Optional[float] = None       # liczba jednostek; None = silnik użyje domyślnego
    tp_pct: Optional[float] = None       # take profit jako % (alt: meta['tp'] absolute)
    sl_pct: Optional[float] = None       # stop loss jako %
    meta:   Optional[dict[str, Any]] = None  # extra info (tp/sl absolute, trail, debug)

class StrategyBase(ABC):
    ParamSchema: Optional[Type] = None   # dataclass z parametrami strategii

    def __init__(self, params: dict[str, Any] | Any = None) -> None: ...

    @staticmethod
    def required_features() -> set[str]:
        """Jakich kolumn wymaga strategia w df (np. 'Close', 'ATR', 'EMA_fast')."""
        return {"Close"}

    def init(self, state: Any) -> None:
        """Opcjonalna jednorazowa inicjalizacja (precompute, cache)."""
        return None

    @abstractmethod
    def on_bar(self, df: pd.DataFrame) -> Signal:
        """Główna logika — df ma ostatnie N zamkniętych świec. Zwróć Signal."""
```

### Silniki

- **Backtest**: `algo_bot/engine/backtester.py` używa `make_bt_wrapper(StrategyClass, params)` który tworzy adapter klasy `BTStrategy` (z backtesting.py) wokół naszej `StrategyBase`. Wrapper wywołuje `on_bar()`, parsuje `Signal`, executuje przez `self.buy()/self.sell()/self.position.close()`.

- **Live**: `live/live_binance.py` w pętli czeka na zamknięcie świecy (`wait_for_next_close()`), buduje df z ostatnich N świec, wywołuje `strategy.on_bar(df)`, parsuje `Signal`, executuje przez `BinanceFuturesAdapter`.

Obie ścieżki używają tej samej klasy strategii. Zmiana w strategii = zmiana raz, działa wszędzie.

### Konwencja zwracania Signal

- **Hold**: `return Signal()` (wszystkie pola None)
- **Enter**: `return Signal(action="enter", side="long", size=..., tp_pct=..., sl_pct=...)` lub z `meta={"tp": absolute_price, "sl": ..., "trail": ...}`
- **Exit**: `return Signal(action="exit", side="long")` (side opcjonalne, dla disambiguation)
- **Update levels w hold** (dla trailing stop): `return Signal(meta={"sl": new_sl, "trail": new_trail})`

### Parametryzacja

Strategia może zadeklarować `ParamSchema` jako `@dataclass`. `StrategyBase.__init__` automatycznie filtruje przekazane params do pól ParamSchema (przypadkowe klucze są ignorowane). To pozwala mieć typed params bez handcrafted constructora.

```python
@dataclass
class BghtrendPullbackParams:
    ema_fast: int = 21
    ema_mid: int = 89
    # ...

class Strategy(StrategyBase):
    ParamSchema = BghtrendPullbackParams
    # ...
```

## Consequences

**Pozytywne:**
- DRY — strategia pisana raz, działa w backtest i live
- Reproducibility — live trade ma odpowiednik w backteście (bar-by-bar)
- Testowalność — możemy w teście wywoływać `strategy.on_bar(df_fixture)` bez całego silnika
- Typed params przez dataclass — IDE autocompletion, mypy validation
- Łatwa zmiana parametru — edytuj ParamSchema dataclass, defaulty są tam, override przez `--params '{"ema_fast":34}'`
- Strategia agnostyczna wobec executora — `on_bar` nie wie czy live czy backtest, tylko widzi DataFrame

**Negatywne / koszty:**
- Strategia nie ma dostępu do "raw" backtesting.py API (np. `self.I()` registering indicators) — musi liczyć wskaźniki w `on_bar` bezpośrednio na df. Wolniejsze niż backtesting.py natywne (ale dla MVP akceptowalne)
- `Signal` jako dataclass jest "płaski" — nie ma natywnej obsługi multi-asset signals (jedna strategia, jeden symbol). Dla portfolio strategii (faza 2+) trzeba będzie rozszerzyć
- Adapter w `make_bt_wrapper` ma ~150 linii logiki interpretacji Signal — to dodatkowa warstwa do debugowania gdy coś idzie nie tak (TP nie wystrzelił, SL nie zamknął)
- Strategia która chce użyć backtesting.py `self.I()` (lazy-evaluated indicators) musi być przepisana — niektóre stare strategie (`bollinger_band_breakout_short.py`, `short_trend_following.py`) dziedziczą bezpośrednio po `backtesting.Strategy` zamiast `StrategyBase`

**Ryzyka:**
- Jeśli silnik backtest i live interpretują `Signal` choćby trochę inaczej (np. timing entry — na zamknięciu czy otwarciu następnej świecy) — wraca problem z "backtest zarabia, live traci". Mitigation: w fazie 3 (paper trading) sprawdzimy bar-by-bar signal match między backtest a live na tym samym okresie.
- `meta` field jako `dict[str, Any]` jest typeless — łatwo zrobić literówkę (`"tp_pct"` vs `"tp_percentage"`) i nie zauważyć. Mitigation: konwencja kluczy w `meta` udokumentowana w `algo_bot/strategy_base.py` docstring + checked w testach (do zrobienia).

## Alternatives Considered

- **Dwa różne API dla backtest i live** (naive) — strategia jako dwie klasy. Odrzucone bo: duplicate logic = bugs, brak reproducibility. Patrz Context.

- **Strategia dziedziczy z `backtesting.Strategy` (z biblioteki)** — używamy natywnego API backtesting.py, dla live piszemy osobny wrapper który symuluje to API. Odrzucone bo: backtesting.py jest tightly coupled z `bt.run()` lifecycle (init/next/data), nie da się tego sensownie odpalić poza Backtest object. Wymaga rebuild od zera dla live. Komplikuje testing.

- **Strategia jako generator** (`yield Signal` zamiast `return Signal`) — koroutyny zamiast call-and-return. Odrzucone bo: state management w generatorach jest trudniejszy, debug trudniejszy, async-ish style nie pasuje do bar-by-bar mental modelu.

- **Strategia jako Trio/asyncio event loop** — Strategy uruchamia własny loop, silniki sterują "tickami". Odrzucone bo: over-engineering dla MVP, asyncio dodaje complexity której nie potrzebujemy gdy bar = 5min/1h/4h (nie ms).

- **Inspirować się Lean Engine (C#) lub Backtrader (Python)** — duże frameworki z bogatym API. Odrzucone bo: scope creep dla single-developer projektu, custom API jest prościejsze i pełni 90% potrzeb.

## References

- Plik: `algo_bot/strategy_base.py` (88 linii)
- Wrapper backtest: `algo_bot/engine/backtester.py::make_bt_wrapper` (linie ~170-330)
- Wrapper live: `live/live_binance.py` (cały plik to ten wrapper)
- Przykładowa strategia używająca pełny API: `algo_bot/strategies/bghtrend_pullback.py` (333 linie z trail/cooldown/state)
- Powiązane: ADR-005 (backtesting.py jako MVP engine — wymusza ten interface)
