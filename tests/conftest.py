import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Headless GUI tests build MainWindow many times; the first-run download prompt
# is exercised by its own test, never by the main window fixture.
os.environ.setdefault("AUDION_SETUP_PROMPT", "0")
