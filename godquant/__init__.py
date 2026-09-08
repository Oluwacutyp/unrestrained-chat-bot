"""God Quant AI Developer — production-grade, self-improving, multi-agent system.

Termux-first: zero mandatory pip dependencies (stdlib only).
Optional accelerators: requests, numpy, yfinance, rich.
"""

__version__ = "3.1.0"
__author__ = "God Quant AI"

from godquant.config import GodQuantConfig, load_config

__all__ = ["GodQuantConfig", "load_config", "__version__"]
