# Glossary

Terminologia trading + nasze specyficzne pojęcia. Alfabetycznie.

---

## A

**ADR (Architecture Decision Record)**
Lekki dokument w `docs/adr/NNN-tytul.md` zapisujący *dlaczego* podjęliśmy konkretną decyzję architektoniczną. Patrz [docs/adr/README.md](../adr/README.md).

**API**
Application Programming Interface. W algo_bot: 1) publiczne interfejsy modułów (`StrategyBase.on_bar`), 2) API zewnętrzne giełd (Binance REST + WebSocket przez CCXT).

**Anchored walk-forward**
Wariant walk-forward gdzie okno *train* rośnie z każdym foldem (anchored to początku danych), a okno *test* zostaje stałe. Vs **rolling walk-forward** gdzie oba okna mają stały rozmiar i przesuwają się razem.

**ATR (Average True Range)**
Wskaźnik zmienności — średnia "prawdziwego zakresu" świecy (max z {high-low, |high-prevClose|, |low-prevClose|}). Używany w `bghtrend_pullback` do dynamicznego SL i trailing stop.

---

## B

**Backtest**
Symulacja strategii na historycznych danych — odtwarzamy "co by było gdyby strategia była aktywna w przeszłości". Pełen review: PnL, Sharpe, drawdown, win rate. **Nie jest gwarancją** przyszłej wydajności (curve fitting risk).

**Backtest-live mismatch**
Sytuacja gdy strategia w backteście pokazuje profit, ale w live traci. Typowe przyczyny: knoty na thin markets, slippage, funding, kolejność wykonania orderów, latencja, look-ahead bias w backteście.

