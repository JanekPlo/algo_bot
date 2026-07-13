# MMS-inspired v2 Beta mini-benchmark — prerejestracja

Status: **FROZEN BEFORE METRIC READ**  
Data zamrożenia: 2026-07-13  
Zakres: P9 MR-Session 3 Beta  
Klasa każdego wyniku: **SMOKE_ONLY / NOT_ELIGIBLE**

Ten dokument zamraża mały eksperyment inżynierski. Nie jest planem strojenia ani
testem rentowności. Kod, konfiguracje i manifest muszą zostać zapisane przed
pierwszym odczytem metryk strategii. Wynik nie może być użyty do wyboru wariantu do
produkcji.

## 1. Pytanie i warunek kontynuacji

Pytanie: czy jawna state machine sekwencyjności i jednej dokładki zmienia zachowanie
`bare core` w oczekiwany, mechaniczny sposób, bez naruszeń ekspozycji, kolejności
zdarzeń i kosztowego ledgeru?

Warunki technicznego zaliczenia:

- dokładnie 12 prerejestrowanych runów i żadnych dodatkowych prób po obejrzeniu
  metryk;
- `invariant_violation_count == 0` w każdym runie;
- deterministyczny rerun daje identyczny manifest i ledgery poza jawnymi polami
  czasu utworzenia;
- wszystkie runy kończą się `FLAT`, bez aktywnych zleceń ochronnych i bez
  nieuzgodnionych eventów fundingowych;
- każdy wynik pozostaje `SMOKE_ONLY / NOT_ELIGIBLE`, niezależnie od Sharpe/PnL.

Naruszenie któregokolwiek warunku przerywa P9. Nie wolno wtedy rankingować ani
interpretować wyników ekonomicznie.

## 2. Dane i nietknięty holdout

Instrument i częstotliwość:

- `BTCUSDT` perpetual, Binance, H1;
- OHLCV: `bot_data/processed/binance_BTCUSDT_1h.csv`;
- funding: `bot_data/processed/binance_BTCUSDT_funding.csv`.

Zamrożone przedziały UTC, prawostronnie otwarte:

| Rola | Przedział | Użycie w tej sesji |
|---|---|---|
| warm-up | `[2023-12-23 16:00, 2024-01-01 00:00)` | 200 H1 barów; wskaźniki, bez handlu |
| development mini-benchmark | `[2024-01-01 00:00, 2025-07-01 00:00)` | jedyny raportowany okres, 18 miesięcy |
| temporal holdout | `[2025-07-01 00:00, 2026-01-01 00:00)` | **nie ładować, nie hashować, nie uruchamiać i nie raportować** |

Runner ma filtrować wiersze po timestampie przed obliczeniem jakiejkolwiek metryki.
Integralność oraz hashe dotyczą wyłącznie warm-upu i development. Holdout pozostaje
zarezerwowany na przyszłą, osobno autoryzowaną sesję po zamrożeniu kandydata.

Na końcu development nowe wejścia są blokowane. Na przedostatnim barze wykonawczym
otwarty setup otrzymuje `CloseAll(MANUAL)`, aby w profilu next-close zamknąć się na
ostatnim pełnym barze przed `2025-07-01 00:00 UTC`. To wyjście nie zmienia risk mode.

## 3. Stałe konfiguracje

Seed każdego runu: `20260713`.

Wspólne parametry domenowe:

- BB `window=20`, `num_std=2.0`;
- `require_reclaim=false`;
- base SL `2%` od rzeczywistego fill VWAP;
- FULL `exposure_multiplier=1.0`, SCOUT `0.1`;
- dokładka ma target notional równy bazie i jest dozwolona tylko w FULL;
- najwyżej jedna dokładka; wick-pair stop dalej niż `1%` jest odrzucany, nie
  clampowany;
- Stochastic H1 `14/3/3`, progi `20/80`;
- quantity step i minimum `0.001 BTC`, price tick `0.1 USDT`;
- kapitał początkowy `10_000 USDT`.

Dwa i tylko dwa zestawy parametrów:

| ID | `arm_expiry_bars` | Pozostałe parametry |
|---|---:|---|
| `P20_E2_R0` | 2 | wspólne wartości powyżej |
| `P20_E1_R0` | 1 | wspólne wartości powyżej |

To porównanie wrażliwości, nie wybór najlepszego parametru. Żadnego losowego ani
grid sweepu nie wolno dołączyć do tego eksperymentu.

## 4. Macierz 12 runów

Każdy z dwóch zestawów parametrów uruchamia dokładnie sześć wariantów:

| ID | Sekwencyjność | Dokładka |
|---|---|---|
| `V1_BASE_ONLY` | off; zawsze FULL | off |
| `V2_BASE_SEQ` | on | off |
| `V3_BASE_CC` | off; zawsze FULL | `CONFIRMING_CANDLE` |
| `V4_BASE_STOCH` | off; zawsze FULL | `STOCH_CROSS` |
| `V5_BASE_SEQ_CC` | on | `CONFIRMING_CANDLE` |
| `V6_BASE_SEQ_STOCH` | on | `STOCH_CROSS` |

`FIRST_OF_CANDLE_OR_STOCH` i `CANDLE_AND_STOCH` pozostają zaimplementowanymi
ablation policies, lecz nie są uruchamiane w P9. Razem: `2 × 6 = 12` runów.

