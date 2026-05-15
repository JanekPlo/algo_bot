# tests/conftest.py
# Dodajemy katalog główny projektu do sys.path, aby importy z 'src' i 'strategies' działały
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
