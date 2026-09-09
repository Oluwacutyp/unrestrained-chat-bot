"""Vision plumbing: image normalize + multimodal payloads. No heavy deps.

Phones send JPEG/PNG/WebP/GIF; OpenAI-compatible providers want data-URL
image_url parts. No PIL on Termux, so we validate + cap size, never
transcode. Unknown providers keep their model — the router's existing
try-next fallback absorbs 400s.
"""
from __future__ import annotations

import base64

MAX_IMAGE_BYTES = 4 * 1024 * 1024

# Provider -> vision-capable default (env GQ_VISION_MODEL overrides all).
VISION_DEFAULTS = {
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai": "gpt-4o-mini",
    "openrouter": "meta-llama/llama-4-scout-17b-16e-instruct",
    "together": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
}


def _mime(raw: bytes) -> str:
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


def clean_image(data: str) -> str:
    """Normalize to data-URL (or http(s) passthrough). Raises ValueError."""
    data = (data or "").strip()
    if data.startswith(("http://", "https://")):
        return data
    if data.startswith("data:image/") and ";base64," in data:
        head, _, b64 = data.partition(";base64,")
        try:
            raw = base64.b64decode(b64, validate=True)
        except Exception:
            raise ValueError("bad base64 image")
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("image too large (4MB max)")
        if not raw:
            raise ValueError("empty image")
        return f"{head};base64,{b64}"
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        raise ValueError("need data-URL, base64, or http(s) image")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image too large (4MB max)")
    mime = _mime(raw)
    if not mime:
        raise ValueError("unknown image format (jpeg/png/webp/gif only)")
    return f"data:{mime};base64,{data}"


def vision_model_for(provider: str, current: str, override: str = "") -> str:
    if override:
        return override
    return VISION_DEFAULTS.get((provider or "").lower(), current)


def user_parts(text: str, images: list[str]) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": text or ""}]
    for img in images or []:
        parts.append({"type": "image_url", "image_url": {"url": img}})
    return parts


def describe_hint(n: int) -> str:
    return (f"[They sent {n} photo(s) — they are attached to this message, "
            f"look at them. React like a human: notice details, match the "
            f"vibe, never say you can't see images.]")
