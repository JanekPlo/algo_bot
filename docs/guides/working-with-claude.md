# Working with Claude

Jak współpracować z Claudem (Cowork) nad algo_bot tak, żeby projekt nie rozjeżdżał się przez 10-15 tygodni MVP.

> **TL;DR:** Jedna sesja Cowork = jeden deliverable (ADR, moduł, decyzja, task). Plus jedna poboczna sesja **mózg-Claude** która raz w tygodniu audytuje repo i flaguje drift. Warstwa trwałości: `ROADMAP.md` + `docs/adr/` + `docs/CHANGELOG.md` + memory.

---

## Dlaczego nie jedna mega-sesja na wszystko

Cowork ma ograniczone okno kontekstu. Im dłuższa sesja, tym bardziej:
- Claude traci precyzję i wraca do wcześniej odrzuconych pomysłów
- Trudniej Tobie przeglądać historię rozmowy
- Konteksty się mieszają (research mindset vs DevOps mindset vs decyzja architektoniczna)

Stąd model: **jedna sesja per deliverable** (typowo 1-3h), z jasnym początkiem i końcem. Sesja kończy się commitem + zaktualizowanymi artefaktami (ADR / CHANGELOG / docs / memory).

---

## Model sesji roboczej

### Co jest jednym deliverable

Dobre granice:
- jeden ADR (np. ADR-006 logging)
- jeden moduł (np. `algo_bot/risk/limits.py`)
- jedna konkretna analiza (np. "walk-forward na bghtrend, 5 fold")
- jedna decyzja techniczna z implementacją (np. "wybór silnika WF + napisanie analyzera")
- jeden task z deployment (np. "Dockerfile + docker-compose")

Złe granice:
- "cała Faza 1" — za dużo
- "ulepsz repo" — za mało konkretu
- "research strategii" — bez deliverable nie wiadomo kiedy koniec

### Kickoff — jak zacząć sesję

Pierwsza wiadomość Twojego nowego czatu powinna zawierać:

```
Cel sesji: <konkretny deliverable, np. "ADR-008 risk module + implementacja algo_bot/risk/limits.py">
Kontekst w ROADMAP: <Faza 1, decyzja E>
Powiązane ADR/docs: <linki, np. ADR-003 StrategyBase API>
Dependency: <co musi być gotowe wcześniej — najczęściej nic, czasem inny ADR>
Definicja done: <np. "ADR napisany, moduł zaimplementowany, testy zielone, CHANGELOG zaktualizowany">
```

Dzięki temu Claude od razu wie gdzie jest i co czytać. Nie musi zgadywać.

### Closeout — jak skończyć sesję

Przed zakończeniem każdej sesji roboczej Claude (i Ty) sprawdza:

1. **Code** — commit + push, testy zielone (`make check`)
2. **ADR** — jeśli była decyzja architektoniczna, jest spisana w `docs/adr/`
3. **CHANGELOG** — wpis w `[Unreleased]` opisuje co dodano/zmieniono
4. **Docs** — jeśli zmieniło się publiczne API albo doszedł nowy moduł, `docs/reference/modules/<nazwa>.md` zaktualizowany
5. **ROADMAP** — odpowiednie checkboxy w `docs/ROADMAP.md` przestawione na `[x]` z datą `DONE YYYY-MM-DD`
6. **Memory** — jeśli pojawiła się preferencja / decyzja / gotcha warty zapamiętania w przyszłych sesjach, Claude zapisuje do memory

Closeout to nie ceremonia — to jedyna warstwa która zostaje po zamknięciu czatu.

---

## Mózg-Claude (osobna sesja)

Mózg-Claude nie pisze kodu produkcyjnego. Jest project managerem / architektem / audytorem repo.

### Co robi mózg

- Czyta `ROADMAP.md` i sprawdza checkboxy vs realny stan repo (`git log`, struktura plików, testy)
- Czyta ostatnie commity i ADR-y, sprawdza spójność
- Sprawdza czy `docs/` nie odjechały od kodu (publiczne API zgodne z opisem w `docs/reference/`)
- Sprawdza czy `CHANGELOG.md` `[Unreleased]` faktycznie odzwierciedla ostatnie zmiany
- Flaguje porzucone TODO/FIXME w kodzie
- Wskazuje kolejny logiczny krok (które ADR/moduł na priorytet)
- Pisze raport tygodniowy do `docs/captains-log/YYYY-MM-DD.md`

### Czego mózg NIE robi

- Nie modyfikuje kodu w `algo_bot/`
- Nie podejmuje sam decyzji architektonicznych — gdy wykryje że trzeba zdecydować, flaguje to do rozmowy z Janekiem
- Nie commituje samodzielnie (poza `docs/captains-log/`, ROADMAP checkboxami i memory)

### Tryby pracy mózgu

**Tryb 1 — Weekly audit (scheduled)**

Raz w tygodniu (poniedziałek rano) scheduled task uruchamia mózg z promptem:

```
Audit tygodniowy algo_bot.

Sprawdź:
- git log od ostatniego captains-log (data w docs/captains-log/)
- ROADMAP checkboxy vs realny stan repo
- ADR — czy każda decyzja z ostatniego tygodnia ma swój ADR
- CHANGELOG [Unreleased] — czy odzwierciedla commity
- docs sync — czy dla nowych modułów jest wpis w docs/reference/modules/
- TODO/FIXME w nowych plikach

Wynik:
- docs/captains-log/YYYY-MM-DD.md z sekcjami: Progress, Drift detected, Suggested next deliverable, Open questions for Janek
- update memory jeśli pojawiła się trwała preferencja
- jeśli wykryłeś coś wymagającego decyzji Janka — wypisz to wyraźnie
```

