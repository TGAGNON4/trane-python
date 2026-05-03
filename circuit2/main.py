"""Shim: runs final/main.py using this directory's config.py."""

import importlib.util
import sys
from pathlib import Path

_here = Path(__file__).parent.resolve()
_final = _here.parent / "final"

# Insert circuit dir first so its config.py shadows final's, then final for everything else.
sys.path.insert(0, str(_final))
sys.path.insert(0, str(_here))

_spec = importlib.util.spec_from_file_location("_final_main", _final / "main.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

if __name__ == "__main__":
    _mod.main()
