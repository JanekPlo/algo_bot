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

## Setup techniczny — jak Cowork widzi repo

Sekcja dla każdej nowej sesji Claude'a: gdzie żyje repo, jak je czytasz, jak pushujesz, czego unikać. Bez tego pierwsze 30 minut sesji idzie na rozpoznanie środowiska.

### Lokalizacja repo

**Kanoniczna ścieżka:** `~/quant_projects/algo_bot` po stronie WSL (Ubuntu, native ext4 FS).

Dla Cowork (Windows desktop) ta sama lokalizacja jest dostępna jako UNC: `\\wsl.localhost\ubuntu\home\janek\quant_projects\algo_bot`. WSL2 wystawia ten share automatycznie — nic nie trzeba konfigurować poza zainstalowanym WSL2.

**Dlaczego WSL native, nie NTFS:**
- Conda env `algo_bot` jest w WSL i operuje na repo bez tłumaczeń line endings / permissions
- TA-Lib z conda-forge linkuje się natywnie z `libta-lib.so` po stronie Linuxa
- Git ma normalne POSIX permissions, brak fałszywych diffów z `core.fileMode`
- Brak ryzyka spacji w ścieżce (Windows ma `Documents\Claude\Projects\...`, WSL ma czysty home)

Kopiowanie repo na NTFS-side było wcześniej rozważane jako workaround dla "UNC paths are not supported", ale **nie jest potrzebne** — patrz sekcja "Setup nowego projektu Cowork" niżej.

### Setup nowego projektu Cowork (jednorazowo)

Cowork desktop ma pojęcie "Project" (lewy górny dropdown). Każdy projekt ma własną listę **workspace folders** (mountów) i własny **memory dir**. Workspace folders są project-scoped — zostają między sesjami tego samego projektu, ale nie krzyżują się między projektami.

**Konfiguracja projektu algo_bot:**

1. Cowork → menu projektów → New Project → nazwa: `algo_bot`
2. Project settings → Connect folder → w polu adresu wpisz: `\\wsl.localhost\ubuntu\home\janek\quant_projects\algo_bot`
   - Windows file picker akceptuje UNC paths jeśli wpisać je w pasek górny eksploratora
   - Cowork weryfikuje że folder istnieje i dodaje go jako persistent mount
3. Project instructions — wklej zwięzły opis projektu (workspace folder, mindset, język, conventions). Wzór: patrz repo `docs/guides/working-with-claude.md` (sekcja "Project instructions template" — TODO dorobić jako appendix).

Od tego momentu **każda nowa sesja w projekcie algo_bot dostaje UNC mount automatycznie**. Nie trzeba dodawać foldera ręcznie per sesję.

### Jak Cowork montuje repo w runtime

Po dodaniu UNC folder do projektu, w trakcie sesji:

- **Read/Write/Edit** widzą repo jako ścieżkę UNC `\\wsl.localhost\ubuntu\home\janek\quant_projects\algo_bot\<path>` — działa normalnie, file IO przez WSL2 share.
- **Sandbox bash** widzi to samo repo jako linuxowy mount `/sessions/<id>/mnt/algo_bot/<path>`. Można tam odpalać `git status`, `git log`, `cat`, `grep`, ale nie `make check` z conda env (sandbox nie ma własnego conda — patrz sekcja "Conda + TA-Lib").
- Mount jest read+write — sandbox może modyfikować pliki i commitować.

### Git workflow

**Sandbox bash nie działa gdy workspace folder jest UNC.** Próba `cd /sessions/<id>/mnt/algo_bot/` z poziomu sandboxa pada z `UNC paths are not supported` — i to blokuje **wszystkie** wywołania bash w sesji, nawet te nie dotyczące UNC mountu (bo sandbox root sam zawiera UNC mount). Wszystkie operacje shellowe (`git`, `make check`, `pytest`, `ruff`, `mypy`) wykonuje user w WSL terminalu, **nie** Claude w sandboxie.

**Workflow per session:**
- **W trakcie sesji** — Claude edytuje pliki przez Read/Write/Edit. Te toole nie idą przez sandbox bash, tylko przez Windows API → UNC → WSL FS. Stąd działają niezależnie od ograniczenia bash.
- **Closeout** — Claude przygotowuje commit message + listę komend do skopiowania (`git add ...`, `git commit -m "..."`, `git push origin master`).
- **User** wykonuje commit + push w WSL terminalu, potwierdza w czacie że push poszedł. Sesja się zamyka.

**SSH key i git remote** — konfigurujesz raz po stronie WSL (`~/.ssh/config` z aliasem dla GitHub deploy key, albo `GIT_SSH_COMMAND` w `.bashrc`). Klucz prywatny NIE leży w repo (`.ssh/` w `.gitignore`).

**Pull** — robisz w WSL ręcznie gdy chcesz mieć aktualne repo lokalnie (np. przed pracą w PyCharm Remote-WSL albo VS Code z extension WSL). Conda env operuje na tym samym katalogu — zero kopiowania.