## 5. Execution, OMS i koszty

Zamrożone profile:

- timestamp map: `BINANCE_OPEN_TO_INCLUSIVE_CLOSE_V1`;
- wrapper execution: `PYO3_WRAPPER_NEXT_CLOSE_ZERO_LATENCY_SMOKE_V1`;
- OMS domenowy/live: `OMS-A_NETTING_VIRTUAL_LEGS_V1`;
- backtestowy base Close-All:
  `PYO3_NETTING_DECOMPOSED_CLOSEALL_SMOKE_V1`;
- `bar_adaptive_high_low_ordering=true`;
- natywny engine latency `0`; wrapper kolejkuje strategiczne market ordery do
  następnego H1 Close, a ochronę składa synchronicznie po fillu;
- deterministyczne pełne fille; bez losowego modelu partial fills.

PyO3 1.230.0 ignoruje Binance `params.close_position` w symulatorze. Dlatego base
Close-All jest w smoke rozłożony na dzieci `STOP_MARKET reduce_only=True`, po jednym
na każdą unikalną deltę fillu. Native engine ogranicza oversize reduce-only do
pozostałej pozycji, więc nie powstaje reversal. Ślad zleceń różni się jednak od live
Binance i nie dowodzi parity.

Koszty muszą być naliczane w natywnym ledgerze Nautilus:

- prowizja: stałe taker `0.0004 × abs(fill_notional)` przez native fee model;
- funding: historyczne `FundingRateUpdate` i native PyO3 settlement; wrapper drenuje
  unikalne `PositionAdjusted(FUNDING)` przed finalizacją setupu;
- slippage: native one-tick fill model, raportowany osobno jako przybliżenie;
- brak post-hoc ADR-011 overlay na ścieżce Nautilus.

Jeżeli native fee/funding/fill model lub wymagany event kosztowy nie jest dostępny,
run failuje zamiast podstawiać cichy fallback.

## 6. Powody bezwarunkowej dyskwalifikacji

Każdy run zapisuje co najmniej poniższe reason codes:

- `NO_MARK_PRICE_HISTORY` — funding korzysta z dostępnej przyczynowej ceny
  kontraktu, nie z historycznego mark price;
- `H1_INTRABAR_HEURISTIC` — kolejność ekstremów OHLC jest heurystyką adaptive;
- `NO_ORDER_BOOK_OR_TRADES` — nie ma empirycznego spreadu, impactu ani kolejek;
- `APPROX_ONE_TICK_SLIPPAGE`;
- `FIXED_FEE_SCHEDULE` — jedna zamrożona stawka nie odtwarza historycznych tierów;
- `BACKTEST_CLOSEALL_NOT_BINANCE_PARITY`;
- `H1_WICK_PAIR_PROXY` — brak źródłowego M5/M10;
- `NO_H4_D1_CONTEXT`.

Wynik pozostaje `NOT_ELIGIBLE` także wtedy, gdy wszystkie testy techniczne są
zielone.

## 7. Manifest i raportowane pola

Manifest eksperymentu zapisuje przed startem:

- wersje P3 timestamp map, P4 OMS, wrappera, strategii, snapshot schema i
  `BacktestResult` schema;
- Python, uv, NautilusTrader i TA-Lib versions;
- commit bazowy, dirty/source-tree hash i `uv.lock` hash;
- hashe dokładnie użytych development OHLCV/funding i wszystkich 12 configów;
- seed, zakresy, instrument, execution/OMS/cost profiles oraz pełną macierz runów.

Każdy `BacktestResult` zawiera stats, equity, trades, orders, fills, positions,
funding adjustments, invariant ledger oraz co najmniej:

- base reaction/entry intents, submissions i faktycznie rozpoczęte setupy;
- add-on trigger facts, intents, submissions, pierwsze fille, wszystkie fill deltas i
  rejections;
- add-on SL i pełne base SL;
- przejścia FULL→SCOUT, SCOUT setups i SCOUT→FULL;
- czas epizodów SCOUT i flagę right-censored;
- maksymalny committed target i gross realized exposure w quote oraz multiplier;
- gross price PnL, commissions, funding paid/received/net, slippage i setup net PnL;
- końcowe equity/return, opisowy Sharpe, max drawdown i turnover;
- invariant violations, oczekiwane zero.

`ablation.csv` liczy różnicę każdego wariantu względem `V1_BASE_ONLY` w ramach tego
samego zestawu parametrów. Dodatkowo zapisuje zamrożone kontrasty:

- `V2 − V1`, `V3 − V1`, `V4 − V1`;
- `(V5 − V2) − (V3 − V1)`;
- `(V6 − V2) − (V4 − V1)`.

Są to statystyki opisowe mechaniki. Nie wykonuje się testów istotności, optymalizacji,
rankingu ani wnioskowania o przyszłej rentowności.

## 8. Reguła unieważnienia

Zmiana parametrów, wariantów, dat, execution policy, OMS, kosztów, liczby runów albo
definicji metryk po pierwszym odczycie metryk strategii unieważnia protokół. Naprawa
błędu wykonawczego jest dopuszczalna wyłącznie po usunięciu wszystkich wyników z
wadliwego runu, udokumentowaniu przyczyny i ponownym zamrożeniu całego manifestu;
nie wolno przy tym oglądać holdoutu.
