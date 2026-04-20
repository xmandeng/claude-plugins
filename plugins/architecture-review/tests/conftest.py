"""Pytest configuration for architecture-review plugin tests.

Adds the plugin's `bin/` dir to sys.path so `import devserver` works without
an installed package.
"""

import sys
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
sys.path.insert(0, str(BIN_DIR))