**Rozważana w przyszłości alternatywa:** jeśli sandbox bash stałby się kluczowy (np. automated CI in-session, container Docker dla make check), wtedy repo można przenieść na NTFS-side (`~/Documents/Claude/Projects/algo_bot`) i sandbox uzyska pełną funkcjonalność. Cena: NTFS line endings + permissions diffy w git, conda env operuje przez `/mnt/c/...` (wolniej niż native ext4), spacje w ścieżce nadrzędnej (`Documents\Claude\Projects\`). Decyzja odłożona do faktycznej potrzeby — obecny workflow "Claude edytuje, user commituje" jest akceptowalny.

### Conda + TA-Lib

Conda env `algo_bot` siedzi w WSL (`~/miniconda3/envs/algo_bot/`). TA-Lib zainstalowany z conda-forge (wymaga systemowego `libta-lib.so` którego conda-forge dostarcza razem z pythonowym bindingiem).

Sandbox Cowork **nie ma własnego conda** — `make check`, `pytest`, `ruff`, `mypy` musisz odpalać:
- albo w WSL terminalu ręcznie (`cd ~/quant_projects/algo_bot && make check`)
- albo z sandboxa przez `wsl.exe -d Ubuntu bash -lc "..."` jeśli sandbox umie wywołać `wsl.exe` (do zweryfikowania per sesja)

Alternatywa rozważana post-MVP: container Docker z conda env + TA-Lib, żeby sandbox `docker run` był self-contained bez zależności od WSL. Decyzja odłożona — obecny setup wystarcza dla fazy 1-2.

### Decyzja workflow gdy nowa sesja nie ma dostępu

Symptomy:
- `request_cowork_directory` na UNC → "UNC paths are not supported"
- `Read` na UNC → "outside session's connected folders"
- Sandbox bash stuck na UNC paths

**Pierwsza diagnoza:** sprawdź w Cowork UI w którym projekcie jesteś. Jeśli folder algo_bot nie jest w workspace folders tego projektu, sesja go nie widzi — to nie błąd UNC, tylko brak mountu.

**Rozwiązanie:**
1. **Najczęstsze (90%):** otworzyłeś sesję w niewłaściwym projekcie. Wróć do projektu `algo_bot` (lewy górny dropdown) i zacznij nową sesję tam. UNC folder jest do niego podpięty na stałe — natychmiast działa.
2. **Jeśli projekt algo_bot nie ma workspace folder UNC dodanego:** Project settings → Connect folder → wpisz `\\wsl.localhost\ubuntu\home\janek\quant_projects\algo_bot`. Patrz "Setup nowego projektu Cowork" wyżej.
3. **Tool `request_cowork_directory` w Claude'd nie umie dodać UNC** — to ograniczenie tego konkretnego tool calla, nie samego Cowork. Workspace foldery dodajesz **w UI Cowork**, nie przez Claude'a. Jeśli Claude próbuje wywołać `request_cowork_directory` z UNC i pada — zignoruj, dodaj ręcznie w UI.

**Fallback (rzadko potrzebny):** jeśli z jakiegoś powodu UNC nie działa nawet po prawidłowym setupie projektu — Claude pisze do `outputs/`, user kopiuje do repo ręcznie i commituje w WSL. Strata: brak strukturalnego edytowania (Read pokazuje stale state, Edit nie ma punktu odniesienia). Używać tylko jako emergency, nie jako stały workflow.

### Czego unikamy

- **Mieszanie writerów na repo** — w jednym momencie pisze albo sandbox Cowork, albo user w WSL terminalu, nigdy oba naraz. Inaczej dostajesz fałszywe zmiany w `git status` i merge conflicts na niezacommitowanej pracy.
- **Klonowanie repo w lokalizacji ze spacjami** (np. `~/Documents/Some Folder With Spaces/algo_bot/`) — niektóre toole pythonowe (TA-Lib build, conda activate scripts) miewają problemy ze spacjami. Czysta ścieżka `~/quant_projects/algo_bot` jest bezpieczna.
- **Commitowanie SSH key** — `.ssh/` powinno być w `.gitignore` (sprawdź). Klucz prywatny żyje w `~/.ssh/`, nie w repo.
- **Hardkodowane UNC paths w kodzie** — gdy edytujemy skrypt który ma ścieżkę typu `\\wsl.localhost\...` albo `C:\...`, audytujemy go i wymieniamy na portable ścieżki względne albo zmienne środowiskowe.
- **Pracowanie w projekcie "Strona internetowa digitalalchemy" nad algo_bot** — to są dwa różne repa z różnym kontekstem, project instructions i memory. Mieszanie ich daje rozjazd memory i wolniejszy kickoff każdej sesji.

---

*Wersja: 0.3 — 2026-05-21. Dokument żywy — aktualizujemy gdy rytm pracy się zmieni (np. wejście w Fazę 2 wymaga dorzucenia sekcji o sesjach research) albo gdy zmienimy setup techniczny (np. przejście na Docker container dla sandboxa).*