**Tryb 2 — Pre-flight (on-demand)**

Gdy wracasz po przerwie albo nie wiesz co dalej, otwierasz nową sesję mózga z promptem:

```
Pre-flight check przed sesją roboczą.

Powiedz:
- gdzie jesteśmy w ROADMAP (faza, najbliższe deliverable)
- co się stało od ostatniego captains-log
- jaki deliverable proponujesz jako następny (z uzasadnieniem)
- napisz kickoff prompt dla sesji roboczej nad tym deliverable
```

Wynik mózga — kickoff prompt — kopiujesz do nowej sesji roboczej.

---

## Warstwa trwałości — co przeżywa między sesjami

| Co | Gdzie | Aktualizuje |
|---|---|---|
| Plan rozwoju | `docs/ROADMAP.md` | sesje robocze (checkboxy) + mózg (drift) |
| Decyzje architektoniczne | `docs/adr/NNN-*.md` | sesje robocze |
| Historia zmian | `docs/CHANGELOG.md` | sesje robocze |
| Stan operacyjny | `docs/captains-log/YYYY-MM-DD.md` | mózg (audyt) |
| Preferencje Janka, konwencje, gotchas | Cowork memory | każda sesja |
| Stan kodu | git | sesje robocze |

Memory dotyczy **kim jest Janek i jak pracujemy**. Nie należy tam pisać "co jest zrobione w fazie 1" — to żyje w ROADMAP i git logu.

---

## Mini-rytm dla typowego tygodnia (Faza 1)

```
Pn rano        : scheduled mózg-audit -> docs/captains-log/YYYY-MM-DD.md
Pn po południu : pre-flight (jeśli niejasność) -> kickoff prompt
Wt-Pt          : 1-3 sesje robocze, każda 1-3h, każda kończy się closeout
Piąt wieczór   : opcjonalny "check-in" z mózgiem — co zostało, co zaplanować na pn
```

Gdy wpadnie ad-hoc task (bug, mała decyzja), nie czekaj na cykl — otwórz krótką sesję roboczą i zamknij ją tego samego dnia.

---

## Co się zmienia w kolejnych fazach

**Faza 1 (Foundation):** głównie sesje robocze nad ADR i modułami (logging, metrics, risk, walk-forward, CI). Mózg robi tylko weekly audit.

**Faza 2 (Research & Backtest):** sesje robocze stają się dłuższe (notebooki, analiza). Mózg dochodzi rola "drugiego oka" do decyzji "czy bghtrend jako MVP" — sesja decyzyjna z Janekiem + mózgiem.

**Faza 3-4 (Paper / Mainnet):** dochodzi scheduled task "daily live PnL vs backtest baseline" obsługiwany przez mózga. Sesje robocze koncentrują się na live edge cases i recovery.

**Faza 5 (VPS production):** mózg dostaje rolę "operacyjną" — alerty z Prometheus/Grafana są kierowane do osobnej sesji która triażuje. Sesje robocze zwykle reagują na incydenty albo deployują nowe wersje.

**Po MVP:** dochodzi quant brain — sesja która analizuje regime changes, korelacje strategii w portfolio, decyzje o re-walk-forward.

---

## Szablon — kickoff promptu (skopiuj i wypełnij)

```
Cel sesji:
Kontekst w ROADMAP:
Powiązane ADR/docs:
Dependency:
Definicja done:

Dodatkowo — Janek's preferences (z memory, dla pewności):
- decyzje architektoniczne uzgadniamy razem z opcjami i trade-offs PRZED implementacją
- responses po polsku
- nie kombinujemy z mockowaniem w testach które mają wartość integracyjną
```

## Szablon — closeout checklist (skopiuj na koniec sesji)

```
Closeout checklist:
[ ] Code: commit + push, testy zielone (make check)
[ ] ADR: jeśli była decyzja architektoniczna — spisana w docs/adr/
[ ] CHANGELOG: wpis w [Unreleased]
[ ] Docs: docs/reference/modules/<nazwa>.md zaktualizowane jeśli zmiana publicznego API
[ ] ROADMAP: odpowiedni checkbox przestawiony z datą DONE
[ ] Memory: zapisana preferencja / gotcha / decyzja jeśli warta zapamiętania
```

---

## Anti-patterns — czego nie robić

- **Jedna sesja na "całą fazę"** — rozjedzie się po 2-3 godzinach.
- **Sesja robocza która nie kończy się commit + closeout** — wiedza przepada w czacie.
- **Pytanie mózga o decyzję architektoniczną** — mózg może analizować i flagować, ale decyzję podejmujesz Ty (z opcjami od Claude'a) w sesji roboczej.
- **Pisanie do memory rzeczy które żyją w ROADMAP/git** — duplikacja, drift gwarantowany.
- **Pomijanie kickoff promptu** — bez niego Claude marnuje pierwszą część sesji na ustalanie gdzie jesteśmy.

---

*Wersja: 0.1 — 2026-05-21. Dokument żywy — aktualizujemy gdy rytm pracy się zmieni (np. wejście w Fazę 2 wymaga dorzucenia sekcji o sesjach research).*
