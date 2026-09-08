#!/usr/bin/env python3
"""God Quant AI Developer — entry point. Run: python gq.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from godquant.ui.cli import main

if __name__ == "__main__":
    main()
