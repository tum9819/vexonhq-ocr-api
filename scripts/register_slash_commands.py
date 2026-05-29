"""Register VEXONHQ Discord slash commands.

Idempotent: Discord upserts application commands by name. Re-run any
time you add a new command or change a description.

Required env (live mode only):
  DISCORD_APP_ID      from Discord Developer Portal → General Information
  DISCORD_BOT_TOKEN   from Bot tab → Reset Token (NOT the OAuth2 client secret)

Usage:
  python scripts/register_slash_commands.py            # live POST
  python scripts/register_slash_commands.py --dry-run  # print plan only
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

COMMANDS = [
    {
        "name": "resources",
        "description": (
            "Show VPS resource snapshot "
            "(CPU/RAM/disk/swap/scheduler/last deploy)"
        ),
        "type": 1,  # CHAT_INPUT
    },
    {
        "name": "help",
        "description": "List all Ops Bot commands, buttons, and auto messages",
        "type": 1,  # CHAT_INPUT
    },
]


def _post_command(app_id: str, token: str, cmd: dict) -> tuple[int, str]:
    url = (
        f"https://discord.com/api/v10/applications/{app_id}/commands"
    )
    body = json.dumps(cmd).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
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
            return 0, str(data.get("id", "?"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        return e.code, detail
    except urllib.error.URLError as e:
        # Network-down (DNS / connection refused / timeout). Surface a
        # clean message instead of letting a raw traceback escape and
        # break the documented 0/1/2 exit-code contract.
        return -1, f"network error: {e.reason}"


def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv

    if dry_run:
        print("Would register the following commands:")
        for c in COMMANDS:
            print(f"  /{c['name']} — {c['description']}")
        print(f"\nTotal: {len(COMMANDS)} command(s) (dry-run, no API call)")
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

    overall_rc = 0
    for c in COMMANDS:
        code, detail = _post_command(app_id, token, c)
        if code == 0:
            print(f"✅ Registered /{c['name']} (id={detail})")
        else:
            print(
                f"❌ /{c['name']}: HTTP {code} — {detail}",
                file=sys.stderr,
            )
            overall_rc = 1
    return overall_rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
