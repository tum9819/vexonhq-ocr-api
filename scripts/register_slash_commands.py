"""Register VEXONHQ Discord slash commands.

Uses Discord's BULK OVERWRITE endpoint
(PUT /applications/{id}/commands) so the COMMANDS list below is the
canonical source of truth — any command no longer in the list is
DELETED from Discord on the next run. This avoids the "old /resources
+ /help linger forever after rename" hazard.

Required env (live mode only):
  DISCORD_APP_ID      from Discord Developer Portal → General Information
  DISCORD_BOT_TOKEN   from Bot tab → Reset Token (NOT the OAuth2 client secret)

Usage:
  python scripts/register_slash_commands.py            # live PUT
  python scripts/register_slash_commands.py --dry-run  # print plan only

Subcommand design note: one top-level `/vex` namespace with
subcommands underneath (e.g. `/vex resources`, `/vex help`) so this
bot's commands don't collide with other bots sharing the same Discord
server (Sentry, GitHub, etc. all register their own `/help`). One
top-level command per bot is the recommended pattern when multiple
bots co-exist.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Discord application command option types
_OPT_SUB_COMMAND = 1

COMMANDS = [
    {
        "name": "vex",
        "description": "VEXONHQ Ops Bot — VPS snapshot, help, future ops actions",
        "type": 1,  # CHAT_INPUT
        "options": [
            {
                "name": "resources",
                "description": (
                    "Show VPS resource snapshot "
                    "(CPU/RAM/disk/swap/scheduler/last deploy)"
                ),
                "type": _OPT_SUB_COMMAND,
            },
            {
                "name": "help",
                "description": (
                    "List all Ops Bot commands, buttons, and auto messages"
                ),
                "type": _OPT_SUB_COMMAND,
            },
        ],
    },
]


def _bulk_overwrite(app_id: str, token: str, commands: list[dict]) -> tuple[int, str]:
    """PUT the entire command list — Discord deletes anything not in body."""
    url = f"https://discord.com/api/v10/applications/{app_id}/commands"
    body = json.dumps(commands).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            # Discord's Cloudflare returns 1010 (browser-signature ban)
            # to default Python-urllib UA. Match the UA already used by
            # discord_interactions.py so the script behaves like the
            # rest of the bot integration.
            "User-Agent": "VEXONHQ-OpsBot (vexonhq.com, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
            data = json.loads(text)
            # Bulk overwrite returns an array of command objects
            ids = [str(c.get("id", "?")) for c in data] if isinstance(data, list) else []
            return 0, ",".join(ids)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        return e.code, detail
    except urllib.error.URLError as e:
        # Network-down (DNS / connection refused / timeout). Surface a
        # clean message instead of letting a raw traceback escape and
        # break the documented 0/1/2 exit-code contract.
        return -1, f"network error: {e.reason}"


def _describe_command(c: dict) -> list[str]:
    """Render one COMMANDS entry as 1-or-more printable lines for --dry-run."""
    lines = [f"  /{c['name']} — {c['description']}"]
    for opt in c.get("options", []) or []:
        if opt.get("type") == _OPT_SUB_COMMAND:
            lines.append(f"     /{c['name']} {opt['name']} — {opt['description']}")
    return lines


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv

    if dry_run:
        print("Would PUT (bulk overwrite) the following commands:")
        for c in COMMANDS:
            for line in _describe_command(c):
                print(line)
        print(
            f"\nTotal: {len(COMMANDS)} top-level command(s) "
            f"(dry-run, no API call)"
        )
        return 0

    app_id = os.environ.get("DISCORD_APP_ID", "").strip()
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    missing = [k for k, v in (
        ("DISCORD_APP_ID", app_id), ("DISCORD_BOT_TOKEN", token),
    ) if not v]
    if missing:
        print(
            "ERROR: required env vars missing: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    code, detail = _bulk_overwrite(app_id, token, COMMANDS)
    if code == 0:
        names = ", ".join(f"/{c['name']}" for c in COMMANDS)
        print(f"✅ Bulk-overwrote {len(COMMANDS)} command(s): {names} (ids={detail})")
        return 0
    print(
        f"❌ bulk overwrite failed: HTTP {code} — {detail}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
