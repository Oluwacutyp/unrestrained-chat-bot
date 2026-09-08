"""Provider registry, key resolution, fallback chain. Offline (mocked)."""
import os

from godquant.config import GodQuantConfig, load_config
from godquant.llm import router as router_mod
from godquant.llm.providers import (LLMResponse, OpenAICompatibleProvider,
                                    detect_provider, get_provider, key_for)
from godquant.llm.router import LLMRouter


def test_new_provider_defaults():
    hf = OpenAICompatibleProvider("huggingface")
    assert hf.base_url == "https://router.huggingface.co/v1"
    assert "Qwen" in hf.model
    po = OpenAICompatibleProvider("pollinations")
    assert po.base_url == "https://text.pollinations.ai/openai"
    assert po.model == "openai"


def test_hf_alias_and_factory():
    cfg = GodQuantConfig(llm_provider="hf", llm_api_key="hf_x")
    assert isinstance(get_provider(cfg), OpenAICompatibleProvider)
    assert get_provider(cfg).base_url == "https://router.huggingface.co/v1"


def test_key_resolution(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_abc")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_abc")
    assert key_for("huggingface") == "hf_abc"
    assert key_for("groq") == "gsk_abc"
    assert key_for("pollinations") == ""  # no key needed
    cfg = GodQuantConfig(llm_api_key="generic")
    assert key_for("openai", cfg) == "generic" or os.environ.get("OPENAI_API_KEY")


def test_detect_huggingface(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_abc")
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    cfg = GodQuantConfig(llm_provider="auto", llm_api_key="")
    assert detect_provider(cfg) == "huggingface"


def test_chain_building():
    cfg = GodQuantConfig(llm_provider="groq", llm_api_key="x",
                         llm_fallbacks=["huggingface", "pollinations"])
    assert LLMRouter(cfg, None).chain() == ["groq", "huggingface",
                                            "pollinations", "heuristic"]
    cfg2 = GodQuantConfig(offline=True)
    assert LLMRouter(cfg2, None).chain() == ["heuristic"]


class _Fake:
    def __init__(self, name, fail=False):
        self.name = name
        self.fail = fail

    def complete(self, system, user):
        if self.fail:
            raise RuntimeError("boom")
        return LLMResponse(text="ok", model="fake", provider=self.name)


def test_fallback_executes_in_order(monkeypatch):
    calls = []

    def fake_build(self, kind):
        calls.append(kind)
        return _Fake(kind, fail=(kind == "groq"))

    monkeypatch.setattr(LLMRouter, "_build", fake_build)
    cfg = GodQuantConfig(llm_provider="groq", llm_api_key="x",
                         llm_fallbacks=["pollinations"])
    resp = LLMRouter(cfg, None).complete("s", "u")
    assert resp.provider == "pollinations"
    assert calls == ["groq", "pollinations"]


def test_total_failure_lands_on_heuristic(monkeypatch):
    def always_fail(self, kind):
        if kind == "heuristic":
            return _Fake("heuristic")
        return _Fake(kind, fail=True)

    monkeypatch.setattr(LLMRouter, "_build", always_fail)
    cfg = GodQuantConfig(llm_provider="groq", llm_api_key="x")
    assert LLMRouter(cfg, None).complete("s", "u").provider == "heuristic"


def test_fallbacks_env_parsing(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GQ_FALLBACKS", "HuggingFace, pollinations")
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.llm_fallbacks == ["huggingface", "pollinations"]
