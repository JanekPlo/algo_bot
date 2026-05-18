# ADR-004: Hybrid TP/SL — tryby `server` / `local` / `hybrid` w live

- **Status:** Accepted (retroactive)
- **Data:** pre-2026-05 (zapisane retroactive 2026-05-14)
- **Faza projektu:** 0 (legacy)
- **Autorzy:** Janek Płoński (legacy), Janek + Claude (retroactive write-up)

## Context

W live tradingu na Binance Futures pozycja musi mieć **Take Profit** (zamknij gdy zarobiłeś X%) i **Stop Loss** (zamknij gdy stracisz Y%). Standardowe rozwiązanie: po otwarciu pozycji wysyłamy do giełdy zlecenia TP i SL, giełda je trigeruje gdy cena dotrze do progu. To **server-side TP/SL**.

W praktyce na Binance (zwłaszcza na **testnecie**, ale też okazjonalnie na **mainnecie**) zdarzają się **"knoty"** — pojedyncze świece z ekstremalnymi cenami (High/Low daleko od Open/Close), spowodowane:
- Cienka książka zleceń na testnecie (limit orders przy ekstremalnych cenach)
- Mainnet: chwilowe flash crashes / spikes na thin orderbook
- Liquidation cascade

Server-side TP/SL trigerują się na tych knotach, zamykając pozycję na ekstremalnej (niekorzystnej) cenie. W backteście tego nie ma (bo świece historyczne są już "wygładzone" przez agregację) — więc backtest pokazuje profit, live pokazuje loss. Klasyczne backtest-live mismatch z konkretnej przyczyny.

Alternatywa: zamykamy pozycje **lokalnie** — bot monitoruje cenę (np. mark price z mainnetu) i sam wysyła `position.close()` gdy próg osiągnięty. To omija knoty bo polega na ciągłym mark price, nie pojedynczych ticks orderbooka.

Ale local-only ma swoje problemy:
- Jeśli bot się zawiesi / VPS padnie — pozycja zostaje open bez SL (catastrophic)
- Latencja: bot polluje co N sekund, market może wyjść z safe range w międzyczasie
- Jeśli WebSocket padnie i bot nie zauważy — to samo

**Kompromis**: tryb **hybrid** — TP na serwerze (większe pole na knoty, mniejsze ryzyko brak SL), SL lokalnie (kluczowe dla risk management). Lub odwrotnie zależnie od strategii.

## Decision

Live trading w `live/live_binance.py` wspiera 3 tryby (CLI flag `--tpsl_mode`):

### Tryb `server`

- Po `enter` wysyłamy na Binance: market entry + TP (limit order reduce-only) + SL (stop-market reduce-only)
- Bot nie monitoruje cen między barami
- Pozycja zamyka się gdy giełda wytriguje TP lub SL
- **Plusy**: niezawodne (giełda działa nawet gdy bot/VPS padnie), niska latencja
- **Minusy**: knoty na testnecie/thin markets mogą trigerować fałszywie

### Tryb `local`

- Po `enter` wysyłamy tylko market entry (BEZ server-side TP/SL)
- Bot ma `price_feed` skonfigurowany (`mainnet_mark`, `mainnet_last`, `testnet_mark`, `testnet_last`) — czyta cenę regularnie
- Sam wysyła `position.close()` gdy cena przekracza próg TP lub SL
- **Plusy**: omija knoty (używa mark price), pełna kontrola
- **Minusy**: jeśli bot się zawiesi/VPS padnie/WS rozłączy — pozycja open bez exit (KATASTROFA)

### Tryb `hybrid` (domyślny)

- Po `enter` wysyłamy: market entry + TP server-side (limit order) — bez SL
- Bot lokalnie monitoruje SL (z `price_feed`) i wysyła `close()` gdy próg osiągnięty
- **Plusy**: TP jest niezawodne (na giełdzie), SL omija knoty (lokalnie z mark price)
- **Minusy**: nadal "jeśli bot padnie" = SL nie zadziała, ale przynajmniej TP może zamknąć profit

### `price_feed` parametr

