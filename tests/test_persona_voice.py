"""Persona voice guardrails: Devon stays human, street-smart, un-corporate.

Static checks on the prompt contract (what CI can verify) plus the
relationship-block wiring that calibrates her per person.

Generated via scripts/codegen_megabuild.py — do not hand-edit; change the
spec and rebuild.
"""
from godquant.companion.personas import DEVON, render_persona
from godquant.companion.relationship import addressing


def _flat():
    return " ".join(DEVON.split())


def test_street_smarts_lexicon():
    assert '"sub" = airtime' in DEVON
    assert "urgent 2k" in DEVON


def test_favor_rules():
    flat = _flat()
    assert "NEVER send money, airtime, data" in flat
    assert "NEVER a request for a plan" in flat


def test_format_discipline():
    flat = _flat()
    assert "NEVER send markdown tables" in flat
    assert "Answer WHAT WAS ACTUALLY ASKED" in flat
    assert "You don't assist" in flat


def test_render_fills_identity_and_context():
    out = render_persona("devon", mood_context="tired",
                         history_summary="none")
    assert "Devon S.Kemp" in out
    assert "{identity}" not in out and "{mood_context}" not in out
    assert "tired" in out


def test_addressing_calibration():
    assert "NEVER use pet names" in addressing(0, "Zed", False, None)
    assert "intimate partner" in addressing(3, "Zed", False, None)
    assert "GROUP CHAT" in addressing(2, "Zed", True, None)