**`backtesting.py`**
Biblioteka Python ([github.com/kernc/backtesting.py](https://github.com/kernc/backtesting.py)) używana przez legacy silnik backtestowy algo_bot. W Beta 0 pozostaje przypięta do 0.6.5 jako ścieżka porównawcza podczas migracji do NautilusTrader. Patrz [ADR-005](../adr/005-backtesting-py-mvp-engine.md).

**`BacktestResult`**
Wersjonowany, engine-neutral artifact P8. Oprócz legacy tuple przechowuje engine i
wersję, stan drzewa Git, hashe danych/konfiguracji, seed, statystyki oraz ledgery
equity/trades/orders/fills/positions/funding. Cost eligibility jest fail-closed;
„native” nie oznacza automatycznie „realistyczne”.

**BBANDS (Bollinger Bands)**
Wskaźnik = SMA(N) + K*std(N) i SMA(N) - K*std(N). Typowo N=20, K=2. Używany jako proxy zmienności i poziomów wsparcia/oporu.

**Bot data**
Folder `bot_data/` (gitignored) — przechowuje dane historyczne OHLCV. `raw/` to surowe z giełdy, `processed/` to z policzonymi featurami.

---

## C

**Calmar ratio**
Annualized return / max drawdown. Mierzy efektywność z perspektywy największej straty. Im wyższy tym lepszy. Threshold MVP: > 0.5 OOS.

**CCXT**
Crypto eXchange Trading Library — Python lib unified API dla 100+ giełd. W algo_bot używamy do Binance i Bybit.

**Conda env**
Izolowane środowisko Python zarządzane przez Condę. W algo_bot był to
historyczny setup oparty na `environment.yml` i TA-Lib z conda-forge; od Beta 0
jest **superseded** przez uv, `.python-version` i `uv.lock`. Conda nie jest
równorzędnym defaultem i może wrócić tylko jako odseparowany fallback po
wykazaniu konkretnego blockera platformy.

**CPython**
Referencyjna implementacja Pythona. Runtime Beta 0 używa vanilla CPython
3.12.13 przypiętego w `.python-version`; uv pobiera go automatycznie podczas
`uv sync --locked`.

**`config.yaml`**
Główny plik konfiguracyjny w `config/`. Zawiera paths, backtest defaults, features, params per strategia.

**Cooldown**
Okres po stop-loss kiedy strategia nie otwiera nowej pozycji. W `bghtrend_pullback`: `cooldown_bars=10` (10 świec ciszy po SL). Zapobiega "rewenge trading" na bezpośrednio po straty.

---

## D

**DCA (Dollar Cost Averaging)**
Strategia kupowania stałej kwoty USD co N czasu, niezależnie od ceny. Implementowana w `algo_bot/strategies/dca_btc.py`. Reduces timing risk za cenę nie-optimal entries.

**Deadzone**
Martwa strefa wokół zera w oscylatorze momentum. W `bghtrend_pullback`: `|xtrender long_term| <= deadzone` (≈1.5–5) oznacza "brak wyraźnego momentum reżimowego" → entry odfiltrowane, a w pozycji włącza warunek stale-exit (korekta 2026-06-11 — gate liczony na legu `long_term`, nie `short_t3`; patrz indicators-xtrender.md). Zamienia ciągły oscylator w sygnał trójstanowy (bull / flat / bear). Jeden z najbardziej narażonych na overfitting parametrów strategii.

**Deploy key**
SSH key z dostępem do JEDNEGO repo (vs personal SSH key z dostępem do wszystkich). W algo_bot używany przez sandbox/CI do push. Patrz `.deploy_key` (gitignored).

**Determinism**
Backtest jest *deterministyczny* jeśli odpalony dwa razy z tym samym kodem, danymi, seedem zwraca BIT-IDENTYCZNE wyniki. Krytyczne dla reprodukcji i regression testing.

**Drawdown (DD)**
Spadek z poprzedniego peak'a equity. **Max Drawdown** = największy taki spadek w całej historii. Mierzy psychologiczny i finansowy ból. Threshold MVP: max DD < 25% OOS.

**Durable outbox**
Lista stabilnie identyfikowanych intencji zewnętrznych zapisana razem ze stanem
domenowym przed side effectem. Po restarcie adapter odpytuje venue/cache, uznaje
odebrane intencje i dopiero odtwarza te, których rzeczywiście brak.

**Dynamic import**
`importlib.import_module(name)` zamiast `import name` statycznie. W algo_bot: ładujemy strategię po nazwie z CLI (`--strategy bghtrend_pullback` → `importlib.import_module("algo_bot.strategies.bghtrend_pullback")`).

---

## E

**Editable install**
Instalacja pakietu tak, że zmiany w plikach `.py` w repo są widoczne
natychmiast. W aktualnym workflow wykonuje ją `uv sync --locked` (także przez
`make env`/`make install`/`make sync`); ręczne `pip install -e .` jest
historyczną, superseded instrukcją.

**EMA (Exponential Moving Average)**
Średnia ruchoma z eksponencjalnie malejącymi wagami starszych obserwacji. Bardziej responsywna niż SMA. W algo_bot: `algo_bot/indicators/core.py::ema()`.

**Engine**
Silnik wykonujący strategię. W algo_bot dwa: `algo_bot/engine/backtester.py` (backtest) i `live/live_binance.py` (live). Patrz [ADR-005](../adr/005-backtesting-py-mvp-engine.md).

**Expectancy**
Expected PnL per trade = (win_rate * avg_win) - (loss_rate * avg_loss). Pozytywne = profitowa strategia, negatywne = przegrywająca.

---

## F

**Fear & Greed Index**
Crypto sentiment index (0=extreme fear, 100=extreme greed). Używany w `dca_btc.py` jako filtr/scale: kupuj więcej gdy fear, mniej gdy greed.

**Flatten layout**
Ułożenie repo gdzie kod siedzi w roocie (`algo_bot/`), nie zagnieżdżony (`trading/backtesting/algo_bot/`). Patrz [ADR-001](../adr/001-flatten-package-layout.md).

**Fold (walk-forward)**
Pojedynczy cykl train→test w walk-forward. Strategia optymalizowana na train data, testowana na test data. WF z 5 foldami = 5 takich cykli.

**Funding rate**
Mechanizm w perp futures: pozycje long/short płacą sobie nawzajem co 8h żeby utrzymać cenę perp blisko spot. Negatywne funding = shorty płacą longom, pozytywne = longi płacą shortom. Wpływa na realne PnL pozycji.

---

## G

**Grid search**
Optymalizacja parametrów przez sprawdzenie WSZYSTKICH kombinacji z dyskretnej przestrzeni. Wolne ale eksploruje całość. W algo_bot: `algo_bot/engine/sweep.py mode=grid`. Vs **random search**.

---

## H

**hatchling**
Modern Python build backend (alternatywa do setuptools, poetry). Minimal config w pyproject.toml. Patrz [ADR-002](../adr/002-pyproject-hatchling-stack.md).

**HOLD (signal)**
Strategia decyduje *nic nie rób* — `Signal()` z `action=None`. W przeciwieństwie do `enter` lub `exit`.

**Hybrid TP/SL**
Tryb live tradingu: TP na serwerze giełdy, SL lokalnie monitorowany przez bota. Kompromis bezpieczeństwa vs odporności na knoty. Patrz [ADR-004](../adr/004-hybrid-tp-sl-mode.md).

---

## I

**Idempotency**
Operacja jest *idempotentna* jeśli wykonanie jej wielokrotnie daje ten sam efekt co jednokrotne. W live trading: każdy order ma `client_order_id` deterministycznie wyliczony, dzięki czemu restart bota nie tworzy duplikatów pozycji.

**In-sample / Out-of-sample (IS/OOS)**
**In-sample** = dane na których optymalizujemy parametry. **Out-of-sample** = dane na których testujemy (nigdy widziane podczas optymalizacji). Performance OOS to jedyny realny proxy na live performance. Patrz [walk-forward].

**Indicators (wskaźniki)**
Funkcje matematyczne na cenach (EMA, RSI, ATR, MACD, Bollinger, ...). W algo_bot: `algo_bot/indicators/` + zewnętrzne z TA-Lib.

---

## J

**Journal**
CSV log wszystkich akcji bota: trades.csv (entries/exits z PnL) + equity.csv (snapshoty equity w czasie). Per `run_id`. Implementacja: `algo_bot/telemetry/journal.py`.

---

## K

**Knot (świeca z knotem)**
Świeca z bardzo długim wick'iem (high lub low daleko od open/close). Często spowodowane thin orderbook lub spike'iem. Powód problemów backtest-live (patrz [Hybrid TP/SL]).

---

## L

**Latency**
Czas między zdarzeniem na giełdzie a reakcją bota. W algo_bot mierzony od momentu zamknięcia świecy do submitu ordera. Akceptowalna granica: < 500ms dla bar=5min.

**Leverage (dźwignia)**
Multiplier pozycji: 3x leverage = pozycja 3x większa niż margin. W algo_bot domyślnie 3x na live. Wyższy leverage = wyższy zysk/strata + większa szansa liquidation.

**Lint**
Sprawdzanie kodu pod kątem stylu i błędów *bez* uruchamiania. W algo_bot: ruff (zastępuje black+isort+flake8+pyupgrade).

**Liquidation (likwidacja)**
Pozycja wymuszenie zamknięta przez giełdę gdy margin spada poniżej maintenance margin. **Strata 100% pozycji.** Unikamy przez konserwatywne leverage + risk management.

**Live trading**
Bot działa na żywo na giełdzie z realnymi pieniędzmi (lub testnet). Vs backtest (historyczne).

**Lockfile**
Plik z dokładnymi wersjami bezpośrednich i przechodnich zależności. W algo_bot
kanonicznym lockfilem jest `uv.lock`, odtwarzany przez `uv sync --locked`.
`requirements.txt` jest tylko generowanym eksportem kompatybilności.

---

## M

**Mainnet / Testnet**
**Mainnet** = produkcyjna giełda z realnymi pieniędzmi. **Testnet** = testowa wersja z fake money (Binance Futures Testnet). Używamy testnet przed mainnetem.

**Make**
GNU make — system build/automation. W algo_bot wszystkie codzienne komendy zawinięte w Makefile (`make test`, `make check`, etc.). Patrz [makefile-cheatsheet.md](../guides/makefile-cheatsheet.md).

**Mark price**
"Sprawiedliwa" cena instrumentu na perpetual futures, agregowana z multiple giełd (index price) + funding adjustment. Bardziej stabilna niż **last price** (z thin orderbook). Używana do liquidation i ewentualnie do local TP/SL ([ADR-004](../adr/004-hybrid-tp-sl-mode.md)).

**MAR (Managed Account Ratio)**
Annualized return / max drawdown. Synonim Calmar ratio.

**MAR target (Sortino)**
Minimum Acceptable Return w obliczaniu Sortino ratio. Często ustawione na 0 (downside = każda strata) lub risk-free rate.

**`MastermindStateMachine`**
Czysty reducer MMS-inspired v2 bez importów NautilusTrader. Konsumuje typowane
eventy domenowe, emituje typowane intencje i utrzymuje niezależnie risk mode,
position build oraz order lifecycle. Wersjonowany snapshot zachowuje SCOUT,
virtual legs, PnL, dedupe i outbox przez restart.

**MVP (Minimum Viable Product)**
Pierwsza działająca wersja produktu z minimum funkcjonalności. W algo_bot: jedna
strategia musi przejść pełną ścieżkę → walk-forward → testnet → mainnet (mały
kapitał) → VPS 24/7. `bghtrend_pullback` jest już historycznym NO-GO, nie bieżącym
kandydatem.

**mypy**
Static type checker dla Pythona. W algo_bot polityka strict-on-new — pełny rygor tylko dla nowych modułów. Patrz [ADR-002](../adr/002-pyproject-hatchling-stack.md).

---

## N

**NautilusTrader**
Framework event-driven do backtestu i tradingu live, będący celem migracji
silnika. Beta 0 przypina stabilne wydanie 1.230.0 w `pyproject.toml` i
`uv.lock`; obecność zależności nie oznacza automatycznie, że każdy istniejący
runbook przeszedł już z legacy backtesting.py na nowy engine.

**NETTING / virtual legs**
Venue utrzymuje jedną netto pozycję na instrument, natomiast strategia prowadzi
logiczne nogi base/add-on we własnym ledgerze. W MMS v2 baza ma Close-All stop,
a każda faktyczna część dokładki osobne incremental `reduce_only` protection.

**Notebook**
Jupyter notebook (`.ipynb`) — interaktywny dokument code + markdown + plots. W algo_bot: `notebooks/` dla research, nie production code.

---

## O

**OHLCV (Open, High, Low, Close, Volume)**
Format świecy: ceny otwarcia/maksymalna/minimalna/zamknięcia + wolumen. Podstawowa jednostka dla większości strategii.

**Optimization (parametrów)**
Znalezienie najlepszych parametrów strategii dla danego objective (np. Sharpe). W algo_bot: `algo_bot/engine/sweep.py`. **WAŻNE**: optimization na in-sample data ≠ przewaga na out-of-sample. Patrz [overfitting] i [walk-forward].

**Out-of-sample (OOS)** — patrz **In-sample / Out-of-sample**.

**Overfitting**
Model/strategia perfekcyjnie pasuje do *training* data ale słabo generalizuje na *test* data. W tradingu: parametry "magicznie zarabiające" na backteście, tracące na live. Główna pułapka. Mitygowane przez walk-forward + monte carlo + parameter stability.

---

## P

**Paper trading**
Symulacja live tradingu (real-time data) ale BEZ wysyłania faktycznych orderów. Bot udaje że tradzi, journaluje "fake" trades. Faza między backtest a testnet.

**`pip-tools`**
Historyczne tooling dependency management (`pip-compile` + `pip-sync`). Od
Beta 0 ten workflow jest **superseded**: `make lock` wywołuje `uv lock`, a
`make sync` wywołuje `uv sync --locked`.

**Perpetual futures (perp)**
Kontrakt futures bez expiration date. Na crypto: dominująca forma tradingu z leverage. Wymaga funding rate dla utrzymania ceny blisko spot.

**Position sizing**
Decyzja ile capital'u allokować w pojedynczą pozycję. W algo_bot: `% equity per trade` (np. 2% = max loss per trade). Vs sztywne USDT (nie skaluje się z portfelem).

**Preregistration**
Zamrożenie przed eksperymentem: okna danych, wariantów, seedów, kosztów, metryk
i reguł interpretacji. P9 rezerwuje temporal holdout, którego nie wolno ładować,
hashować, uruchamiać ani raportować podczas Beta.

**Profit factor**
Total wins / total losses. > 1 = profitowa strategia. Threshold MVP: > 1.3 OOS.

**Pullback**
Tymczasowy spadek ceny w ramach uptrend'u (lub wzrost w downtrend'zie). W `bghtrend_pullback`: kupujemy/shortujemy POD/NAD trendem w okolicach EMA89 (czekamy na "wycofanie się" ceny).

**`pyproject.toml`**
Standard Python pliku konfiguracyjnego (PEP 621). Single source of truth dla build, deps, tooling. Patrz [ADR-002](../adr/002-pyproject-hatchling-stack.md).

**Pyramiding**
Dokładanie do istniejącej pozycji. MMS v2 dopuszcza maksymalnie jedną dokładkę
o target notional równym bazie; baza x1 + dokładka x1 daje cap x2. Zmiana build
state następuje dopiero po faktycznym fillu, nigdy na samym triggerze.

---

## Q

**Quant / Quantitative trading**
Trading oparty na statystyce, modelach matematycznych, automated decisions. algo_bot jest tym czym jest.

---

## R

**Random search**
Optymalizacja parametrów przez random sampling z przestrzeni. Szybsze niż grid dla wielowymiarowych przestrzeni. W algo_bot: `algo_bot/engine/sweep.py mode=random`.

**RBI (Research → Backtest → Implement)**
Metodologia rozwoju strategii: 1) **Research** — hipoteza ekonomiczna/techniczna, 2) **Backtest** — rygorystyczne testowanie, 3) **Implement** — paper → testnet → mainnet. algo_bot jest zbudowany wokół tego cyklu.

