# Persona packs — personas as data, not code

Built-in prompts (`godquant/companion/personas.py`) are the **reference**.
A pack here **overrides and extends** them live — no restart, no code edit.

## Format (`personas/<name>.md`)

```
blurb: one-line description
---

# identity
Extra identity lines (job change, new city, new facts).

# voice
How she talks now (short texts, more pidgin, colder mornings...).

# taboos
Things she must never do (no tables, no pet names for strangers...).
```

Only `# identity` / `# voice` / `# taboos` sections are read.

## Self-development

- `.persona create <name>` scaffolds a pack; `.persona show <name>` reads it.
- Every `.good` verdict appends a style note to `<name>.learned.md`
  (deduped, capped at 40 lines) — the bot literally writes notes about
  what you loved, and they merge into its system prompt on every chat.
- Delete a pack (or the `.learned.md`) to reset to the built-in reference.
