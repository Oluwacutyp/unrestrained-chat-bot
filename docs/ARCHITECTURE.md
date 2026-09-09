# God-tier architecture (v4.x plan — approved, building phase by phase)

Phone-first (Termux): everything runs on-device today. Seams are left open
for bigger iron (Chroma/LanceDB, GPU fine-tuning) behind stable interfaces.

## Memory (v4.0 ✅ shipped)

```
chat/bridges → CompanionAgent.chat()
                    ├─ BondStore  (relationship: score/friction/streaks, facts,
                    │              reminders, intentions, journal, notes)
                    ├─ MemoryStore (SQLite: memories v2 [layer/scope/
                    │              importance/confidence/pinned/access],
                    │              episodes, working-memory, costs)
                    └─ Mind        (facade: remember/recall/episodes/
                                   pin/edit/forget/inspect)
Recall = BM25 (godquant/memory/bm25.py) + pinned/importance/recency
priors + access tracking. Vector rerank hook ready (embedding column
fits the same API; SqliteVectorIndex/Chroma later).
```

- **Scopes:** `global` | `owner` | `channel:chat`. Chats see global + own
  scope only; `*` is owner-only. Chats can never leak by construction,
  and `test_facts_never_cross_chats` + `test_recall_finds_and_isolates`
  guard it.
- **Dream v2** (`dream.consolidate_global`, runs in `/dream` + nightly
  tick): decay untouched, prune trivial (cap 50), near-dupe merge
  (trigram > 0.92), cross-chat `name=` link *proposals* (never auto-merge).
- **Ops:** `GET /recall`, `GET|POST /memories`, owner `.recall` / `.mem`.
- Old DBs auto-migrate (PRAGMA-checked ALTERs, covered by tests).

## Next phases (approved)

- **v4.1 tools + autonomy ✅ shipped:** `Tool` registry (schemas, safety
  levels, audit log) replacing regex tools; missions v2 (persistent,
  resumable, sub-agents, tick progress, self-critique gate); fixed
  `depends_on` (topological waves) in the orchestrator fan-out.
- **v4.2 personal-model path:** JSONL trajectory logging on every
  chat/mission; `.good`/`.bad` preference pairs; `gq export-training`
  (SFT + DPO packs); one-command LoRA script (RunPod/Colab) → GGUF →
  existing `llamacpp` provider. Training happens off-phone; the *data
  asset* is collected on-phone starting now.
- **v4.3 life-OS + creative:** calendar, finance/health logs, project
  tracking, world bibles for long-running creative consistency.

## Reliability bar (every phase)

All bridges working, full suite green (156 pytest + 65 node at v4.0),
no feature removed, conventional commits, docs updated per phase.
