# ADR-006: Logging — stdlib logging z JSON file handler

- **Status:** Accepted
- **Data:** 2026-05-21
- **Faza projektu:** 1 (Foundation)
- **Autorzy:** Janek Płoński, Claude

## Context

algo_bot do tej pory nie ma spójnej warstwy logowania. Wszystkie moduły używają `print(...)` z ręcznym timestampowaniem i `flush=True`. Audyt repo (`grep -r "print("` w `*.py`) pokazuje 76 wywołań w 14 plikach — top heavyweighty: `live/live_binance.py` (27 wywołań z prefiksem `[{ts()}]` i mieszanką poziomów WARN/ERR/INFO), `algo_bot/executor.py` (19, ale plik ma FIXME na broken `optimize_backtest` — retrofit poczeka), `algo_bot/fetch_data.py` (6), `algo_bot/process_data.py` (5), `algo_bot/engine/sweep.py` (3), `algo_bot/engine/exchanges/binance_adapter.py` (2), `algo_bot/engine/backtester.py` (1).

Konsekwencje stanu obecnego:
- **Brak poziomów** — wszystko jest `print`, dystynkcja WARN vs ERR vs INFO żyje tylko w treści stringa
- **Brak filtering** — nie można w VPS produkcji wyciszyć DEBUG zostawiając WARN+
- **Brak machine-readable formatu** — Faza 5 chce strukturalne logi do `/var/log/algo_bot/` z opcjonalnym sinkiem do Loki/Promtail. Z `print` nie da się ich sparsować.
- **Brak rotacji** — long-running live trading zapełni dysk
- **Brak handler'ów dla third-party** — `ccxt` i `backtesting.py` używają stdlib `logging`. Ich logi (np. retry, throttling, API errors) lecą w pustkę bo nikt nie ma skonfigurowanego root loggera
- **Brak `caplog` w testach** — nie da się weryfikować że krytyczne callsite'y (recovery, TPSL filled, błędy adaptera) faktycznie logują

ROADMAP wymienia Decyzję C jako prerequisite Fazy 1: *"Logging framework + setup (`algo_bot/log.py`) zamiast `print` w całym kodzie"*. Faza 5 wymienia *"Strukturalne logi → `/var/log/algo_bot/` z rotacją, opcjonalnie sink do Loki"* — moduł logging muszą być skonstruowany tak, żeby ścieżka do Loki/Sentry/OpenTelemetry była dostępna bez refaktoru.

Kontekst observability w repo: `algo_bot/telemetry/journal.py` (CSV) jest **osobną warstwą** — event store dla trades + equity snapshots z czasem (per `run_id`). To nie jest "log" w sensie diagnostycznym. Journal i logger pozostaną dwoma osobnymi warstwami — journal trzyma "co się stało w tradingu", logger trzyma "co się stało w systemie".

## Decision

**Używamy stdlib `logging` z konfiguracją w `algo_bot/log.py`.** Konkretnie:

1. **Moduł `algo_bot/log.py`** — single point of configuration z funkcją `setup_logging(level=..., log_dir=..., run_id=...)` i helperem `get_logger(name) -> Logger`. Wywołanie idempotentne (re-init nie duplikuje handlerów).

2. **Dwa handlery:**
   - **`StreamHandler`** (stderr) — plain format dla człowieka: `2026-05-21 14:23:01 [INFO] algo_bot.engine.backtester: Wyniki zapisane w results/backtests/...`
   - **`RotatingFileHandler`** — JSON format dla machine-readable: `{"ts": "...", "level": "INFO", "logger": "algo_bot.engine.backtester", "message": "...", "run_id": "...", "extra_field": "..."}`. Plik: `logs/algo_bot.log` (configurable), rotacja 10 MB × 5 backupów.

3. **JSON format = własny `JsonFormatter`** (~30 linii) bez zewnętrznych deps. Zgodne z polityką stdlib-first (ADR-002, mindset stdlib-first). `python-json-logger` rozważone i odrzucone — nasz format jest na tyle prosty że własna implementacja nie generuje długu.

