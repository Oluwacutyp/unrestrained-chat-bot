"""Vision tests — generated via scripts/codegen_megabuild.py."""
import base64

import pytest

from godquant.llm import vision as V

JPEG = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 100).decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()


def test_clean_data_url_passthrough():
    u = f"data:image/jpeg;base64,{JPEG}"
    assert V.clean_image(u) == u
    assert V.clean_image("https://x.com/a.jpg") == "https://x.com/a.jpg"


def test_clean_raw_base64_sniffs_mime():
    assert V.clean_image(JPEG).startswith("data:image/jpeg;base64,")
    assert V.clean_image(PNG).startswith("data:image/png;base64,")
    with pytest.raises(ValueError):
        V.clean_image("!!!not-base64!!!")
    with pytest.raises(ValueError):
        V.clean_image(base64.b64encode(b"zzz-not-an-image").decode())


def test_clean_rejects_oversize(monkeypatch):
    monkeypatch.setattr(V, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        V.clean_image(JPEG)


def test_vision_model_map_and_override():
    assert V.vision_model_for("groq", "other") == \
        "meta-llama/llama-4-scout-17b-16e-instruct"
    assert V.vision_model_for("pollinations", "openai") == "openai"
    assert V.vision_model_for("groq", "x", override="u/m") == "u/m"


def test_user_parts_shape():
    parts = V.user_parts("hi", ["data:image/jpeg;base64,AAA"])
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["type"] == "image_url"
    assert V.describe_hint(2).startswith("[They sent 2 photo(s)")
