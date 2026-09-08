#!/usr/bin/env python3
"""Unrestrained Chat Bot — alias entry point. Run: python bot.py --help"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from godquant.ui.cli import main

if __name__ == "__main__":
    main()