4. **Per-module logger:** każdy plik konsumujący logger pisze:
   ```python
   from algo_bot.log import get_logger
   logger = get_logger(__name__)
   ```
   Konwencja `getLogger(__name__)` daje hierarchię modułów (`algo_bot.engine.backtester` < `algo_bot.engine` < `algo_bot` < root) i pozwala konfigurować level per subpakiet (np. `logging.getLogger("ccxt").setLevel(WARNING)` żeby wyciszyć szum).

5. **Kontekst (run_id, strategy, symbol)** — przekazujemy przez `extra={...}` przy wywołaniu albo przez `LoggerAdapter` jako per-run wrapper:
   ```python
   logger.info("Position opened", extra={"side": "long", "qty": 0.1, "run_id": rid})
   # albo:
   ctx = logging.LoggerAdapter(logger, {"run_id": rid, "strategy": "bghtrend_pullback"})
   ctx.info("Position opened", extra={"side": "long", "qty": 0.1})
   ```
   `JsonFormatter` automatycznie wciąga `extra` fields do output JSON-a.

6. **Journal NIE jest migrowany pod logger.** `algo_bot/telemetry/journal.py` pozostaje osobnym CSV layerem. Logger ma rolę diagnostyczną (lifecycle, errors, retry, warnings); journal ma rolę event store (zamknięte trade'y z PnL, equity snapshots). Dwa różne audytoria: ludzki debug vs analiza wyników po fakcie.

7. **mypy strict-on-new dla `algo_bot.log`** — dodajemy moduł do `[[tool.mypy.overrides]]` z `disallow_untyped_defs = true` (analogicznie do `algo_bot.risk.*`, `algo_bot.metrics`).

8. **Scope retrofitu w tej sesji:** `live/live_binance.py` (27 callsite'ów) + `algo_bot/engine/backtester.py` (1). Pozostałe (`executor.py` po fixie FIXME, `fetch_data.py`, `process_data.py`, `sweep.py`, `binance_adapter.py`, indicators) w follow-up sesjach — każda jako mały deliverable.

9. **Future migration path:** Faza 5 (Production na VPS) może wymóc structured-first logging (Loki/Datadog). Wtedy osobny ADR rozważy migrację na `structlog` z zachowaniem stdlib-compat (`structlog.stdlib.LoggerFactory()` — caplog i third-party logi nadal działają). Decyzja odłożona — Faza 1 nie potrzebuje processor pipeline.

## Consequences

**Pozytywne:**
- **Zero zewnętrznych dependency** — stdlib `logging` jest częścią Pythona 3.11 (zgodne z mindset stdlib-first)
- **`pytest caplog` działa natywnie** — testy mogą weryfikować że krytyczne callsite'y (np. `_close_at` po TP/SL hit, recovery po restarcie, cancel_all_orders fail) faktycznie logują na właściwym poziomie
- **Logi z `ccxt` i `backtesting.py` łapią się** — root logger przechwytuje wszystko, możemy ustawić `logging.getLogger("ccxt").setLevel(WARNING)` żeby wyciszyć retry chatter zachowując errory
- **Ścieżka do Loki/Sentry/OTel bez refaktoru** — wszystkie sinki czytają stdlib `LogRecord`. W Fazie 5 dorzucamy `SentryHandler` / Loki via promtail bez zmiany callsite'ów.
- **Idiomatyczność** — `logger = logging.getLogger(__name__)` to standardowy pattern, każdy Python dev zna
- **Rotacja file** — `RotatingFileHandler` 10 MB × 5 backups = max 50 MB na hosta, nie zapełni dysku w long-running live trading
- **Hierarchical filtering** — można per subpakiet podkręcać level (np. `algo_bot.engine.exchanges` na DEBUG przy debugowaniu API, reszta INFO)

**Negatywne / koszty:**
- **Boilerplate** — `extra={...}` przy każdym strukturalnym callsite jest mniej ergonomiczne niż loguru `logger.info("Position {} opened", side)`. Można skompresować przez `LoggerAdapter` per-run, ale to dodatkowa konstrukcja
- **JsonFormatter — własny kod** — ~30 linii do utrzymania. Niewielki ale realny dług (np. gdy dodamy nowe pola standardowe trzeba dotknąć formatter'a)
- **Retrofit 27 callsite'ów w `live_binance.py`** — mechaniczna ale żmudna praca. Trzeba dokonać semantycznego rozróżnienia: `print("WARN ...")` → `logger.warning(...)`, `print("ERR ...")` → `logger.error(...)`, reszta `logger.info(...)` (z wyjątkiem `OPENED/CLOSED` które są informacyjne ale stoi po nich event do journala — info wystarczy)
- **`LoggerAdapter` vs `extra=` ergonomicznie nie jest idealne** — gdy strategia rozwija się na portfolio (multi-symbol, multi-strategy), kontekst staje się nietrywialny. Wtedy structlog z `bind_contextvars` byłby wygodniejszy. Akceptujemy na Fazę 1.

**Ryzyka:**
- Jeśli w Fazie 5 (Production) okaże się że ekosystem observability (Loki + Grafana + Alertmanager) wymaga structured-first logging na poziomie callsite'u (nazwy eventów + key=value pairs zamiast `f-string` messages), migracja na `structlog` będzie potrzebna. **Plan B:** osobny ADR-XXX w Fazie 5, migracja stopniowa moduł po module.
- Jeśli rozwiniemy multi-symbol portfolio (po MVP), kontekst per pozycja (`symbol`, `position_id`, `strategy_instance`) zacznie być rozjeżdżający się z prostym `LoggerAdapter`. Rozwiązanie: `contextvars`-based context propagation albo migracja na structlog.
- Jeśli `JsonFormatter` rozrośnie się o redaction sekretów (API keys), filtrowanie PII, sampling — wtedy `python-json-logger` może być prostszy niż własna implementacja. Próg migracji: ~80 linii w `JsonFormatter`.

## Alternatives Considered

- **`loguru`** — najwygodniejsze API w ekosystemie (`from loguru import logger; logger.info("...")`, built-in rotation, najlepsza serializacja exception tracebacks, `.bind()` dla kontekstu). Odrzucone bo: **`pytest caplog` NIE działa out-of-the-box** (loguru nie używa stdlib logging, wymaga `pytest-loguru` plugin lub interceptora który propaguje loguru → stdlib). **Logi z `ccxt`/`backtesting.py` (stdlib) nie idą do naszego sinka** bez `InterceptHandler` który trzeba dopisać ręcznie. Dwa miejsca gdzie magia może się popsuć w live tradingu — to dwie powierzchnie do debugowania w momentach które są stresujące (incydent o 3 w nocy). Singleton logger jest też mniej idiomatyczny dla "logger per module" które jest powszechne. Convenience wins ale infrastructure costs przeważają na Fazę 1.

- **`structlog`** — structured logging first-class (event_name + key=value zamiast f-string message), idealne dla observability stack (Loki/Datadog parsują JSON natywnie), composable processor pipeline (redaction, sampling, context binding), `bind_contextvars` dla kontekstu bez ręcznego przekazywania. `structlog.stdlib.LoggerFactory()` daje integrację ze stdlib (caplog działa, third-party logi spójne). Odrzucone na Fazę 1 bo: **najwyższa learning curve** (processor pipeline, BoundLogger, LoggerFactory wymagają zrozumienia), **najwięcej boilerplate w configu** (`configure(processors=[...])`), API "event + dict" jest mniej intuicyjne dla projektu który ma 76 prostych `print()` do retrofitu. **Główny kandydat na rewizję w Fazie 5** — gdy Loki/Grafana/Alertmanager staną się głównym konsumentem logów, structlog jest naturalnym następcą. Migracja zachowana otwarta przez stdlib-compat.

- **`python-json-logger`** — lekka dep (~80 KB) do JSON output, drop-in `Formatter` subclass. Odrzucone w Fazie 1 bo: nasz `JsonFormatter` ma ~30 linii, zero magii, łatwo go zaadaptować (np. dodać redaction). Stdlib-first wygrywa dopóki potrzeby format'u są proste. Próg migracji: gdy `JsonFormatter` zacznie wymagać redaction, sampling, multiple shapes na różne sinki — wtedy `python-json-logger` (lub structlog) wygrywa.

- **Custom logger framework od zera** — własna abstrakcja z dispatcherem, formatterami, handlerami. Odrzucone bo: scope creep, ekosystem Pythona już ma stdlib `logging` które działa.

- **Bez logger framework — `print()` z konwencją prefiksów** (np. `print(f"[INFO] ...")`, `print(f"[ERR] ...")`). Odrzucone bo: nie da się filtrować, nie da się ratować do Loki/Sentry, nie da się testować przez `caplog`, nie da się wyciszyć w VPS production. To stan obecny — całość Decyzji C jest po to żeby z niego wyjść.

## References

- Plik (po implementacji): `algo_bot/log.py`
- Pliki konsumujące (retrofit w tej sesji): `live/live_binance.py`, `algo_bot/engine/backtester.py`
- Pliki w follow-up: `algo_bot/executor.py` (po fixie FIXME), `algo_bot/fetch_data.py`, `algo_bot/process_data.py`, `algo_bot/engine/sweep.py`, `algo_bot/engine/exchanges/binance_adapter.py`
- Powiązane ADR:
  - ADR-002 (pyproject-hatchling-stack) — mypy strict-on-new policy obowiązuje nowy moduł `algo_bot.log`
  - ADR-005 (backtesting.py jako silnik MVP) — `backtesting.py` używa stdlib logging, automatycznie wpięty
- Python docs: <https://docs.python.org/3/library/logging.html>
- Python logging cookbook: <https://docs.python.org/3/howto/logging-cookbook.html>

## Notes

- **Status journala (`algo_bot/telemetry/journal.py`):** ZACHOWANY jako osobny layer. Journal trzyma structured event store (trades + equity per `run_id` w CSV) — to event sourcing dla wyników tradingu, nie diagnostyka. Logger trzyma diagnostykę (lifecycle, errors, third-party chatter). Dwie warstwy, dwa cele, dwa formaty. Współpracują: `logger.info("Position opened", extra={"trade_id": ...})` + `journal.log_entry(trade_id, ...)` — logger mówi "co system robi", journal mówi "co tradingowo się stało".

- **Migracja `journal` na strukturalny logger w przyszłości:** nie ma planów. Journal ma dedykowany schema (kolumny CSV), używany przez post-run analytics i reconciliation (Faza 4 — equity z giełdy vs equity z journala). Jeśli kiedyś zechcemy unify journal + log do jednego sinka (np. Loki), będzie to osobny ADR z analizą czy event store powinien być w tym samym pipelinie co diagnostics.

- **Follow-up sesje (poza tym deliverable):**
  - Retrofit `executor.py` (po fixie FIXME na `optimize_backtest`)
  - Retrofit `fetch_data.py`, `process_data.py`, `sweep.py`, `binance_adapter.py`
  - Dorobić `LoggerAdapter` factory dla per-run kontekstu (gdy stanie się powtarzalnym wzorcem)
  - Faza 5 — `SentryHandler` / Loki sink — osobny ADR jeśli wymaga rozważenia structlog

- **Konwencja message:** logger messages po polsku (zgodne z `feedback_engineering_mindset` reguła #5 — docstringi/komentarze PL). Event names jako keyword arguments po angielsku gdy strukturalne (`logger.info("position_opened", side="long")`). Mieszanka: human-readable message PL + structured fields EN.
