# MMS v2 Beta — wyniki development-only P9

> **Status:** zakończony 2026-07-13<br>
> **Decyzja:** **ITERATE BETA**<br>
> **Klasa wyników:** `SMOKE_ONLY / NOT_ELIGIBLE`<br>
> **Interpretacja:** opis mechaniki; bez rankingu, doboru wariantu ani wniosku o edge

## Zakres i integralność eksperymentu

P9 wykonał dokładnie zamrożoną macierz `2 × 6 = 12` runów dla
`BTCUSDT-PERP.BINANCE`, H1, na oknie development
`2024-01-01T00:00:00Z`–`2025-07-01T00:00:00Z` z 200 barami warmup.
Holdout `2025-07-01`–`2026-01-01` nie został odczytany (`rows_read = 0`).

- `P20_E1_R0`: BB(20, 2), `arm_expiry_bars=1`, bez reclaim;
- `P20_E2_R0`: BB(20, 2), `arm_expiry_bars=2`, bez reclaim;
- `V1`: base only;
- `V2`: base + sequential leverage;
- `V3`: base + confirming-candle add-on;
- `V4`: base + Stochastic-cross add-on;
- `V5`: sequential leverage + confirming-candle add-on;
- `V6`: sequential leverage + Stochastic-cross add-on.

Każdy run przeszedł 22 kontrole końcowe: zero naruszeń domeny, pozycja i
zlecenia płaskie/zamknięte, pusty outbox, poprawny cutoff, pojedyncze settlementy
fundingu, zgodność ksiąg funding/slippage, zgodność liczby obserwowanych tranzycji,
round-trip snapshotu oraz brak danych holdout. Łącznie: **12/12 ukończonych runów,
264/264 zaliczonych kontroli**. `ablation.csv` zawiera zamrożone 22 wiersze:
12 różnic wariant-minus-base oraz 10 kontrastów.

Identyfikatory dowodu:

- manifest core SHA-256: `8b8ebb29f2e627d052fc281a8ff65d7e147cae00e98f5351f680fbbf5a12a029`;
- prerejestracja SHA-256: `3aa53985e6093521223bdac80747837506ce55aeefc90934c6a82cc498f70c26`;
- `uv.lock` SHA-256: `6020bd7ed209fe8f50ef844e110900605de45aefbda5fe54b1ddd01212bba4eb`;
- ablation SHA-256: `9a11bfcfdbfc063d605d62bcfbe11d1080bddf2a2f45c094273a722b304a27ef`;
- data SHA-256: `3f7f1aa135e9aeb3fc95e1eabe9a1379093335e4db132173b90466adeffbf67e`.

Artefakty wykonawcze są lokalne i gitignored:
`results/experiments/mms-v2-beta-p9-20260713-r6/`.

## Wyniki opisowe

Kapitał początkowy każdego runu wynosił 10 000 USDT. `Net PnL` jest
`setup_net_pnl` z natywnej księgi ceny, prowizji, fundingu i slippage. Sharpe jest
wyłącznie opisowym Sharpe H1, a nie statystyką kwalifikującą.

