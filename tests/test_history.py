"""v4.5 history harvest: pairing, channel rows, state, export flow."""
import json
import py_compile
import tempfile
from pathlib import Path

from godquant.train.collector import TrajectoryLogger
from godquant.train.export import build_packs
from godquant.train.history import (channel_rows, dedupe, load_state,
                                    pair_messages, save_state)

ROOT = Path(__file__).resolve().parent.parent


def _m(sender, text, ts=0):
    return {"sender": sender, "text": text, "ts": ts}


def test_pair_messages_basic_flow():
    msgs = [_m("a", "hey there"), _m("b", "yo! what's up"),
            _m("a", "nm, you?"), _m("b", "same here")]
    rows = pair_messages(msgs)
    assert [(u, a) for u, a, _ in rows] == [
        ("hey there", "yo! what's up"), ("nm, you?", "same here")]


def test_pair_messages_same_sender_latest_wins_and_skips():
    msgs = [_m("a", "one two"), _m("a", "three four"), _m("b", "reply here"),
            _m("b", "x"), _m("a", ""), _m("a", "ok Tylor")]
    rows = pair_messages(msgs)
    assert [(u, a) for u, a, _ in rows] == [("three four", "reply here")]


def test_channel_rows_instruction_shape():
    rows = channel_rows(["short", "x" * 100], "My Chan")
    assert len(rows) == 1
    u, a, _ = rows[0]
    assert "My Chan" in u and a == "x" * 100


def test_dedupe_and_state_roundtrip():
    rows = [("u", "a", 1.0), ("u", "a", 2.0), ("u", "b", 3.0)]
    assert len(dedupe(rows)) == 2
    ws = Path(tempfile.mkdtemp())
    assert load_state(ws) == {}
    save_state(ws, {"42": 99})
    assert load_state(ws) == {"42": 99}


def test_history_rows_flow_into_export():
    ws = Path(tempfile.mkdtemp())
    TrajectoryLogger(ws).log_history("hey there", "yo! what's up",
                                     chat="Zed", chat_type="dm")
    out = build_packs(ws)
    assert out["sft"] == 1
    row = json.loads((ws / "train" / "sft.jsonl").read_text().splitlines()[0])
    assert row["messages"][1]["content"] == "hey there"
    assert row["messages"][2]["content"] == "yo! what's up"


def test_harvest_script_compiles():
    py_compile.compile(str(ROOT / "bridges" / "tg_history.py"), doraise=True)
