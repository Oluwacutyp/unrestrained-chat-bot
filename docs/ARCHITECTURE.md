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
- **v4.2 personal-model path ✅ shipped:** JSONL trajectory logging on
  every chat/mission; `.good`/`.bad` preference pairs; `gq.py
  export-training` (SFT + DPO packs + card); HF-native pipe
  (`hf_pipe`: dataset push to the user's account, one-file TRL SFT+LoRA
  →DPO script, Gradio Space scaffold). AutoTrain rejected (dead upstream);
  training runs off-phone, the *data asset* is collected on-phone.
- **v4.3 life-OS + creative ✅ shipped:** calendar/events (due-date
  fire via tick, daily repeats), spending ledger (per-cat totals),
  health log, world bibles (append/replace, injected as canon into the
  `/chat` system prompt via `bible=`), today's events in the brief.
- **v4.4 personal model ✅ shipped:** `/model` accepts `{primary?, model?,
  gguf?}` and persists via `save_config` (providers rebuild per call, so
  the switch is instant); `.model load|list` on all bridges; GGUF
  download accepts any `user/repo/file.gguf` (+`HF_TOKEN` auth); SFT
  export in messages + ShareGPT + Alpaca; `docs/colab_train.ipynb`
  (QLoRA SFT→DPO on free T4, pushes adapter + GGUF); author watermark
  `oluwacutyp / peacethefirst1` in CLI, dataset cards, notebook.
- **v4.5 history harvest ✅ shipped:** `bridges/tg_history.py` walks all
  dialogs (DMs/groups/channels) via the userbot session; pure pairing
  logic in `godquant/train/history.py` (sliding different-sender pairs,
  channel→Alpaca style rows, dedupe, per-chat resume state);
  `TrajectoryLogger.log_history` lands rows in `trajectories.jsonl` so
  export/push/Colab work unchanged. No ratings needed.
- **v4.6 social + research ✅ shipped:** `reddit_bridge.py` (PRAW inbox +
  outbox u/user|t1_|t3_ + `--backfill`), `x_bridge.py` (tweepy mentions +
  outbox, tier-honest), `discord_history.py` (guild harvest, resume
  state); `tools/research.py` (oEmbed/yt-transcript/article extract,
  DDG `research()` briefs) behind upgraded `web_fetch` + new
  `deep_research` tool + `/research` route + `.research` on both cores;
  `scripts/codegen_megabuild.py` + `codegen` tool, added to CoderAgent.
- **v4.6.1 persona hotfix ✅ shipped:** Devon prompt gains STREET SMARTS
  (sub/airtime/urgent-2k lexicon, broke-friend favor rules), FAVOR &
  REQUEST DISCIPLINE (answer what's asked, plans only on demand), FORMAT
  DISCIPLINE (no tables/headers/corporate lists, short texts, no assistant
  phrases); regression tests generated via the codegen megabuild.

## Reliability bar (every phase)

All bridges working, full suite green (156 pytest + 65 node at v4.0),
no feature removed, conventional commits, docs updated per phase.
