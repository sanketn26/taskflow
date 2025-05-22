import sys
import os
from pathlib import Path

# Add the project root to Python's path so relative imports work from tests
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))