Dla `local` i `hybrid` decydujemy które źródło ceny używamy do lokalnego monitoringu SL:
- `mainnet_mark` — mark price z mainnetu (NAJSTABILNIEJSZE — używa indeksu z multiplie giełd)
- `mainnet_last` — last trade price z mainnetu
- `testnet_mark` — mark price z testnetu (mniej stabilny)
- `testnet_last` — last trade z testnetu (najgorszy — knoty)

Najczęstsza praktyczna kombinacja: **`hybrid` + `mainnet_mark`** — TP server-side, SL local na mainnet mark, omija knoty testnetu w pełni.

## Consequences

**Pozytywne:**
- Możemy testować strategię na testnecie bez fałszywych zamknięć z knotów (`hybrid` + `mainnet_mark`)
- Mainnet z dużą płynnością + `hybrid` lub `server` daje natural protection
- Backtest-live mismatch zmniejszony — knoty już nie psują live PnL
- Risk management bardziej elastyczny — możemy decydować per strategia która część jest local/server
- Recovery z journala: jeśli bot wstanie po crashu, czyta otwarte pozycje z giełdy (server-side TP może być widoczne), restartuje local SL monitoring

**Negatywne / koszty:**
- 3 tryby = 3 ścieżki kodu w `live_binance.py` (więcej powierzchni do testowania)
- Konfiguracja per-run (`--tpsl_mode`) zamiast głębokiej decyzji projektowej — może być źle ustawione przez pomyłkę
- Local SL nie jest niezawodny — kluczowa odpowiedzialność że VPS chodzi (faza 5 to adresuje przez monitoring + healthcheck)
- Pomiędzy świecami: jeśli używamy `local` SL i strategia HOLD-uje (nie wysyła nowego Signal między barami), tylko local price monitoring decyduje. Wymaga że poll loop chodzi co sekundę/parę-sekund, nie co bar (5min-4h)

**Ryzyka:**
- Local SL z `mainnet_mark` na testnecie tradingu = sprawdzamy logikę na mainnet cenach. Może być rozjazd z faktycznymi cenami testnet (pozycje na różnych poziomach). To OK na testnet pre-flight, ale wymaga jasnego myślenia.
- Hybrid TP server + local SL: jeśli local SL trigeruje, musimy też **anulować** server-side TP (inaczej zostanie wisieć i przy następnym entry zostanie wykonane jako duplikat). Logika "cleanup po close" musi być pewna.
- Jeśli bot ma bug w local SL logic i pozwoli pozycji rosnąć (-50%) bez zamknięcia — to katastrofa, której server-side SL by zapobiegł. Stąd hybrid jest bezpieczniejszy niż local pure.

## Alternatives Considered

- **Tylko server-side TP/SL** — proste, niezawodne, ale knoty na testnecie czynią development bolesny. Odrzucone bo: testnet development jest częścią pipeline'u przed mainnetem, knoty = false negatives w PnL.

- **Tylko local TP/SL** — pełna kontrola, ale brak fallback gdy bot padnie. Odrzucone bo: za duże ryzyko dla MVP gdzie nie mamy jeszcze production-grade monitoring (faza 5).

- **Conditional orders Binance** (np. trailing stop server-side z dynamic trigger) — Binance ma ograniczone funkcje conditional, nie wszystko co możemy lokalnie. Odrzucone bo: limituje flexibility logiki strategii.

- **Tryb adaptacyjny** (auto-switch między server i local zależnie od market conditions) — over-engineering dla MVP. Odrzucone, ale może być przyszły kierunek (faza 4+).

## References

- Plik: `live/live_binance.py` (401 linie, cały plik to implementacja)
- CLI args: `--tpsl_mode` (`server` | `local` | `hybrid`), `--price_feed` (`mainnet_mark` | ...)
- Default: `--tpsl_mode local --price_feed mainnet_mark` (testnet-safe)
- Powiązane: ADR-003 (StrategyBase Signal — `meta['sl']`, `meta['tp']` mogą być absolutne ceny zamiast pct)

## Notes

- W fazie 3 (paper/testnet MVP) zrobimy systematyczne porównanie sygnałów backtest vs live na tych samych barach z konkretnym strategy + tpsl_mode kombinacją. Cel: 100% zgodności entry/exit timing.
- W fazie 5 (VPS production) dodajemy reconciliation: porównanie equity z giełdy vs equity z journala raz dziennie. Każda rozbieżność > X% → alert. To dodatkowy safety net.
