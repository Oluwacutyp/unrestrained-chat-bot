"""Central configuration. Sources (lowest to highest priority):
1. Defaults  2. ~/.godquant/config.json  3. ./.godquant.json  4. Environment vars
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def home_dir() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


def base_dir() -> Path:
    return home_dir() / ".godquant"


@dataclass
class GodQuantConfig:
    # --- LLM ---
    llm_provider: str = "auto"          # auto|openai|groq|anthropic|gemini|ollama|deepseek|openrouter|heuristic
    llm_model: str = ""                 # empty = provider default
    llm_base_url: str = ""              # override (Ollama, proxies)
    llm_api_key: str = ""               # or env GQ_API_KEY / provider keys
    llm_temperature: float = 0.3
    llm_max_tokens: int = 2000
    llm_timeout: int = 60
    # --- Quant ---
    default_symbol: str = "BTCUSDT"
    data_source: str = "auto"           # auto|binance|stooq|yfinance|synthetic
    initial_cash: float = 10_000.0
    commission: float = 0.001
    slippage: float = 0.0005
    max_risk_per_trade: float = 0.01
    # --- Agents ---
    max_workers: int = 4
    max_iterations: int = 3
    auto_apply_patches: bool = False
    workspace: str = ""                 # empty = ./workspace
    # --- Memory ---
    memory_db: str = ""                 # empty = ~/.godquant/memory.db
    # --- Companion (Partner fusion) ---
    persona: str = "alex"           # alex|companion|realistic|quant
    server_host: str = "0.0.0.0"
    server_port: int = 5000
    model_path: str = ""            # GGUF for llamacpp (empty = ~/.godquant/models/*.gguf)
    llamacpp_threads: int = 4
    llamacpp_ctx: int = 4096
    # --- Misc ---
    log_level: str = "INFO"
    offline: bool = False               # force heuristic provider + synthetic data

    def resolved_workspace(self) -> Path:
        p = Path(self.workspace) if self.workspace else Path.cwd() / "workspace"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def resolved_memory_db(self) -> Path:
        p = Path(self.memory_db) if self.memory_db else base_dir() / "memory.db"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


_ENV_MAP = {
    "GQ_PROVIDER": "llm_provider",
    "GQ_MODEL": "llm_model",
    "GQ_BASE_URL": "llm_base_url",
    "GQ_API_KEY": "llm_api_key",
    "GQ_TEMPERATURE": "llm_temperature",
    "GQ_MAX_TOKENS": "llm_max_tokens",
    "GQ_SYMBOL": "default_symbol",
    "GQ_DATA": "data_source",
    "GQ_CASH": "initial_cash",
    "GQ_WORKERS": "max_workers",
    "GQ_WORKSPACE": "workspace",
    "GQ_OFFLINE": "offline",
    "GQ_PERSONA": "persona",
    "GQ_PORT": "server_port",
    "GQ_MODEL_PATH": "model_path",
    "OPENAI_API_KEY": "llm_api_key",
    "GROQ_API_KEY": "llm_api_key",
    "ANTHROPIC_API_KEY": "llm_api_key",
    "GEMINI_API_KEY": "llm_api_key",
    "DEEPSEEK_API_KEY": "llm_api_key",
}


def _coerce(field_name: str, value: str):
    bools = {"offline", "auto_apply_patches"}
    ints = {"llm_max_tokens", "llm_timeout", "max_workers", "max_iterations",
            "server_port", "llamacpp_threads", "llamacpp_ctx"}
    floats = {"llm_temperature", "initial_cash", "commission", "slippage",
              "max_risk_per_trade", "max_risk_per_trade"}
    if field_name in bools:
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    if field_name in ints:
        return int(value)
    if field_name in floats:
        return float(value)
    return value


def load_config(cli_overrides: dict | None = None) -> GodQuantConfig:
    cfg = GodQuantConfig()
    for path in (base_dir() / "config.json", Path.cwd() / ".godquant.json"):
        try:
            if path.exists():
                data = json.loads(path.read_text())
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
        except Exception:
            pass
    for env, attr in _ENV_MAP.items():
        if env in os.environ and os.environ[env].strip():
            try:
                setattr(cfg, attr, _coerce(attr, os.environ[env]))
            except Exception:
                pass
    # provider-specific key fallback
    if not cfg.llm_api_key:
        for k in ("OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY",
                  "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
            if os.getenv(k):
                cfg.llm_api_key = os.environ[k]
                break
    if cli_overrides:
        for k, v in cli_overrides.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


def save_config(cfg: GodQuantConfig) -> Path:
    base_dir().mkdir(parents=True, exist_ok=True)
    p = base_dir() / "config.json"
    p.write_text(json.dumps(asdict(cfg), indent=2))
    return p
