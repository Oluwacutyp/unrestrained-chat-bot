"""Persona pack tests — generated via scripts/codegen_megabuild.py."""
from godquant.companion import persona_pack as PP


def test_scaffold_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(PP, "pack_dir", lambda: tmp_path)
    p = PP.scaffold("Zed", blurb="test")
    assert p.exists()
    pack = PP.load_pack("zed")
    assert pack and pack["blurb"] == "test"
    assert PP.load_pack("nobody") is None


def test_render_merges_sections(tmp_path, monkeypatch):
    monkeypatch.setattr(PP, "pack_dir", lambda: tmp_path)
    (tmp_path / "zed.md").write_text(
        "blurb: t\n---\n\n# voice\ntalk soft\n\n# taboos\nno tables\n")
    out = PP.render_pack("zed", "BASE")
    assert out.startswith("BASE")
    assert "[VOICE PACK]\ntalk soft" in out
    assert "[TABOOS]\nno tables" in out
    assert PP.render_pack("ghost", "BASE") == "BASE"


def test_voice_drift_dedupe_and_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(PP, "pack_dir", lambda: tmp_path)
    monkeypatch.setattr(PP, "MAX_LEARNED_LINES", 3)
    assert PP.voice_drift("zed", "keep it short") is True
    assert PP.voice_drift("zed", "keep it short") is False
    assert PP.voice_drift("zed", "") is False
    PP.voice_drift("zed", "two")
    PP.voice_drift("zed", "three")
    PP.voice_drift("zed", "four")
    assert "keep it short" not in PP.read_learned("zed")
    assert "four" in PP.read_learned("zed")
