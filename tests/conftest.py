# tests/conftest.py
# Po decyzji A (flatten + algo_bot package) sys.path hack nie jest potrzebny —
# pakiet algo_bot jest importowalny po `pip install -e .` (faza 1, decyzja B).
# Plik zostawiamy pusty: pytest go automatycznie zaladuje gdy bedziemy chcieli
# dodac shared fixtures.