**Recovery time**
Czas od max drawdown do nowego peak'a equity. Mierzy "ile czekamy żeby wrócić do break-even".

**Risk management**
Wszystko co ogranicza maksymalną stratę: position sizing, max DD stop, daily loss limit, max concurrent positions. Faza 1 decyzja E.

**R:R (Risk:Reward, reward:risk)**
Stosunek wielkości targetu (take profit) do wielkości ryzyka (entry → stop loss). W `bghtrend_pullback`: `rr_target` (1.2–2.0), gdzie `risk = entry − SL` (SL zakotwiczony przy EMA89), a `TP = entry ± rr_target × risk`. Definiuje strukturę payoffu — wyższy R:R = niższy wymagany win rate dla profitowości.

**RSI (Relative Strength Index)**
Oscillator 0-100 mierzący momentum. > 70 = overbought, < 30 = oversold. W algo_bot: `algo_bot/indicators/core.py::rsi()`.

**Rolling walk-forward**
Wariant walk-forward gdzie oba okna (train, test) mają stały rozmiar i przesuwają się razem. Vs **anchored**.

**ruff**
Modern Python linter + formatter (Rust). Zastępuje black+isort+flake8+pyupgrade. W algo_bot: `make lint`, `make format`. Patrz [ADR-002](../adr/002-pyproject-hatchling-stack.md).

