"""Puts the repo root on sys.path so tests can import `config` and `core`.

pytest prepends the test file's own directory, not the project root, so without this
`import config` fails when running plain `pytest` from the repo root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
