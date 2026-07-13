# Architektura — algo_bot

> Stan: 2026-07-13, MR-Session 3 Beta. Ten dokument opisuje bieżący kod.
> Beta służy do weryfikacji semantyki i powtarzalnego benchmarku; nie jest
> zgodą na handel testnet ani mainnet.

## Pryncypia

1. **Najpierw zdarzenia, potem skutki uboczne.** Logika Mastermind jest czystą,
   deterministyczną maszyną stanów. Adapter wykonawczy tłumaczy jej intencje na
   komendy silnika.
2. **Jedna semantyka czasu.** Dane CCXT opisują czas otwarcia świecy, a domena
   dostaje inkluzywny czas zamknięcia. Decyzja na zamknięciu nie może wypełnić
   zlecenia przed tym zamknięciem.
3. **NETTING na venue, wirtualne nogi w domenie.** Binance utrzymuje jedną
   pozycję netto. Domena rozlicza oddzielnie bazę i pojedynczy addon oraz nie
   polega na natywnym hedgingu venue.
4. **Idempotencja jest częścią modelu.** Identyfikatory zdarzeń, zleceń i
   egzekucji są deterministyczne. Outbox, high-watermarki i snapshoty muszą
   przeżyć restart bez ponownego zwiększenia ekspozycji.
5. **Fail closed.** Nieznany fill, niezgodność pozycji, brak ochrony albo
   niepełne koszty kończą się uzgodnieniem lub bezpiecznym zamknięciem, a nie
   domysłem.
6. **Pełny koszt i pochodzenie wyniku.** Wynik benchmarku musi obejmować fee,
   funding i poślizg oraz wskazywać dokładny kod, konfigurację, dane i runtime.
7. **Preregistracja przed wynikiem.** Okna, warianty, seed i kryteria są
   zamrożone przed uruchomieniem macierzy. Holdout nie jest ładowany w sesji
   Beta.

## Warstwy i granice odpowiedzialności

```text
CCXT OHLCV/funding
        |
        v
  mms_beta_data.py  ---------------------->  manifest wejścia
        |
        v
  nautilus_poc.py / Nautilus BacktestEngine
        |
        v
  nautilus_mastermind.py   <---- checkpoint transportu/restart
        |
        v
  mastermind/state_machine.py  <---- checksummed snapshot domeny
        |
        +---- intents ----> OMS/adapter ----> orders/fills/cancel
        ^                                      |
        |--------------------------------------+
        |
        v
  backtest_result.py  ---->  mms_beta_benchmark.py  ----> wynik Beta
```

Granica domeny Mastermind nie importuje NautilusTrader, pandas ani NumPy.
Obiekty specyficzne dla silnika kończą się w adapterach. Dzięki temu reducer
można testować sekwencjami zdarzeń bez zegara, sieci i giełdy.

### Dane i czas

- `algo_bot/fetch_data.py`, `data_loader.py`, `process_data.py` obsługują
  istniejący pipeline CCXT/CSV.
- `algo_bot/engine/mms_beta_data.py` buduje ściśle walidowany bundle Beta z
  OHLCV, natywnego funding i cech. Odrzuca duplikaty, luki kontraktu,
  nie-UTC, wyjście poza okno i dane holdout.
- `algo_bot/engine/nautilus_poc.py` jest wykonawczym oracle czasu i filli dla
  migracji. Konwersja CCXT jest jawna:
  `close_ns = (open_ms + interval_ms - 1) * 1_000_000`.
- Gap-stop jest wykonywany po cenie otwarcia luki. Kolejność intrabar przy
  jednoczesnym TP/SL jest parametrem modelu, nie przypadkiem implementacji.

### Domena Mastermind

Kod znajduje się w `algo_bot/strategies/mastermind/`:

| Moduł | Odpowiedzialność |
|---|---|
| `model.py` | typowane zdarzenia, intencje, stan, enumy i inwarianty |
| `signals.py` | czyste sygnały MMS i warunki addonu |
| `state_machine.py` | reducer zdarzenie -> nowy stan + outbox intencji |
| `snapshot.py` | kanoniczna serializacja, checksum i migracja snapshotu |

Maszyna rozróżnia nogę bazową i najwyżej jeden addon. Ekspozycja liczona jest
z filli, a nie z samych submitów. Zamknięcie `CloseAll` używa znaku rzeczywistej
pozycji po uzgodnieniu. Przejście limitów ryzyka następuje dopiero po płaskiej
pozycji i kompletnym przypisaniu kosztów zamknięcia.

Outbox reprezentuje intencje, które nie zostały jeszcze trwale potwierdzone.
Samo wyemitowanie komendy nie oznacza ACK. Snapshot obejmuje aktywny setup,
egzekucje i pozostałe ilości, ochronę, zamykanie, koszty, deduplikację oraz
outbox potrzebny do bezpiecznego replayu.

### Adapter i OMS

| Moduł | Rola |
|---|---|
| `nautilus_oms_poc.py` | sprawdzenie realnych ograniczeń OMS i Binance |
| `nautilus_adapter.py` | zamrożony adapter Tier 1 dla porównania silników |
| `nautilus_mastermind.py` | backend Mastermind dla profilu Cython/PyO3 |

Kontrakt OMS Beta to `NETTING` z wirtualnymi nogami. Ochrona bazy może zamknąć
całość, natomiast ochrona addonu redukuje dokładną ilość addonu. Natywne
brackety, contingent listy i amend nie są zakładane, jeśli adapter Binance nie
potwierdza ich obsługi.

