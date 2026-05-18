# algo_bot — dokumentacja

> Quantitative trading framework dla kryptowalutowych futures (USDT-M perpetuals), metodologia RBI (Research → Backtest → Implement).

## Zacznij tutaj

Nowy w projekcie? Idź w tej kolejności:

1. **[Getting Started](guides/getting-started.md)** — setup od zera (conda env, install, weryfikacja)
2. **[Daily Workflow](guides/daily-workflow.md)** — co robisz codziennie (komendy, cykl edit → test → commit)
3. **[Makefile Cheatsheet](guides/makefile-cheatsheet.md)** — każdy `make <target>` wytłumaczony
4. **[Package Overview](reference/package-overview.md)** — co siedzi w którym katalogu

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
│   └── 005-backtesting-py-mvp-engine.md
│
├── guides/                            # how-to (zorientowane na zadanie)
│   ├── getting-started.md             # setup od zera
│   ├── daily-workflow.md              # codzienne komendy + cykl
│   ├── makefile-cheatsheet.md         # każdy make target
│   ├── adding-a-strategy.md           # (TBD — faza 2)
│   ├── running-backtest.md            # (TBD — faza 2)
│   ├── running-sweep.md               # (TBD — faza 2)
│   ├── walk-forward-howto.md          # (TBD — po decyzji F)
│   ├── live-trading-checklist.md      # (TBD — faza 3-4)
│   ├── deploying-to-vps.md            # (TBD — faza 5)
│   └── troubleshooting.md             # (TBD — zbieramy po drodze)
│
├── reference/                         # encyklopedia (info-oriented)
│   ├── package-overview.md            # tree z opisami
│   ├── modules/                       # (TBD — per moduł)
│   ├── config-reference.md            # (TBD — YAML schemas)
│   └── metrics-reference.md           # (TBD — po decyzji D)
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

**Markdown plain** (GitHub Flavored). Po MVP rozważamy upgrade do MkDocs + Material — patrz [ADR-006 (planowany)](adr/006-mkdocs-after-mvp.md).

**Docstring style w kodzie**: Google (`Args:`, `Returns:`, `Raises:`). Patrz przykłady w [guides/adding-a-strategy.md](guides/adding-a-strategy.md) (gdy powstanie).

**Per-file headers**: każdy plik `.py` w pakiecie ma 5-15 linijkowy docstring header z opisem co robi + public API + pointer do `docs/reference/modules/<plik>.md`.

**Język**: docs po polsku (zgodnie z preferencją autora). Docstringi i komentarze w kodzie też po polsku. Tylko nazwy publicznego API (klasy, funkcje, parametry) po angielsku.

**Pisanie sync z kodem**: każda zmiana publicznego API ALBO dodanie nowego modułu = update docs w tym samym PR/commicie. Wymusza to dyscyplinę i nie pozwala docs zostać daleko w tyle.

## Status docs (faza 1)

✓ = napisane | ⧗ = w trakcie | ☐ = planowane

| Doc | Status |
|---|---|
| docs/README.md | ✓ |
| docs/ROADMAP.md | ✓ |
| docs/ARCHITECTURE.md | ✓ |
| docs/CHANGELOG.md | ⧗ |
| docs/adr/README.md | ⧗ |
| docs/adr/template.md | ⧗ |
| docs/adr/001..005 | ⧗ |
| docs/guides/getting-started.md | ⧗ |
| docs/guides/daily-workflow.md | ⧗ |
| docs/guides/makefile-cheatsheet.md | ⧗ |
| docs/reference/package-overview.md | ⧗ |
| docs/concepts/glossary.md | ⧗ |

Reszta dochodzi w fazach 2-5 zgodnie z deliverables ROADMAP.
