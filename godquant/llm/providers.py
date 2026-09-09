"""LLM providers — stdlib-only HTTP (urllib), optional `requests` fast-path.

Supported: OpenAI, Groq, DeepSeek, OpenRouter (OpenAI-compatible),
Anthropic, Gemini, Ollama (native + /v1), Heuristic (offline, no key).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

try:
    import requests as _requests  # type: ignore
except Exception:
    _requests = None


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


def _http_post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    # Browser-like UA: Cloudflare-fronted APIs (Groq, Pollinations, HF router)
    # 403/1010 the stock `Python-urllib` / `python-requests` UAs on some networks.
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 10; Termux) godquant/3.1",
               "Accept": "application/json", **headers}
    data = json.dumps(payload).encode()
    if _requests is not None:
        r = _requests.post(url, json=payload, headers=headers, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
        try:
            return r.json()
        except Exception:
            raise RuntimeError(f"LLM bad JSON ({r.status_code}): {r.text[:300]}")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"LLM HTTP {e.code}: {body}") from e


class BaseProvider:
    name = "base"
    default_model = ""

    def __init__(self, api_key: str = "", model: str = "", base_url: str = "",
                 temperature: float = 0.3, max_tokens: int = 2000, timeout: int = 60):
        self.api_key = api_key or ""
        self.model = model or self.default_model
        self.base_url = (base_url or "").rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, system: str, user: str) -> LLMResponse:
        raise NotImplementedError


class OpenAICompatibleProvider(BaseProvider):
    """Covers OpenAI, Groq, DeepSeek, OpenRouter, Ollama-/v1, Together, etc."""
    name = "openai-compatible"
    default_model = "gpt-4o-mini"

    PROVIDER_DEFAULTS = {
        "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
        "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct"),
        "huggingface": ("https://router.huggingface.co/v1", "Qwen/Qwen2.5-7B-Instruct"),
        "pollinations": ("https://text.pollinations.ai/openai", "openai"),
        "ollama": ("http://localhost:11434/v1", "llama3.1"),
        "together": ("https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    }

    def __init__(self, provider: str = "openai", **kw):
        passed_model = kw.get("model", "")
        super().__init__(**kw)
        self.provider_kind = provider
        if not self.base_url or self.base_url in ("auto", ""):
            self.base_url = self.PROVIDER_DEFAULTS.get(provider, (self.PROVIDER_DEFAULTS["openai"][0], ""))[0]
        if not passed_model:  # check what was PASSED (base fills class default)
            self.model = self.PROVIDER_DEFAULTS.get(provider, ("", self.default_model))[1]
        self.name = provider

    def complete(self, system: str, user: str) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        out = _http_post(url, payload, headers, self.timeout)
        text = out["choices"][0]["message"]["content"] or ""
        usage = out.get("usage", {})
        return LLMResponse(text=text.strip(), model=self.model, provider=self.name,
                           prompt_tokens=usage.get("prompt_tokens", 0),
                           completion_tokens=usage.get("completion_tokens", 0))


class PollinationsProvider(OpenAICompatibleProvider):
    """Pollinations: keyless free tier, but the free model set rotates.

    Unless the user pins GQ_MODEL explicitly, the model is resolved live
    from /models (cached 24h) preferring known-free text models — so a
    "402 Payment Required" on a stale default heals itself.
    """

    MODELS_URL = "https://text.pollinations.ai/models"
    FREE_PREFS = ["openai-fast", "openai-roblox", "nova-fast", "mistral",
                  "deepseek", "qwen-coder", "kimi", "gemini-fast", "openai"]

    def __init__(self, **kw):
        self._explicit_model = bool(kw.get("model"))
        super().__init__("pollinations", **kw)

    @classmethod
    def _cache_path(cls):
        from godquant.config import base_dir
        return base_dir() / "pollinations_models.json"

    @classmethod
    def _pick(cls, names: list[str]) -> str:
        lower = {n.lower(): n for n in names if n}
        for pref in cls.FREE_PREFS:
            if pref in lower:
                return lower[pref]
            for key, orig in lower.items():
                if key.startswith(pref + "-") or key.startswith(pref + "_"):
                    return orig
        return names[0] if names else ""

    @classmethod
    def resolve_free_model(cls) -> str:
        import time as _t
        cp = cls._cache_path()
        try:
            if cp.exists() and _t.time() - cp.stat().st_mtime < 86400:
                pick = cls._pick(json.loads(cp.read_text()))
                if pick:
                    return pick
        except Exception:
            pass
        try:
            req = urllib.request.Request(
                cls.MODELS_URL,
                headers={"User-Agent": "godquant/3.1", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode())
            if isinstance(data, dict):
                data = data.get("models") or data.get("data") or []
            names = [m.get("name", "") for m in data
                     if isinstance(m, dict) and m.get("name")]
            pick = cls._pick(names)
            if pick:
                try:
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_text(json.dumps(names))
                except Exception:
                    pass
                return pick
        except Exception:
            pass
        return "openai"

    def complete(self, system: str, user: str) -> LLMResponse:
        if not self._explicit_model:
            try:
                self.model = self.resolve_free_model()
            except Exception:
                pass
        return super().complete(system, user)


class AnthropicProvider(BaseProvider):
    name = "anthropic"
    default_model = "claude-3-5-haiku-latest"

    def complete(self, system: str, user: str) -> LLMResponse:
        url = "https://api.anthropic.com/v1/messages"
        headers = {"Content-Type": "application/json",
                   "x-api-key": self.api_key,
                   "anthropic-version": "2023-06-01"}
        payload = {"model": self.model, "max_tokens": self.max_tokens,
                   "system": system,
                   "messages": [{"role": "user", "content": user}]}
        out = _http_post(url, payload, headers, self.timeout)
        blocks = out.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = out.get("usage", {})
        return LLMResponse(text=text.strip(), model=self.model, provider=self.name,
                           prompt_tokens=usage.get("input_tokens", 0),
                           completion_tokens=usage.get("output_tokens", 0))


class GeminiProvider(BaseProvider):
    name = "gemini"
    default_model = "gemini-2.0-flash"

    def complete(self, system: str, user: str) -> LLMResponse:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        payload = {"system_instruction": {"parts": [{"text": system}]},
                   "contents": [{"parts": [{"text": user}]}],
                   "generationConfig": {"temperature": self.temperature,
                                        "maxOutputTokens": self.max_tokens}}
        out = _http_post(url, payload, {"Content-Type": "application/json"}, self.timeout)
        parts = out["candidates"][0]["content"]["parts"]
        return LLMResponse(text="".join(p.get("text", "") for p in parts).strip(),
                           model=self.model, provider=self.name)


class OllamaNativeProvider(BaseProvider):
    name = "ollama"
    default_model = "llama3.1"

    def complete(self, system: str, user: str) -> LLMResponse:
        host = self.base_url or "http://localhost:11434"
        host = host[:-3] if host.endswith("/v1") else host
        url = f"{host}/api/generate"
        payload = {"model": self.model, "system": system, "prompt": user,
                   "stream": False,
                   "options": {"temperature": self.temperature,
                               "num_predict": self.max_tokens}}
        out = _http_post(url, payload, {"Content-Type": "application/json"},
                         max(self.timeout, 180))
        return LLMResponse(text=out.get("response", "").strip(),
                           model=self.model, provider=self.name,
                           prompt_tokens=out.get("prompt_eval_count", 0),
                           completion_tokens=out.get("eval_count", 0))


class HeuristicProvider(BaseProvider):
    """Offline fallback. Zero network. Template-driven quant/dev assistance.

    Used automatically when no API key exists or --offline is passed.
    Real quant math (indicators/backtests) still runs at full power locally.
    """
    name = "heuristic"
    default_model = "heuristic-v1"

    def complete(self, system: str, user: str) -> LLMResponse:
        import re as _re
        low = (system + "\n" + user).lower()

        def has(*words):
            return any(_re.search(r"\b" + _re.escape(w), low) for w in words)

        # companion personas route to a dedicated offline voice (never risk/code)
        if has("be uncensored", "be real. be human", "texting style",
               "current mood", "mood context", "history summary"):
            text = self._companion(user)
        elif "self-improvement judge" in low or '"lesson"' in low:
            text = self._judge(user)
        elif has("review", "critic", "audit"):
            text = self._review(user)
        elif has("backtest", "strategy", "strategies", "sharpe", "quant",
                 "sortino", "drawdown"):
            text = self._quant(user)
        elif has("risk", "position siz", "kelly", "var ", "value at risk"):
            text = self._risk(user)
        elif has("plan", "orchestrat", "decompos", "route"):
            text = self._plan(user)
        else:
            text = self._code(user)
        return LLMResponse(text=text, model=self.default_model, provider=self.name)

    def _judge(self, user: str) -> str:
        score = 60
        if "sharpe" in user.lower():
            import re as _re
            m = _re.search(r"sharpe\s+(-?[\d.]+)", user.lower())
            if m:
                try:
                    score = max(5, min(95, int(50 + float(m.group(1)) * 15)))
                except ValueError:
                    pass
        lesson = ("Prefer strategies validated on out-of-sample data with "
                  "transaction costs included; distrust single-metric optima.")
        if "error" in user.lower() or "fail" in user.lower():
            lesson = ("Reproduce failures in the sandbox first, then fix root "
                      "cause and add a regression test.")
            score = min(score, 45)
        return json.dumps({"score": score, "lesson": lesson,
                           "tags": ["heuristic", "offline"]})

    def _companion(self, user: str) -> str:
        import hashlib as _h
        import re as _re
        # surface any tool results the companion engine gathered
        tools = "\n".join(_re.findall(
            r"\[TOOL:[^\]]+\][^\[]*?(?=\n\[|\Z)", user, _re.S))
        low = user.lower()
        m = _re.search(r"\[mood:\s*([a-z]+)\s*(\d+)", low)
        mood, level = (m.group(1), int(m.group(2))) if m else ("neutral", 5)
        bank = {
            "hot": ["hey you \U0001F440 thinking about you rn... come over? \U0001F970",
                    "miss your hands on me \U0001F62E\u200D\U0001F4A8 when am i seeing you?",
                    "thinking dirty thoughts about you again \U0001F975 come fix it"],
            "mad": ["mhm. what's up? \U0001F644",
                    "yeah? what do you want? \U0001F644",
                    "k, i'm listening. make it quick \U0001F644"],
            "low": ["heyyy... rough day, coding's killing me \U0001F62E\u200D\U0001F4A8 wyd?",
                    "ugh, today drained me \U0001F62E\u200D\U0001F4A8 talk to me, distract me",
                    "tired af and buggy code... be nice to me? \U0001F97A"],
            "jel": ["oh?? \U0001F440 who exactly are we talking about rn...",
                    "excuse me?? who is SHE \U0001F440",
                    "oh we're talking about other people now? interesting \U0001F643"],
            "lov": ["miss you more \U0001F97A\u2764 tell me everything, how was your day?",
                    "aww love you too \U0001F62D\u2764 how's my favorite person?",
                    "you always know what to say \U0001F970\u2764 missed you sm"],
            "joy": ["heyyy!! \U0001F60D\U0001F495 so good to hear from you, what's up?!",
                    "OMGG hii!! \U0001F970 what's the good news?!",
                    "yesss my favorite notification \U0001F60D\U0001F495 wyd?!"],
            "mid": ["heyyy \U0001F60A what's good? talk to me \U0001F495",
                    "heyy, what's on your mind? \U0001F60A",
                    "oh hey! perfect timing, i needed a break \U0001F60A wyd?"],
        }
        if mood == "horny" and level >= 7:
            key = "hot"
        elif mood in ("annoyed", "angry"):
            key = "mad"
        elif mood in ("sad", "stressed", "tired"):
            key = "low"
        elif mood == "jealous":
            key = "jel"
        elif any(w in low for w in ("miss you", "love you")):
            key = "lov"
        elif mood in ("happy", "excited"):
            key = "joy"
        else:
            key = "mid"
        opts = bank[key]
        opener = opts[int(_h.md5(user.encode()).hexdigest(), 16) % len(opts)]
        # keyword hook: reference something THEY actually said (last user line)
        hook = ""
        for ln in reversed([x.strip() for x in user.split("\n") if x.strip()]):
            if not ln.startswith("Them:"):
                continue
            words = [w.strip(".,!?\"'").lower() for w in ln.split()]
            cands = [w for w in words if len(w) >= 6 and w.isalpha()
                     and w not in ("really", "because", "something", "though")]
            if cands:
                hook = f" (ngl '{cands[0]}' has me curious \U0001F440)"
            break
        # NOTE: no meta leak — offline replies stay in character.
        if tools.strip():
            return f"{opener}{hook}\n\n---\n{tools.strip()[:2500]}"
        return opener + hook

    def _code(self, user: str) -> str:
        return (
            "## Heuristic Developer (offline mode)\n\n"
            "No LLM API key is configured, so I generated a structured plan instead of "
            "free-form code. Set `GQ_API_KEY` (Groq has a free tier) for full generation.\n\n"
            f"### Request\n{user[:1500]}\n\n"
            "### Suggested implementation steps\n"
            "1. Write a failing test in `tests/` describing the behaviour.\n"
            "2. Implement the smallest pure-Python module under `workspace/`.\n"
            "3. Run `python gq.py test` then `python gq.py review --path workspace/`.\n"
            "4. Iterate with `python gq.py improve --runs 3`.\n\n"
            "### Starter template\n```python\n"
            "def solve(*args, **kwargs):\n"
            '    """TODO: implement. Keep functions pure and typed."""\n'
            "    raise NotImplementedError\n```"
        )

    def _quant(self, user: str) -> str:
        return (
            "## Heuristic Quant (offline mode)\n\n"
            f"### Request\n{user[:1200]}\n\n"
            "### Recommended pipeline (runs fully offline)\n"
            "1. `python gq.py backtest --strategy sma_cross --symbol BTCUSDT`\n"
            "2. `python gq.py optimize --strategy rsi_meanrev --metric sharpe`\n"
            "3. `python gq.py risk --equity 10000 --risk 0.01`\n\n"
            "Built-in strategies: `sma_cross`, `ema_macd`, `rsi_meanrev`, "
            "`bollinger_breakout`, `donchian_trend`. Local indicators + backtester "
            "are exact — only the narrative layer is heuristic until you add a key."
        )

    def _risk(self, user: str) -> str:
        return (
            "## Heuristic Risk (offline mode)\n\n"
            f"### Request\n{user[:1200]}\n\n"
            "- Fixed-fractional: risk ≤ 1% equity per trade.\n"
            "- Kelly: use half-Kelly of the estimated edge, cap at 2%.\n"
            "- Portfolio heat: sum of open-trade risk ≤ 6%.\n"
            "- Kill-switch: halt after -3% day / -10% month.\n"
            "Run `python gq.py risk --equity <E> --risk 0.01` for exact sizing."
        )

    def _review(self, user: str) -> str:
        return (
            "## Heuristic Review (offline mode)\n\n"
            "Checklist applied to the submission:\n"
            "- [ ] No bare `except:` / silent failures\n"
            "- [ ] No hardcoded secrets or absolute paths\n"
            "- [ ] Pure functions typed; I/O isolated at edges\n"
            "- [ ] Edge cases: empty data, NaN, lookahead bias in backtests\n"
            "- [ ] Tests cover happy path + one failure path\n\n"
            f"### Input excerpt\n{user[:1500]}\n\n"
            "Verdict: NEEDS-HUMAN-REVIEW (heuristic mode cannot approve)."
        )

    def _plan(self, user: str) -> str:
        return (
            "## Heuristic Plan (offline mode)\n\n"
            f"### Goal\n{user[:1200]}\n\n"
            "### Task graph\n"
            "1. researcher → gather facts/constraints\n"
            "2. quant/coder → produce artifact (strategy/code)\n"
            "3. backtest/sandbox → verify empirically\n"
            "4. risk/reviewer → gate on safety + quality\n"
            "5. evolver → store lesson, propose next iteration"
        )


def key_for(kind: str, cfg=None) -> str:
    """Resolve the API key for a provider kind: own env var → cfg key → ''."""
    env_vars = {
        "openai": ["OPENAI_API_KEY"],
        "groq": ["GROQ_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
        "together": ["TOGETHER_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "huggingface": ["HF_TOKEN", "HUGGINGFACE_HUB_TOKEN"],
        "hf": ["HF_TOKEN", "HUGGINGFACE_HUB_TOKEN"],
    }
    for var in env_vars.get(kind, []):
        if os.environ.get(var):
            return os.environ[var]
    if cfg is not None and getattr(cfg, "llm_api_key", ""):
        return cfg.llm_api_key
    return ""


def detect_provider(cfg) -> str:
    """Resolve 'auto' → concrete provider based on env/keys."""
    if cfg.offline:
        return "heuristic"
    if cfg.llm_provider != "auto":
        return cfg.llm_provider
    key = cfg.llm_api_key or ""
    # explicit env vars win (even when cfg key is empty — setup wizard flow)
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN"):
        return "huggingface"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if not key:
        if os.environ.get("OLLAMA_HOST"):
            return "ollama"
        return "heuristic"
    # guess from key shape
    if key.startswith("sk-ant"):
        return "anthropic"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("gsk_"):
        return "groq"
    if key.startswith("hf_"):
        return "huggingface"
    return "openai"


def get_provider(cfg) -> BaseProvider:
    kind = detect_provider(cfg)
    kw = dict(api_key=cfg.llm_api_key, model=cfg.llm_model,
              base_url=cfg.llm_base_url, temperature=cfg.llm_temperature,
              max_tokens=cfg.llm_max_tokens, timeout=cfg.llm_timeout)
    if kind == "anthropic":
        return AnthropicProvider(**kw)
    if kind == "gemini":
        return GeminiProvider(**kw)
    if kind == "ollama":
        if (cfg.llm_base_url or "").endswith("/v1"):
            return OpenAICompatibleProvider("ollama", **kw)
        return OllamaNativeProvider(**kw)
    if kind == "heuristic":
        return HeuristicProvider(**kw)
    if kind == "llamacpp":
        from godquant.companion.local_llm import LlamaCppProvider  # lazy: no cycle
        return LlamaCppProvider(model_path=getattr(cfg, "model_path", ""),
                                n_ctx=getattr(cfg, "llamacpp_ctx", 4096),
                                n_threads=getattr(cfg, "llamacpp_threads", 4),
                                **kw)
    if kind == "hf":
        kind = "huggingface"
    if kind == "pollinations":
        return PollinationsProvider(**kw)
    if kind in ("openai", "groq", "deepseek", "openrouter", "together",
                "huggingface"):
        return OpenAICompatibleProvider(kind, **kw)
    return HeuristicProvider(**kw)