Backend Cython służy jako semantyczny oracle małych scenariuszy. Backend PyO3
jest decomposed: mapuje osobne intencje na zlecenia venue i utrzymuje własny,
checksummowany checkpoint transportowy. Restart ma kolejność:

1. odtworzenie i walidacja snapshotu domeny oraz checkpointu adaptera,
2. odczyt cache pozycji i otwartych zleceń,
3. uzgodnienie różnic oraz anulowanie sierot,
4. replay wyłącznie intencji, których brak na venue,
5. wznowienie nowych barów i zdarzeń wykonawczych.

Brak checkpointu transportowego przy aktywnej ekspozycji jest błędem
fail-closed; nie wolno rekonstruować kierunku lub zakresu ochrony z domysłów.

### Wynik i benchmark

- `algo_bot/engine/backtest_result.py` definiuje wspólny artefakt wyniku z
  ledgerami filli, fee, funding i poślizgu oraz metadanymi pochodzenia.
- `algo_bot/engine/mms_beta_benchmark.py` uruchamia prerejestrowaną macierz
  `2 zbiory expiry x 6 wariantów`, weryfikuje manifest przed startem i zapisuje
  wyłącznie wyniki z kompletnym ledgerem i spełnionymi inwariantami.
- `docs/experiments/mms-v2-beta-preregistration.md` jest zamrożonym kontraktem
  eksperymentu. Zmiana kodu, configu, danych albo lockfile po zamrożeniu
  unieważnia manifest.

Wariant `smoke` zawsze pozostaje niekwalifikowany. Ablacje są opisowe i nie
służą do wyboru zwycięzcy po obejrzeniu wyników. Dwie historyczne strategie —
`bghtrend_pullback` i `mean_reversion_bb_stoch` — pozostają baseline'ami NO-GO,
a nie aktualnym kandydatem live.

## Pozostały stos badawczy

Repo zachowuje wcześniejszą, działającą ścieżkę `backtesting.py`:

| Obszar | Pliki |
|---|---|
| strategie | `algo_bot/strategies/*.py`, `strategy_base.py` |
| wskaźniki | `algo_bot/indicators/` |
| backtest i sweep | `engine/backtester.py`, `engine/sweep.py` |
| walk-forward | `engine/walkforward.py` |
| risk i metryki | `risk/limits.py`, `metrics.py`, `microstructure.py` |
| journal | `telemetry/journal.py` |

Ta ścieżka jest utrzymywana dla regresji i porównań. Nie wyznacza semantyki
egzekucji Mastermind; w razie rozbieżności rozstrzygają specyfikacja wykonywalna
i scenariusze Nautilus.

## Konfiguracja, runtime i jakość

- Python jest przypięty w `.python-version`; zależności i narzędzia w
  `pyproject.toml` oraz `uv.lock`.
- `requirements.txt` jest eksportem lockfile, nie niezależnym źródłem wersji.
- `config/mr_b1.yaml`–`mr_b3.yaml` podlegają walidacji przestrzeni parametrów.
- `make check` oraz CI uruchamiają Ruff, mypy i pytest w środowisku z
  `uv sync --locked`.
- Testy obejmują czystą domenę, snapshot/restart, OMS, oba adaptery,
  przetwarzanie danych, schemat wyniku i runner prerejestracji.

## Inwarianty przekrojowe

1. Pozycja netto venue musi odpowiadać sumie pozostałych ilości filli domeny.
2. Łączna zaangażowana ekspozycja obejmuje fill oraz żywy remainder zlecenia;
   nie może przekroczyć limitu wariantu.
3. Zlecenie terminalne nie wraca do stanu submitted/accepted.
4. Callback starego setupu nie może zmienić bieżącego setupu.
5. To samo zdarzenie wykonawcze lub funding nie może zostać zaksięgowane drugi
   raz po restarcie.
6. Po finalnym uzgodnieniu nie może zostać niepotwierdzona intencja w outboxie,
   otwarta pozycja ani osierocone zlecenie.
7. Każdy wynik finansowy musi spełniać równanie PnL na podstawie ledgerów; brak
   kosztu oznacza wynik nieważny.
8. Benchmark nie odczytuje ani nie raportuje holdout 2025-07-01–2026-01-01.

## Status i granice Beta

MR-Session 3 dowodzi mechaniki tylko wtedy, gdy przejdą kolejno bramki:
runtime, semantyka czasu, OMS, parytet Tier 1, reducer/restart, backend PyO3,
artefakt wyniku i zamrożony benchmark. Awaria wcześniejszej bramki blokuje
metryki zależne; nie wolno zastąpić brakującego dowodu syntetycznym sukcesem.

Poza zakresem Beta pozostają m.in. parytet produkcyjnego `CloseAll`, pełne
mark-price/fee semantics dla live, H4/D1 oraz sześciosymbolowy rollout. Z tego
powodu pozytywny wynik benchmarku nie jest sam w sobie decyzją o rozpoczęciu
Session 4 ani wdrożeniu kapitału.

Dokumentami normatywnymi są:

- `docs/specs/mms-v2-executable-spec.md`,
- `docs/experiments/mms-v2-beta-preregistration.md`,
- `docs/adr/014-engine-migration-nautilus.md`,
- `docs/ROADMAP.md`.
