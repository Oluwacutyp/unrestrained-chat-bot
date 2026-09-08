#!/usr/bin/env bash
# God Quant AI Developer — Termux installer (also works on any Debian/Ubuntu).
set -e
echo "◈ God Quant installer"
if command -v pkg >/dev/null 2>&1; then
  pkg update -y && pkg install -y python git
else
  sudo apt-get update && sudo apt-get install -y python3 git
fi
PY=$(command -v python3 || command -v python)
$PY -m pip install --upgrade pip 2>/dev/null || true
echo "— optional accelerators (safe to skip on low storage) —"
$PY -m pip install requests rich pytest 2>/dev/null || echo "(skipped optionals — stdlib mode is fine)"
echo "— telegram userbot (recommended: your own account, replies + texts first) —"
$PY -m pip install telethon 2>/dev/null || echo "(skipped telethon — install later for the TG bridge)"
mkdir -p ~/.godquant workspace tests
$PY bot.py doctor
echo ""
echo "✓ Done. Try:"
echo "  $PY bot.py --offline partner \"hey babe i miss you\" --persona alex"
echo "  $PY bot.py serve            # brain + chat UI + proactive ticker"
echo "  python bridges/telegram_userbot.py   # needs TG_API_ID/TG_API_HASH"
echo ""
echo "For full AI power, add a free key:  export GQ_API_KEY=gsk_...   (Groq free tier)"
