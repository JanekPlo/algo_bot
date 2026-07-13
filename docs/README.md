# algo_bot — dokumentacja

> Quantitative trading framework dla kryptowalutowych futures (USDT-M perpetuals), metodologia RBI (Research → Backtest → Implement).

## Zacznij tutaj

Nowy w projekcie? Idź w tej kolejności:

1. **[Getting Started](guides/getting-started.md)** — setup od zera (uv 0.11.28, Python 3.12.13, `uv.lock`)
2. **[Daily Workflow](guides/daily-workflow.md)** — co robisz codziennie (komendy, cykl edit → test → commit)
3. **[Makefile Cheatsheet](guides/makefile-cheatsheet.md)** — każdy `make <target>` wytłumaczony
4. **[Working with Claude](guides/working-with-claude.md)** — jak współpracować z Claudem (Cowork) na tym projekcie: sesje robocze, mózg-Claude, kickoff/closeout
5. **[Package Overview](reference/package-overview.md)** — co siedzi w którym katalogu

Runtime Beta 0 używa wyłącznie domyślnej ścieżki
`uv sync --locked` + `uv run`. `uv.lock` jest kanonicznym lockfilem,
NautilusTrader jest przypięty do 1.230.0, a TA-Lib do 0.7.0 (wheel zawiera
bibliotekę C). Materiały opisujące Condę/`environment.yml` lub pip-tools jako
aktywny default są historyczne i **superseded**.

## Struktura docs

