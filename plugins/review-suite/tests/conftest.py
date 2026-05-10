"""Shared pytest configuration for plan-review tests.

Makes `bin/devserver.py` importable as a module (`devserver`) even though
`bin/` is not a Python package and the file is normally executed as a script.
"""

import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
