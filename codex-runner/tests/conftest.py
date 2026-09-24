"""Select this runner explicitly when pytest starts at the repository root.

The repository also retains a legacy root run.py/src; it must not shadow the
shared playground under test.
"""
from pathlib import Path
import sys

RUNNER = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RUNNER), str(RUNNER / 'src')]
