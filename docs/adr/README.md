# Architecture Decision Records (ADRs)

## Po co ADRs

Każda znacząca decyzja architektoniczna lub techniczna w algo_bot żyje w osobnym pliku **ADR**. Powód: gdy za pół roku, rok, dwa lata, zobaczymy w kodzie coś co wygląda dziwnie i pomyślimy "po co to tak zrobiliśmy?" — ADR powie *dlaczego*. Commit messages są niewystarczające (krótkie, rozproszone, trudno przeszukiwalne).

ADR to **decyzja w czasie** — zapis stanu wiedzy i ograniczeń w momencie podjęcia. Nawet jeśli za rok decyzję zmienimy (przez **superseding ADR**), oryginał zostaje jako historia.

Format: lekki, oparty na propozycji [Michael Nygard'a](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

## Index

| # | Tytuł | Status | Data | Faza |
|---|---|---|---|---|
| [001](001-flatten-package-layout.md) | Flatten repo + `algo_bot/` package layout | Accepted | 2026-05-14 | 1 |
| [002](002-pyproject-hatchling-stack.md) | pyproject.toml + hatchling + conda + pip-tools + ruff + mypy | Accepted | 2026-05-14 | 1 |
| [003](003-strategybase-signal-api.md) | StrategyBase + Signal — unified API dla backtest+live | Accepted (retroactive) | pre-2026-05 | 0 |
| [004](004-hybrid-tp-sl-mode.md) | Hybrid TP/SL — server/local/hybrid mode w live | Accepted (retroactive) | pre-2026-05 | 0 |
| [005](005-backtesting-py-mvp-engine.md) | backtesting.py jako silnik backtestowy MVP | Accepted (retroactive) | pre-2026-05 | 0 |
| [006](006-logging-strategy.md) | Logging — stdlib logging z JSON file handler | Accepted | 2026-05-21 | 1 |
| [007](007-risk-adjusted-metrics.md) | Risk-adjusted metrics — hand-rolled `algo_bot/metrics.py` | Accepted | 2026-05-22 | 1 |
| [008](008-risk-limits-module.md) | Risk limits module — pure gates + backtester hook | Accepted | 2026-05-24 | 1 |
| [009](009-walk-forward.md) | Walk-forward analyzer — `algo_bot/engine/walkforward.py` | Accepted | 2026-05-25 | 1 |
| [010](010-automated-quality-gates-ci-pre-commit.md) | Automated quality gates — CI + pre-commit | Accepted | 2026-05-25 | 1 |
| [011](011-microstructure-adjustments.md) | Microstructure adjustments — slippage + funding overlay (`algo_bot/microstructure.py`) | Accepted | 2026-06-19 | 2 |
| [012](012-mvp-no-go-bghtrend.md) | MVP no-go for `bghtrend_pullback` — kept as historical baseline | Accepted | 2026-07-05 | 2 |
| [013](013-wf-eligibility-thresholds.md) | WF-eligibility thresholds — pre-WF filter (`WF_ELIGIBILITY_THRESHOLDS`) | Accepted | 2026-07-05 | 2 |
| [014](014-engine-migration-nautilus.md) | Engine migration to `nautilus_trader` — parallel coexistence with `backtesting.py` | Accepted | 2026-07-13 | 2 |

## Polityka

**Kiedy piszemy ADR:**
- Wybór biblioteki/frameworka z konsekwencjami na cały projekt (np. silnik backtestowy, build backend)
- Decyzja o publicznym API modułu (np. sygnatura `Signal`, `Strategy.on_bar`)
- Reorganizacja struktury (layout, namespace, package boundaries)
- Wybór konwencji który będziemy egzekwować (docstring style, linter config, type checker policy)
- Decyzja o trade-offie ryzyko-vs-pragmatyzm (np. hybrid TP/SL zamiast pełnego server-side)
- Cofnięcie wcześniejszej decyzji (superseding)

**Kiedy NIE piszemy ADR:**
- Trywialne fixy (literówka, brakujący import)
- Drobne refactory bez zmiany API
- Stylistyka (formatowanie, naming pojedynczych zmiennych)
- Decyzje per-strategia (parametry strategii idą do code i ROADMAP, nie ADR)

**Numeracja:** sekwencyjna, 3-cyfrowa, zero-padded (`001`, `002`, ...). Nigdy nie używamy ponownie numeru — gdy ADR jest superseded, nowy dostaje kolejny numer.

**Statusy:**
- **Proposed** — przedyskutowane, ale jeszcze niezatwierdzone
- **Accepted** — zatwierdzone, w mocy
- **Deprecated** — odradzane, ale nadal w użyciu (planowane do usunięcia)
- **Superseded by ADR-NNN** — zastąpione nowszą decyzją (link do następcy)

**Format:** patrz [template.md](template.md). Każdy ADR zawiera:
- Header (numer, tytuł, status, data)
- Context (jaki problem rozwiązujemy, co było wcześniej, ograniczenia)
- Decision (co konkretnie wybieramy)
- Consequences (co się zmienia, plusy + minusy + ryzyka)
- Alternatives Considered (jakie inne opcje były na stole, dlaczego odrzucone)
- (opcjonalnie) References, Notes

**Język:** historyczne ADR-y są po polsku. Nowe ADR-y od końca Fazy 1 piszemy po angielsku zgodnie z aktualną konwencją dokumentacji. Nazwy techniczne (biblioteki, klasy, parametry) po angielsku.

## Jak dodać nowy ADR

```bash
cd ~/quant_projects/algo_bot
# Skopiuj template do nowego pliku (kolejny wolny numer)
N=$(printf "%03d" $(($(ls docs/adr/[0-9]*.md 2>/dev/null | wc -l) + 1)))
cp docs/adr/template.md docs/adr/${N}-krotki-opis-decyzji.md
# Edytuj plik, wypełnij sekcje
# Dodaj wpis do tabeli powyżej (Index)
git add docs/adr/${N}-*.md docs/adr/README.md
git commit -m "docs: ADR-${N} <tytul>"
```