**`run_id`**
Unikatowy identyfikator pojedynczego backtest/sweep run. Format: `<TIMESTAMP_UTC>_<STRATEGY>_<SYMBOL>_<TIMEFRAME>_<PARAMS_HASH>`. Używany jako nazwa folderu w `results/`.

---

## S

**Sharpe Ratio**
(Mean return - risk-free rate) / std(returns). Risk-adjusted return. > 1 = dobre, > 2 = świetne. Threshold MVP: > 1.0 OOS.

**SCOUT / FULL**
Binarny risk mode MMS v2. FULL rezerwuje bazę x1 (i opcjonalnie jedną dokładkę
x1); pełny bazowy SL przełącza następny setup na base-only SCOUT x0.1. FULL wraca
dopiero po całkowitym, naturalnym i dodatnim po kosztach zamknięciu setupu SCOUT.

**Signal**
Output strategii w algo_bot — dataclass `algo_bot.strategy_base.Signal`. Zawiera: action ('enter'/'exit'/None), side ('long'/'short'/None), size, tp_pct, sl_pct, meta. Patrz [ADR-003](../adr/003-strategybase-signal-api.md).

**Slippage**
Różnica między oczekiwaną a faktyczną ceną wykonania ordera. Wyższy na thin markets. W algo_bot: `--slippage_bps` parameter dla backtestera (post-run adjustment).

