#!/usr/bin/env bash
# Live-update the bot on Termux (or anywhere): stop → pull → restart.
# Run from the repo root:  bash update.sh
# Your login session, memory, and bonds all survive (they live outside git).
set -u
cd "$(dirname "$0")"

_kill() { # _kill PATTERN — pkill if present, else /proc scan (Termux-safe)
    if command -v pkill >/dev/null 2>&1; then
        pkill -f "$1" 2>/dev/null
    else
        for p in /proc/[0-9]*; do
            if tr '\0' ' ' < "$p/cmdline" 2>/dev/null | grep -q "$1"; then
                kill "${p#/proc/}" 2>/dev/null
            fi
        done
    fi
}

_run() { # _run LOG CMD... — background a process, keep it after we exit
    local log="$1"; shift
    if command -v nohup >/dev/null 2>&1; then
        nohup "$@" > "$log" 2>&1 &
    else
        "$@" > "$log" 2>&1 &
        disown 2>/dev/null || true
    fi
}

echo "◈ stopping brain + userbot…"
_kill "bot.py serve"
_kill "telegram_userbot"
sleep 2

echo "◈ pulling…"
if ! git pull --ff-only; then
    echo "(!) local changes in the way — stashing, then pulling"
    git stash push -m "update.sh auto-stash"
    git pull --ff-only
fi

echo "◈ restarting brain…"
_run brain.log python bot.py serve
sleep 4
echo "◈ restarting userbot…"
_run userbot.log python bridges/telegram_userbot.py
sleep 4

echo "── brain.log ──"; tail -4 brain.log 2>/dev/null || echo "(no log yet)"
echo "── userbot.log ──"; tail -4 userbot.log 2>/dev/null || echo "(no log yet)"
echo "◈ done — if a log shows an error above, re-run that command in the foreground to see it."