| Parametry | Wariant | Setupy | Add-on trigger / fill / reject / SL | FULL→SCOUT→FULL | Max eksp. | Net PnL | Equity | Return | Sharpe H1 | Max DD | Turnover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| E1 | V1 | 630 | 0 / 0 / 0 / 0 | 0 / 0 | 1.041× | -3629.68 | 6372.85 | -36.27% | -0.699 | -41.13% | 965.97× |
| E1 | V2 | 630 | 0 / 0 / 0 / 0 | 122 / 122 | 1.020× | -2047.02 | 7956.16 | -20.44% | -0.466 | -29.30% | 711.37× |
| E1 | V3 | 630 | 0 / 0 / 0 / 0 | 0 / 0 | 1.041× | -3629.68 | 6372.85 | -36.27% | -0.699 | -41.13% | 965.97× |
| E1 | V4 | 631 | 160 / 135 / 19 / 110 | 0 / 0 | 2.025× | -4961.12 | 5040.90 | -49.59% | -1.087 | -52.49% | 1049.93× |
| E1 | V5 | 630 | 0 / 0 / 0 / 0 | 122 / 122 | 1.020× | -2047.02 | 7956.16 | -20.44% | -0.466 | -29.30% | 711.37× |
| E1 | V6 | 630 | 173 / 97 / 8 / 79 | 121 / 121 | 2.014× | -3121.08 | 6881.67 | -31.18% | -0.784 | -35.53% | 826.02× |
| E2 | V1 | 675 | 0 / 0 / 0 / 0 | 0 / 0 | 1.022× | -2314.77 | 7688.28 | -23.12% | -0.298 | -36.81% | 1101.03× |
| E2 | V2 | 675 | 0 / 0 / 0 / 0 | 135 / 135 | 1.015× | -1835.15 | 8168.12 | -18.32% | -0.358 | -34.96% | 783.85× |
| E2 | V3 | 675 | 0 / 0 / 0 / 0 | 0 / 0 | 1.022× | -2314.77 | 7688.28 | -23.12% | -0.298 | -36.81% | 1101.03× |
| E2 | V4 | 675 | 175 / 142 / 24 / 118 | 0 / 0 | 2.027× | -4098.27 | 5904.09 | -40.96% | -0.723 | -46.60% | 1207.39× |
| E2 | V5 | 675 | 0 / 0 / 0 / 0 | 135 / 135 | 1.015× | -1835.15 | 8168.12 | -18.32% | -0.358 | -34.96% | 783.85× |
| E2 | V6 | 674 | 190 / 98 / 11 / 77 | 134 / 134 | 2.010× | -2551.61 | 7451.35 | -25.49% | -0.529 | -36.96% | 923.26× |

Mechanicznie zaobserwowano:

- `V3 == V1` i `V5 == V2`: confirming-candle add-on nie został wyzwolony na tym
  profilu danych; to ważny wynik diagnostyczny, nie przesłanka do wyboru wariantu.
- Sekwencja (`V2 − V1`) ograniczyła turnover i stratę w obu zestawach parametrów,
  ale nie osiągnęła dodatniego wyniku.
- Stochastic add-on (`V4`) faktycznie uruchamiał pyramiding i dochodził do około
  2× ekspozycji; samodzielnie pogarszał opisowy wynik względem base.
- Połączenie sekwencji i Stochastic (`V6`) zmieniło zarówno liczbę add-onów, jak i
  wynik względem addytywnej sumy efektów. To potwierdza działanie interakcji automatu,
  nie jej rentowność.
- Wszystkie 12 wyników było ujemnych. Ze względu na z góry zadeklarowaną
  niekwalifikowalność nie wolno na tej podstawie rankować wariantów ani wybierać
  parametrów do holdoutu.

## Dlaczego decyzja brzmi `ITERATE BETA`

Beta potwierdziła działanie kontraktów timestamp/execution, modelu NETTING z
wirtualnymi nogami, czystego automatu stanów, idempotencji/recovery, cienkiego
wrappera PyO3 oraz audytowalnych artefaktów. Nie ma podstaw do bailoutu: warstwa
techniczna działa, a invarianty są zielone.

Nie ma też podstaw do uruchomienia MR-Session 4. Każdy run był bezwarunkowo
`NOT_ELIGIBLE` z następujących powodów:

- brak historii mark price;
- heurystyka intrabar H1 i proxy wick-pair;
- brak order book/trades;
- przybliżony one-tick slippage i stały fee schedule;
- PyO3 Close-All bez parytetu z Binance;
- brak kontekstu H4/D1.

Przed pełnym sweepem trzeba więc: rozwiązać lub formalnie zastąpić parytet
Close-All, dostarczyć mark-price i wiarygodniejszy profil fill/cost, zdecydować o
fidelity M5/M10 oraz zamrozić jednoznaczny zakres Session 4. Holdout pozostaje
  zamknięty do czasu nowej prerejestracji kwalifikowalnego eksperymentu.