**Sortino Ratio**
Jak Sharpe, ale tylko *downside* deviation w mianowniku (nie całe std). Lepiej penalizuje straty niż zyskowne odchylenia. Threshold zazwyczaj > 1.5.

**Spread**
Różnica między best bid a best ask. Wpływ na koszty wejścia/wyjścia. W algo_bot: `--spread_bps` parameter.

**`StrategyBase`**
Abstract base class dla wszystkich strategii w algo_bot. Definiuje API `on_bar(df) -> Signal`. Patrz [ADR-003](../adr/003-strategybase-signal-api.md).

**SQN (System Quality Number)**
Van Tharp's metric: sqrt(N) * mean(R) / std(R) gdzie R = R-multiples. < 1 = zła strategia, > 2 = average, > 3 = good, > 5 = excellent.

**Sweep**
Eksploracja przestrzeni parametrów (grid lub random). W algo_bot:
`uv run algo-sweep`. Output do `results/experiments/index.csv`.

---

## T

**T3 (Tillson T3)**
Wygładzona średnia ruchoma — kombinacja sześciu kolejnych EWM z "volume factor" `b` (typowo 0.6–0.8), która pozwala filtrowi "wyprzedzać" zamiast opóźniać. W algo_bot: `algo_bot/indicators/core.py::t3()`, używana w xtrenderze do wygładzenia short-term legu (`short_t3`). Wyższy `b` = bardziej responsywny + overshoot, niższy = gładszy + lag.