Dokumentacja organizowana wg [Diátaxis framework](https://diataxis.fr/) — cztery odrębne typy:

| Typ | Folder | Po co | Przykład |
|---|---|---|---|
| **Tutorials** | (na razie brak) | Krok-po-kroku do nauki | "Twój pierwszy backtest" |
| **How-to guides** | `guides/` | Procedury do konkretnych zadań | "Jak dodać nową strategię" |
| **Reference** | `reference/` | Encyklopedyczne, słownikowe | "API `StrategyBase`" |
| **Explanation** | `concepts/` | Zrozumieć dlaczego | "Walk-forward methodology" |

Plus dwa rodzaje docs strategicznych:

- **[ROADMAP.md](ROADMAP.md)** — 5-fazowy plan rozwoju (Foundation → Production)
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — warstwy systemu, mapa modułów, decyzje wysokopoziomowe
- **[adr/](adr/README.md)** — Architecture Decision Records — *dlaczego* coś jest tak a nie inaczej
- **[CHANGELOG.md](CHANGELOG.md)** — wersjonowane zmiany (keep-a-changelog)
- **[MMS v2 executable spec](specs/mms-v2-executable-spec.md)** — źródło prawdy dla
  domeny, tranzycji, sizingu, idempotencji i recovery
- **[Beta preregistration](experiments/mms-v2-beta-preregistration.md)** — zamrożone
  okno development/holdout, macierz ablation i profil kosztów P9
- **[Beta results](experiments/mms-v2-beta-results.md)** — wyniki 12 runów P9,
  ograniczenia kwalifikowalności i decyzja `ITERATE BETA`

## Mapa docs

```
docs/
├── README.md                          # ten plik
├── ROADMAP.md                         # plan rozwoju (5 faz)
├── ARCHITECTURE.md                    # architektura wysokopoziomowa
├── CHANGELOG.md                       # historia zmian
│
├── adr/                               # Architecture Decision Records
│   ├── README.md                      # ADR index + policy
│   ├── template.md                    # szablon nowego ADR
│   ├── 001-flatten-package-layout.md
│   ├── 002-pyproject-hatchling-stack.md
│   ├── 003-strategybase-signal-api.md
│   ├── 004-hybrid-tp-sl-mode.md
│   ├── 005-backtesting-py-mvp-engine.md
│   ├── 006-logging-strategy.md
│   ├── 007-risk-adjusted-metrics.md
│   ├── 008-risk-limits-module.md
│   ├── 009-walk-forward.md
│   ├── 010-automated-quality-gates-ci-pre-commit.md
│   ├── 011-microstructure-adjustments.md
│   ├── 012-mvp-no-go-bghtrend.md
│   ├── 013-wf-eligibility-thresholds.md
│   └── 014-engine-migration-nautilus.md
│
├── guides/                            # how-to (zorientowane na zadanie)
│   ├── getting-started.md             # setup od zera
│   ├── daily-workflow.md              # codzienne komendy + cykl
│   ├── makefile-cheatsheet.md         # każdy make target
│   ├── working-with-claude.md         # workflow Cowork: sesje + mózg-Claude
│   ├── adding-a-strategy.md           # (TBD — faza 2)
│   ├── data-fetching.md               # pobieranie i walidacja danych
│   ├── running-backtest.md            # pojedynczy backtest
│   ├── running-sweep.md               # sweep parametrów
│   ├── vps-research-runner.md         # locked research runtime na VPS
│   ├── walk-forward-howto.md          # (TBD — po decyzji F)
│   ├── live-trading-checklist.md      # (TBD — faza 3-4)
│   ├── deploying-to-vps.md            # (TBD — faza 5)
│   └── troubleshooting.md             # (TBD — zbieramy po drodze)
│
├── reference/                         # encyklopedia (info-oriented)
│   ├── package-overview.md            # tree z opisami
│   ├── modules/                       # per-moduł deep reference
│   │   └── metrics.md                 # ADR-007 — algo_bot.metrics
│   ├── config-reference.md            # (TBD — YAML schemas)
│   └── metrics-reference.md           # (TBD — interpretacja metryk w summary.json)
│
├── specs/
│   └── mms-v2-executable-spec.md       # wykonywalny kontrakt MMS-inspired v2
├── experiments/
│   ├── mms-v2-beta-preregistration.md  # prerejestracja development-only P9
│   └── mms-v2-beta-results.md          # wyniki P9 + decyzja iterate Beta
│
└── concepts/                          # narrative explanations
    ├── glossary.md                    # terminologia
    ├── rbi-methodology.md             # (TBD)
    ├── risk-management.md             # (TBD — po decyzji E)
    ├── walk-forward.md                # (TBD — po decyzji F)
    └── microstructure.md              # (TBD)
```

Pliki oznaczone `(TBD ...)` będą dodane w odpowiednich fazach/po odpowiednich decyzjach.

## Konwencje pisania docs

**Markdown plain** (GitHub Flavored). Ewentualny upgrade do MkDocs + Material wymaga
osobnej przyszłej decyzji; ADR-006 dotyczy obecnie strategii logowania.

**Docstring style w kodzie**: Google (`Args:`, `Returns:`, `Raises:`). Patrz przykłady w [guides/adding-a-strategy.md](guides/adding-a-strategy.md) (gdy powstanie).

**Per-file headers**: każdy plik `.py` w pakiecie ma 5-15 linijkowy docstring header z opisem co robi + public API + pointer do `docs/reference/modules/<plik>.md`.

**Język**: docs po polsku (zgodnie z preferencją autora). Docstringi i komentarze w kodzie też po polsku. Tylko nazwy publicznego API (klasy, funkcje, parametry) po angielsku.

**Pisanie sync z kodem**: każda zmiana publicznego API ALBO dodanie nowego modułu = update docs w tym samym PR/commicie. Wymusza to dyscyplinę i nie pozwala docs zostać daleko w tyle.

## Status docs (Beta)

✓ = napisane | ⧗ = w trakcie | ☐ = planowane

| Doc | Status |
|---|---|
| docs/README.md | ✓ |
| docs/ROADMAP.md | ✓ |
| docs/ARCHITECTURE.md | ✓ |
| docs/CHANGELOG.md | ✓ |
| docs/adr/README.md | ✓ |
| docs/adr/template.md | ✓ |
| docs/adr/001..014 | ✓ |
| docs/specs/mms-v2-executable-spec.md | ✓ |
| docs/experiments/mms-v2-beta-preregistration.md | ✓ |
| docs/experiments/mms-v2-beta-results.md | ✓ |
| docs/guides/getting-started.md | ✓ |
| docs/guides/daily-workflow.md | ✓ |
| docs/guides/makefile-cheatsheet.md | ✓ |
| docs/guides/data-fetching.md | ✓ |
| docs/guides/running-backtest.md | ✓ |
| docs/guides/running-sweep.md | ✓ |
| docs/guides/vps-research-runner.md | ✓ |
| docs/guides/working-with-claude.md | ✓ |
| docs/reference/package-overview.md | ✓ |
| docs/concepts/glossary.md | ✓ |

Reszta dochodzi w fazach 2-5 zgodnie z deliverables ROADMAP.
