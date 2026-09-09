"""Bridge proof tests: fake Telegram events → real handlers → replies.

Simulates 'a text was sent to my account' and asserts the bot answers.
No telethon install, no network, no Telegram account needed.
"""
import asyncio
import importlib.util
import json
import tempfile
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_bridge():
    path = Path(__file__).resolve().parent.parent / "bridges" / "telegram_userbot.py"
    spec = importlib.util.spec_from_file_location("tg_bridge_live", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- fakes ----------
class FakeSender:
    def __init__(self, id, username=None, first_name="Babe", bot=False):
        self.id = id
        self.username = username
        self.first_name = first_name
        self.bot = bot


class FakeEvent:
    """Quacks like telethon NewMessage event AND Message (catchup path)."""

    def __init__(self, text, sender, out=False, group=False, chat_id=None):
        self.raw_text = text
        self._sender = sender
        self.sender_id = sender.id
        self.chat_id = chat_id if chat_id is not None else sender.id
        self.is_group = group
        self.is_channel = False
        self.is_reply = False
        self.out = out
        self.date = datetime.now(timezone.utc)
        self.replies = []

    async def get_sender(self):
        return self._sender

    async def get_reply_message(self):
        raise AssertionError("no reply in fake")

    async def reply(self, text):
        self.replies.append(text)
        return text


class FakeAction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeClient:
    def __init__(self):
        self.sent = []  # (entity, text)
        self.read_acks = []
        self.dialogs = []

    def action(self, *a, **k):
        return FakeAction()

    async def send_message(self, entity, text):
        if entity == "boom":
            raise RuntimeError("send failed")
        self.sent.append((entity, text))

    async def iter_dialogs(self, limit=50):
        for d in self.dialogs[:limit]:
            yield d

    async def get_entity(self, dialog_id):
        for d in self.dialogs:
            if d.id == dialog_id:
                return d.entity
        raise ValueError("unknown")

    async def get_messages(self, entity, limit=1):
        for d in self.dialogs:
            if d.entity is entity:
                return d.messages[:limit]
        return []

    async def send_read_acknowledge(self, entity):
        self.read_acks.append(entity)


class FakeDialog:
    def __init__(self, id, entity, unread_count, messages,
                 group=False, channel=False):
        self.id = id
        self.entity = entity
        self.unread_count = unread_count
        self.messages = messages
        self.is_group = group
        self.is_channel = channel


class FakeMe:
    id = 999
    username = "myaccount"


def run(coro):
    return asyncio.run(coro)


# ---------- incoming DM → reply ----------
def test_dm_gets_brain_reply(monkeypatch):
    mod = _load_bridge()
    mod.ALLOW.clear()

    async def fake_chat_full(text, sid, display, use_search=False,
                             chat_id=None, is_group=False, image=""):
        assert sid == 42 and "hey" in text
        assert chat_id == sid and is_group is False  # DM: context == sender
        return {"response": "heeey babe!! 😍", "mood": "happy",
                "substance": 0.5}

    async def fake_api(path, payload=None, timeout=120):
        assert path == "/contacts"  # registration sighting
        return {}
    monkeypatch.setattr(mod, "chat_full", fake_chat_full)
    monkeypatch.setattr(mod, "api", fake_api)

    ev = FakeEvent("hey babe", FakeSender(42, "babe"))
    out = run(mod.handle_incoming(ev, FakeClient(), FakeMe()))
    assert out == "heeey babe!! 😍"
    assert ev.replies == ["heeey babe!! 😍"]


def test_contact_command_path(monkeypatch):
    mod = _load_bridge()
    mod.ALLOW.clear()

    async def fake_api(path, payload=None, timeout=120):
        if path.startswith("/mood"):
            return {"current": "happy", "level": 8}
        return {}
    monkeypatch.setattr(mod, "api", fake_api)
    ev = FakeEvent("!mood", FakeSender(42))
    out = run(mod.handle_incoming(ev, FakeClient(), FakeMe()))
    assert "happy" in out and ev.replies == [out]


def test_ignored_cases(monkeypatch):
    mod = _load_bridge()
    mod.ALLOW.clear()

    async def boom(*a, **k):
        raise AssertionError("brain must NOT be called")
    monkeypatch.setattr(mod, "api", boom)
    monkeypatch.setattr(mod, "chat_reply", boom)

    assert run(mod.handle_incoming(
        FakeEvent("hi", FakeSender(1, bot=True)), FakeClient(), FakeMe())) is None
    assert run(mod.handle_incoming(
        FakeEvent("   ", FakeSender(2)), FakeClient(), FakeMe())) is None
    assert run(mod.handle_incoming(
        FakeEvent("hi all", FakeSender(3), group=True), FakeClient(), FakeMe())) is None
    mod.ALLOW.update({"42"})
    assert run(mod.handle_incoming(
        FakeEvent("hi", FakeSender(7, "stranger")), FakeClient(), FakeMe())) is None


def test_group_mention_replies_when_enabled(monkeypatch):
    mod = _load_bridge()
    mod.ALLOW.clear()
    monkeypatch.setattr(mod, "GROUPS", True)
    async def fake_group_chat(t, s, d, use_search=False,
                              chat_id=None, is_group=False, image=""):
        assert chat_id == -100 and is_group is True  # group: shared context
        return {"response": "yo 👀", "mood": "neutral", "substance": 0.1}
    monkeypatch.setattr(mod, "chat_full", fake_group_chat)

    async def fake_api(path, payload=None, timeout=120):
        return {}
    monkeypatch.setattr(mod, "api", fake_api)
    ev = FakeEvent("hey @myaccount wyd", FakeSender(5), group=True, chat_id=-100)
    assert run(mod.handle_incoming(ev, FakeClient(), FakeMe())) == "yo 👀"


# ---------- owner commands ----------
def test_owner_tick_command(monkeypatch):
    mod = _load_bridge()

    async def fake_api(path, payload=None, timeout=120):
        assert path == "/tick"
        return {"queued": [{"channel": "telegram", "to": "42"}]}
    monkeypatch.setattr(mod, "api", fake_api)
    ev = FakeEvent(".tick", FakeSender(999), out=True)
    out = run(mod.handle_owner_message(ev))
    assert "telegram:42" in out and ev.replies == [out]


def test_owner_non_command_ignored():
    mod = _load_bridge()
    ev = FakeEvent("just a note to self", FakeSender(999), out=True)
    assert run(mod.handle_owner_message(ev)) is None
    assert ev.replies == []


# ---------- outbox delivery ----------
def test_deliver_outbox_sends_and_acks(monkeypatch):
    mod = _load_bridge()
    acks = []

    async def fake_api(path, payload=None, timeout=120):
        if path.startswith("/outbox"):
            return {"pending": [{"id": 1, "to": "me", "message": "morning ❤"},
                                {"id": 2, "to": "boom", "message": "x"}]}
        acks.append((payload["id"], payload["ok"]))
        return {}
    monkeypatch.setattr(mod, "api", fake_api)
    client = FakeClient()
    sent = run(mod.deliver_outbox(client))
    assert sent == ["me"] and client.sent == [("me", "morning ❤")]
    assert (1, True) in acks and (2, False) in acks  # fail nacked for retry


# ---------- catchup ----------
def test_catchup_replies_to_fresh_unread_only(monkeypatch):
    mod = _load_bridge()
    mod.ALLOW.clear()
    monkeypatch.setenv("TG_CATCHUP", "1")
    monkeypatch.setenv("TG_CATCHUP_MINS", "60")
    monkeypatch.setenv("TG_CATCHUP_MAX", "5")
    monkeypatch.setattr(mod, "chat_full",
                        lambda t, s, d, **kw: asyncio.sleep(
                            0, {"response": "back! ❤", "mood": "happy",
                                "substance": 0.2}))

    async def fake_api(path, payload=None, timeout=120):
        return {}
    monkeypatch.setattr(mod, "api", fake_api)

    fresh = FakeEvent("you there?", FakeSender(11))
    old = FakeEvent("ancient", FakeSender(12))
    old.date = datetime.now(timezone.utc) - timedelta(hours=5)
    client = FakeClient()
    client.dialogs = [
        FakeDialog(11, FakeSender(11), 2, [fresh]),
        FakeDialog(12, FakeSender(12), 1, [old]),
        FakeDialog(13, FakeSender(13, bot=True), 3,
                   [FakeEvent("spam", FakeSender(13, bot=True))]),
    ]
    done = run(mod.catchup_unread(client, FakeMe()))
    assert done == [11] and fresh.replies == ["back! ❤"] and old.replies == []


# ---------- whatsapp bridge self-consistency (static) ----------
def test_whatsapp_bridge_defines_everything_it_calls():
    import re
    src = (Path(__file__).resolve().parent.parent / "bridges"
           / "whatsapp.js").read_text()
    code = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)      # block comments
    code = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`",
                  "''", code)                                # strings FIRST
    code = re.sub(r"//[^\n]*", " ", code)                    # line comments
    # (strings before // so http:// URLs don't eat their own line)
    defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_]\w*)", code))
    defined |= set(re.findall(r"const\s+([A-Za-z_]\w*)\s*=", src))
    assert {"registerContact", "pollOutbox", "getAIResponse",
            "shouldRespond", "isSpamming"} <= defined
    calls = set()
    for m in re.finditer(r"(?<![.\w$])([A-Za-z_]\w*)\s*\(", code):
        calls.add(m.group(1))
    builtins = {"if", "for", "while", "switch", "catch", "function", "return",
                "require", "setTimeout", "parseInt", "console", "process",
                "setInterval", "Promise", "JSON", "Math", "Date", "isNaN",
                "Set", "Map", "Client", "LocalAuth", "async", "of", "new"}
    undefined = {c for c in calls - defined - builtins
                 if c not in ("log",) and not c.startswith("_")}
    # method calls on objects (client.on, axios.post...) were excluded by lookbehind;
    # remaining must be defined or builtins
    assert not undefined, f"undefined calls in whatsapp.js: {undefined}"


# ---------- full loop: bridge payload → live server → reply + side effects ----------
def test_full_server_loop_for_bridge_payload():
    from godquant.companion.companion import CompanionAgent
    from godquant.companion.server import create_server
    from godquant.agents.orchestrator import Orchestrator
    from godquant.config import GodQuantConfig
    from godquant.llm.router import LLMRouter
    from godquant.memory.store import MemoryStore

    tmp = Path(tempfile.mkdtemp())
    cfg = GodQuantConfig(offline=True, workspace=str(tmp / "ws"),
                         memory_db=str(tmp / "mem.db"))
    mem = MemoryStore(cfg.resolved_memory_db())
    router = LLMRouter(cfg, mem)
    srv = create_server(cfg, router, mem, Orchestrator(cfg, router, mem),
                        CompanionAgent(cfg, router, mem),
                        host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        # EXACTLY what chat_reply() posts when your account gets a DM:
        req = urllib.request.Request(
            base + "/chat",
            data=json.dumps({"message": "hey babe i miss you",
                             "conversation_id": "tg:42", "channel": "telegram",
                             "chat_id": "42", "display": "Babe",
                             "persona": "alex"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        assert data["response"] and data["mood"] in ("happy", "excited")
        # side effects the reply depends on:
        with urllib.request.urlopen(base + "/contacts", timeout=10) as r:
            contacts = json.loads(r.read().decode())["contacts"]
        assert any(c["chat_id"] == "42" for c in contacts)  # auto-registered
        with urllib.request.urlopen(base + "/mood?conversation_id=tg:42",
                                    timeout=10) as r:
            assert json.loads(r.read().decode())["current"] == data["mood"]
    finally:
        srv.shutdown()
        mem.close()