**TA-Lib**
Technical Analysis Library — C library z Python bindings i około 150
wskaźnikami. Beta 0 przypina TA-Lib 0.7.0; wheel instalowany z `uv.lock`
zawiera bibliotekę C, więc systemowy build i conda-forge nie są potrzebne.
Patrz [getting-started.md](../guides/getting-started.md).

**Take Profit (TP)**
Order zamykający pozycję na profit gdy cena dotrze do progu. W algo_bot per strategia (z meta lub paramów).

**Testnet** — patrz **Mainnet / Testnet**.

**Trail (trailing stop)**
SL który podąża za ceną — przy wzroście long position SL też się przesuwa w górę. W `bghtrend_pullback`: ATR-based trailing stop.

**Trend following**
Strategia próbująca jechać "z trendem" — kupować w uptrend, shortować w downtrend. Vs **mean reversion**.

**`tzdata`**
Python package z time zone data. Wymagana na Windowsie dla `zoneinfo` (stdlib). W algo_bot used w `live_binance.py` dla Europe/Warsaw timestamps.

---

## U

**`uv`**
Manager Pythona, środowiska i zależności rozwijany przez Astral. W Beta 0 uv
0.11.28 jest przypiętym, domyślnym narzędziem: czyta `.python-version`,
odtwarza `uv.lock` do `.venv` i uruchamia komendy przez `uv run`.

---

## V

**`vectorbt`**
Alternatywny silnik backtestowy do `backtesting.py`. 50-100x szybszy, multi-asset, ale większy learning curve. Kandydat na migrację po MVP. Patrz [ADR-005](../adr/005-backtesting-py-mvp-engine.md).

**VPS (Virtual Private Server)**
Maszyna w cloudzie (Digital Ocean, Hetzner, AWS) gdzie bot będzie chodził 24/7. Plan: faza 5.

---

## W

**Walk-forward analysis (WF)**
Rygorystyczny test out-of-sample: dzielimy historię na N foldów, każdy fold = train (optimize) + test (evaluate). Aggregujemy metryki tylko z test folds. Patrz [Rolling/Anchored WF]. Faza 1 decyzja F.

**Win rate**
% zwycięskich tradów. Mylące samo w sobie — strategia z 30% win rate może być zyskowna jeśli winners >> losers (R:R > 2.3).

**WSL (Windows Subsystem for Linux)**
Linux uruchomiony jako maszyna wirtualna w Windowsie. W algo_bot: zalecane środowisko dev dla użytkowników Windows.

---

## X

**xtrender**
Custom oscillator implementowany w `algo_bot/indicators/xtrender.py`. Wariant Bryan G. Howell'a. Używany w `bghtrend_pullback` jako momentum confirm.

---

## Y

(brak)

---

## Z

**Zoneinfo**
Python stdlib (3.9+) do time zones. W algo_bot używany w `live_binance.py` dla Europe/Warsaw. Wymaga `tzdata` na Windowsie.

---

## Symbole i konwencje

- **`bps` (basis points)**: 1 bps = 0.01% = 0.0001. `slippage_bps=5` = 5 bps slippage = 0.05%.
- **`USDT-M`**: USDT-Margined futures (collateral w USDT). Na Binance to `BTC/USDT:USDT`.
- **`/` w symbolu**: separator base/quote. `BTC/USDT` = base=BTC, quote=USDT.
- **Kolejność świec**: indeksowane chronologicznie ascending. `df.iloc[-1]` = ostatnia świeca (najbardziej recent).
- **`@dataclass` dla params**: konwencja w algo_bot — każda strategia ma `ParamSchema = SomeDataclass` z defaultami.
- **Polski w komentarzach + docstringach, angielski w nazwach publicznych**: konwencja językowa.

---

## Pokrewne sources

- [docs/ARCHITECTURE.md](../ARCHITECTURE.md) — wysokopoziomowa mapa
- [docs/adr/README.md](../adr/README.md) — decyzje architektoniczne
- [Investopedia](https://www.investopedia.com/) — definicje finansowe non-crypto
- [Binance Academy](https://academy.binance.com/) — crypto trading basics
