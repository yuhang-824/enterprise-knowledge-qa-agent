"""Start the local UI without installing dependencies."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
os.chdir(root)
from knowledge_qa.web import main

if __name__ == "__main__": main()
