# ADR-NNN: <Tytuł decyzji w trybie deklaratywnym>

- **Status:** Proposed | Accepted | Deprecated | Superseded by ADR-XXX
- **Data:** YYYY-MM-DD
- **Faza projektu:** 0 (legacy) | 1 (Foundation) | 2 (Research) | 3 (Paper) | 4 (Live) | 5 (Production)
- **Autorzy:** <imię> [, <imię>, ...]

## Context

Co się dzieje, że trzeba podjąć decyzję. Opisz:
- Aktualny stan (co już jest, co działa, co nie działa)
- Problem do rozwiązania
- Ograniczenia techniczne, czasowe, ludzkie
- Czego się dowiedzieliśmy (np. z istniejących prób, literatury, prototypów)

3-6 akapitów. Jasno opisz tło — za rok ktoś (Ty) przeczyta to nie pamiętając kontekstu.

## Decision

Co konkretnie wybieramy. Konkretne, jednoznaczne stwierdzenia:

- Wybieramy X (konkretną bibliotekę/wzorzec/strukturę)
- Stosujemy konwencję Y
- Implementujemy interfejs Z w sygnaturze `def foo(a: int, b: str) -> Bar`

Krótkie i precyzyjne. Bez "może" / "rozważamy". Jeśli decyzja ma podelementy (jak ADR-002 z 4 wymiarami), wymień je all out.

## Consequences

Co się zmieni w wyniku decyzji.

**Pozytywne:**
- Konsekwencja 1
- Konsekwencja 2

**Negatywne / koszty:**
- Co trzeba dorobić
- Czego nie będziemy mogli zrobić łatwo
- Co podnosi złożoność

**Ryzyka:**
- Co może pójść nie tak
- Pod jakimi warunkami będziemy musieli zmienić decyzję

## Alternatives Considered

Jakie inne opcje były rozważane i dlaczego odrzucone. Format: **<nazwa opcji>** — krótki opis + powód odrzucenia.

- **Opcja B** — opis. Odrzucone bo: <powód>.
- **Opcja C** — opis. Odrzucone bo: <powód>.

Sekcja KLUCZOWA. Pokazuje że decyzja nie była przypadkowa, tylko świadomy wybór z N opcji.

## References

(Opcjonalnie) Linki do:
- Dyskusji (issue, PR, slack thread)
- Zewnętrznych źródeł (paper, blog post, dokumentacja)
- Powiązanych ADR-ów (poprzednie decyzje które kontekstują tę)

## Notes

(Opcjonalnie) Dodatkowe uwagi, edge cases, plany follow-up.
