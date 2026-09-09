"""Local GGUF provider — plugs llama.cpp models into the LLM router.

Works wherever llama-cpp-python installs (PC, and Termux with enough storage).
Graceful everywhere: missing package/model yields a clear error caught by the
router's fallback chain (never a crash).

Termux note: a 7B Q4 model needs ~4GB storage + ~6GB RAM and is SLOW on phones.
Prefer 0.5–1.5B Q4 models on-device (see SMALL_MODELS) or use Groq/Ollama.
"""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

from godquant.config import base_dir
from godquant.llm.providers import BaseProvider, LLMResponse

log = logging.getLogger("godquant.local_llm")

SMALL_MODELS = {
    # phone-friendly GGUFs (Qwen2.5 instruct, small + capable)
    "qwen2.5-0.5b-q4": ("Qwen/Qwen2.5-0.5B-Instruct-GGUF",
                        "qwen2.5-0.5b-instruct-q4_k_m.gguf", "~400MB"),
    "qwen2.5-1.5b-q4": ("Qwen/Qwen2.5-1.5B-Instruct-GGUF",
                        "qwen2.5-1.5b-instruct-q4_k_m.gguf", "~1GB"),
    "dolphin-2.8-7b-q4": ("cognitivecomputations/dolphin-2.8-mistral-7B-GGUF",
                           "dolphin-2.8-mistral-7b.Q4_K_M.gguf", "~4.4GB (PC)"),
}

MODELS_DIR = base_dir() / "models"


class LlamaCppProvider(BaseProvider):
    name = "llamacpp"
    default_model = "local-gguf"

    def __init__(self, model_path: str = "", n_ctx: int = 4096,
                 n_threads: int = 4, n_batch: int = 512, **kw):
        super().__init__(**kw)
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_threads = n_threads
        self.n_batch = n_batch
        self._llm = None

    def _load(self):
        if self._llm is not None:
            return self._llm
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError as e:
            raise RuntimeError("llama-cpp-python not installed "
                               "(pip install llama-cpp-python)") from e
        path = self.model_path or str(MODELS_DIR / "model.gguf")
        if not Path(path).exists():
            raise RuntimeError(f"GGUF not found: {path}. "
                               f"Run: python gq.py models --download qwen2.5-0.5b-q4")
        log.info("loading GGUF %s (one-time, slow)...", path)
        self._llm = Llama(model_path=path, n_ctx=self.n_ctx,
                          n_threads=self.n_threads, n_batch=self.n_batch,
                          verbose=False)
        return self._llm

    def complete(self, system: str, user: str,
                       images: list | None = None) -> LLMResponse:
        if images:
            user += "\n[photo attached \u2014 local model can\u2019t view images]"
        llm = self._load()
        prompt = (f"<|im_start|>system\n{system}<|im_end|>\n"
                  f"<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n")
        out = llm(prompt, max_tokens=self.max_tokens,
                  temperature=self.temperature, top_p=0.95,
                  repeat_penalty=1.1,
                  stop=["<|im_end|>", "\nUser:", "\n\nUser:"])
        usage = out.get("usage", {})
        return LLMResponse(text=out["choices"][0]["text"].strip(),
                           model=self.model_path or self.default_model,
                           provider=self.name,
                           prompt_tokens=usage.get("prompt_tokens", 0),
                           completion_tokens=usage.get("completion_tokens", 0))


def download_model(key: str, dest_dir: str | Path = MODELS_DIR) -> Path:
    """Download a GGUF from Hugging Face with a progress line. Stdlib-only.

    key is a SMALL_MODELS preset or 'user/repo/file.gguf' for any public
    (or HF_TOKEN-authed) repo — e.g. your own Colab-trained model.
    """
    if key in SMALL_MODELS:
        repo, fname, size = SMALL_MODELS[key]
    elif key.count("/") == 2 and key.lower().endswith(".gguf"):
        repo, fname = key.rsplit("/", 1)
        size = "custom"
    else:
        raise ValueError(f"unknown model '{key}'. Available: "
                         f"{sorted(SMALL_MODELS)} or user/repo/file.gguf")
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / fname
    if target.exists():
        return target
    url = f"https://huggingface.co/{repo}/resolve/main/{fname}?download=true"
    log.info("downloading %s (%s)...", fname, size)
    import os as _os
    _tok = _os.environ.get("HF_TOKEN", "")
    _headers = {"User-Agent": "godquant/1.0"}
    if _tok:
        _headers["Authorization"] = f"Bearer {_tok}"
    req = urllib.request.Request(url, headers=_headers)
    with urllib.request.urlopen(req) as r, open(target, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r  {got / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)
    print()
    return target


def list_models() -> str:
    lines = []
    for key, (repo, fname, size) in SMALL_MODELS.items():
        have = "✓" if (MODELS_DIR / fname).exists() else " "
        lines.append(f"[{have}] {key:<18} {size:<14} {repo}/{fname}")
    lines.append("custom GGUF: bot.py models --download user/repo/file.gguf")
    lines.append(f"\nmodels dir: {MODELS_DIR}")
    return "\n".join(lines)
