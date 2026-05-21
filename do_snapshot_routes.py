"""
DigitalOcean weekly auto-snapshot rotation (P2.4, Session 31).

Per CLAUDE.md rule 9, we keep at most 3 DO snapshots on the
`vexonhq-core` droplet:
  1. `vexonhq-clean-base` — pristine OS image, NEVER deleted
  2. `vexonhq-<previous-stable>` or `vexonhq-session<N>-complete-*` —
     manually managed by TUM around stable sessions
  3. `vexonhq-auto-YYYY-MM-DD` — managed by THIS module, weekly

Rotation policy (this module):
  - Weekly Sun 03:00 Asia/Bangkok, create a new `vexonhq-auto-YYYY-MM-DD`
  - After create, delete the oldest `vexonhq-auto-*` snapshots until at
    most `DO_SNAPSHOT_MAX_KEEP` (default 1) remain.
  - NEVER touches `vexonhq-clean-base` or `vexonhq-session*-*` —
    those are TUM's manual slots.

Cost ceiling at default config: 1 clean-base (~30 GB) + 1 manual
(~30 GB) + 1 auto (~30 GB) = 90 GB × $0.06/GB/mo = $5.40/mo, within
the CLAUDE.md rule 9 cost cap.

Endpoints (registered on the FastAPI app):
  GET /snapshots/status?secret=<ALERTS_WEBHOOK_SECRET>
        Lists current snapshots on the droplet — read-only sanity
        check that the DO API token still works.
  GET /snapshots/auto-rotate?secret=<ALERTS_WEBHOOK_SECRET>
        Manually trigger the same rotation that the weekly cron runs.
        Useful for verifying setup without waiting for Sunday.

Env vars (gracefully no-op if missing):
  DO_API_TOKEN            DigitalOcean Personal Access Token (read+write).
                          Create at cloud.digitalocean.com/account/api/tokens.
                          Scopes needed: droplet:read, droplet:create,
                          image:read, image:delete.
  DO_DROPLET_NAME         default "vexonhq-core" — name of the droplet
                          we snapshot (must match exactly).
  DO_SNAPSHOT_PREFIX      default "vexonhq-auto-" — auto-rotated
                          snapshots are named "<prefix>YYYY-MM-DD" and
                          only snapshots matching this prefix are
                          considered for deletion.
  DO_SNAPSHOT_MAX_KEEP    default "1" — how many auto-prefixed snapshots
                          to retain after rotation. Bump to 2 for
                          extra safety (costs +~$1.80/mo per slot).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

import discord_interactions as _di

log = logging.getLogger("do_snapshot_routes")

router = APIRouter(prefix="/snapshots", tags=["snapshots", "do"])

ALERTS_WEBHOOK_SECRET = os.environ.get("ALERTS_WEBHOOK_SECRET", "")
DO_API_TOKEN = os.environ.get("DO_API_TOKEN", "")
DO_DROPLET_NAME = os.environ.get("DO_DROPLET_NAME", "vexonhq-core")
DO_SNAPSHOT_PREFIX = os.environ.get("DO_SNAPSHOT_PREFIX", "vexonhq-auto-")
try:
    DO_SNAPSHOT_MAX_KEEP = max(1, int(os.environ.get("DO_SNAPSHOT_MAX_KEEP", "1")))
except ValueError:
    DO_SNAPSHOT_MAX_KEEP = 1

DO_API_BASE = "https://api.digitalocean.com/v2"


# ──────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────
class DOApiError(RuntimeError):
    """Raised when a DigitalOcean API call fails (non-2xx or unreachable)."""


# ──────────────────────────────────────────────────────────────────
# Config inspection
# ──────────────────────────────────────────────────────────────────
def is_do_configured() -> bool:
    """True iff we can call the DO API."""
    return bool(DO_API_TOKEN and DO_DROPLET_NAME)


# ──────────────────────────────────────────────────────────────────
# Low-level DO API helper
# ──────────────────────────────────────────────────────────────────
def _do_api(
    method: str,
    path: str,
    body: Optional[dict[str, Any]] = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """
    Wrap urllib for DO API calls. Adds Bearer auth, JSON content-type,
    raises DOApiError on non-2xx. Returns parsed JSON (empty dict on
    204 No Content).
    """
    if not DO_API_TOKEN:
        raise DOApiError("DO_API_TOKEN env var not set")

    url = f"{DO_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {DO_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise DOApiError(
            f"DO API {method} {path} -> {e.code}: {detail}"
        ) from e
    except urllib.error.URLError as e:
        raise DOApiError(f"DO API unreachable: {e}") from e


# ──────────────────────────────────────────────────────────────────
# Droplet lookup
# ──────────────────────────────────────────────────────────────────
def find_droplet_id(name: str) -> Optional[int]:
    """
    Find the DO droplet id by name. Returns None if not found.

    DO API paginates at 25 by default; for a personal account with a
    handful of droplets, a single page is plenty. If TUM's account
    ever grows past 25 droplets, this needs pagination.
    """
    data = _do_api("GET", "/droplets?per_page=200")
    for d in data.get("droplets", []):
        if d.get("name") == name:
            return int(d["id"])
    return None


# ──────────────────────────────────────────────────────────────────
# Snapshot list / create / delete
# ──────────────────────────────────────────────────────────────────
def list_droplet_snapshots(droplet_id: int) -> list[dict[str, Any]]:
    """
    GET /droplets/{id}/snapshots → list of snapshot dicts.

    Each dict has at least: id (int), name (str), created_at (ISO string),
    size_gigabytes (int).
    """
    data = _do_api("GET", f"/droplets/{droplet_id}/snapshots?per_page=200")
    return data.get("snapshots", [])


def create_snapshot(droplet_id: int, name: str) -> dict[str, Any]:
    """
    POST /droplets/{id}/actions {type: snapshot, name: name}.

    Returns the action dict ({id, status, type, ...}). The snapshot
    itself is created asynchronously — actual snapshot id appears in
    /snapshots later (typically ~5-10 min for a 30 GB droplet).
    """
    payload = {"type": "snapshot", "name": name}
    data = _do_api("POST", f"/droplets/{droplet_id}/actions", body=payload)
    return data.get("action", data)


def delete_snapshot(snapshot_id: int) -> None:
    """
    DELETE /snapshots/{id}. Raises on failure, returns None on success.
    """
    _do_api("DELETE", f"/snapshots/{snapshot_id}")


# ──────────────────────────────────────────────────────────────────
# Rotation orchestrator
# ──────────────────────────────────────────────────────────────────
def rotate_auto_snapshots(
    *,
    droplet_name: Optional[str] = None,
    prefix: Optional[str] = None,
    max_keep: Optional[int] = None,
    today: Optional[_dt.date] = None,
    notify_discord: bool = True,
) -> dict[str, Any]:
    """
    Full weekly rotation: create new auto snapshot, delete old ones.

    Returns a structured report dict for the caller (also posted to
    Discord if `notify_discord` and Bot is configured):

      {
        "ok": bool,
        "droplet": str,
        "created": str | None,        # name of newly-created snapshot
        "kept": [str, ...],           # names retained
        "deleted": [str, ...],        # names removed
        "errors": [str, ...],         # any non-fatal warnings
      }

    Raises DOApiError on fatal failures (no droplet found, create
    failed, etc.). Delete failures are logged + included in `errors`
    but do not raise — the new snapshot was already created so the
    rotation is partially successful.
    """
    dname = droplet_name or DO_DROPLET_NAME
    pfx = prefix or DO_SNAPSHOT_PREFIX
    keep_n = max_keep if max_keep is not None else DO_SNAPSHOT_MAX_KEEP
    day = today or _dt.date.today()

    if not is_do_configured():
        raise DOApiError("DO_API_TOKEN or DO_DROPLET_NAME not configured")

    droplet_id = find_droplet_id(dname)
    if droplet_id is None:
        raise DOApiError(f"droplet not found by name: {dname}")

    # Snapshot name embeds the ISO date for human-readability + sorts
    # lexically in age order.
    new_name = f"{pfx}{day.isoformat()}"

    log.info(
        "rotate_auto_snapshots: droplet=%s id=%s creating %s",
        dname, droplet_id, new_name,
    )
    create_action = create_snapshot(droplet_id, new_name)
    log.info(
        "rotate_auto_snapshots: create action id=%s status=%s",
        create_action.get("id"), create_action.get("status"),
    )

    # Note: create returns an "action" before the snapshot is durable.
    # For rotation purposes we list existing auto-prefixed snapshots
    # (which won't include the new one yet) and delete oldest beyond
    # the keep limit. That leaves an extra slot in flight for ~10 min
    # until the new snapshot completes — that's fine.
    report: dict[str, Any] = {
        "ok": True,
        "droplet": dname,
        "droplet_id": droplet_id,
        "created": new_name,
        "create_action_id": create_action.get("id"),
        "create_action_status": create_action.get("status"),
        "kept": [],
        "deleted": [],
        "errors": [],
    }

    try:
        existing = list_droplet_snapshots(droplet_id)
    except DOApiError as e:
        # Non-fatal — snapshot was created, just couldn't list for cleanup
        report["errors"].append(f"list snapshots failed: {e}")
        log.error("rotate_auto_snapshots: list failed: %s", e)
        if notify_discord:
            _post_report(report)
        return report

    # Filter to auto-prefixed only — NEVER touch clean-base / session-N
    auto_snaps = [
        s for s in existing
        if isinstance(s.get("name"), str) and s["name"].startswith(pfx)
    ]
    # Sort newest first (by created_at ISO string)
    auto_snaps.sort(key=lambda s: s.get("created_at", ""), reverse=True)

    # Keep top N, delete the rest
    keep_slice = auto_snaps[:keep_n]
    delete_slice = auto_snaps[keep_n:]
    report["kept"] = [s["name"] for s in keep_slice]

    for snap in delete_slice:
        try:
            delete_snapshot(int(snap["id"]))
            report["deleted"].append(snap["name"])
            log.info(
                "rotate_auto_snapshots: deleted %s (id=%s)",
                snap["name"], snap["id"],
            )
        except DOApiError as e:
            err = f"delete {snap['name']} failed: {e}"
            report["errors"].append(err)
            log.error("rotate_auto_snapshots: %s", err)

    if notify_discord:
        _post_report(report)
    return report


def _post_report(report: dict[str, Any]) -> None:
    """Format `report` as a Discord message and POST via Bot API."""
    icon = "📸" if not report.get("errors") else "⚠️"
    lines = [
        f"{icon} **Weekly DO snapshot rotation**",
        f"Droplet: `{report.get('droplet')}` (id `{report.get('droplet_id')}`)",
        f"Created: `{report.get('created')}` "
        f"(action `{report.get('create_action_status')}`)",
    ]
    if report.get("kept"):
        lines.append("Kept: " + ", ".join(f"`{n}`" for n in report["kept"]))
    if report.get("deleted"):
        lines.append("Deleted: " + ", ".join(f"`{n}`" for n in report["deleted"]))
    if report.get("errors"):
        lines.append("Errors:")
        for e in report["errors"]:
            lines.append(f"  • `{e[:200]}`")

    text = "\n".join(lines)
    try:
        _di.send_simple_message(text)
    except Exception:
        log.exception("_post_report: Discord notify failed")


# ──────────────────────────────────────────────────────────────────
# Manual endpoints
# ──────────────────────────────────────────────────────────────────
@router.get("/status")
def snapshot_status(secret: str = Query("")):
    """
    Read-only view of current snapshots on the droplet.

    Use to verify DO_API_TOKEN works after setup, or to see what state
    rotation is in. Requires ALERTS_WEBHOOK_SECRET as a defence-in-depth
    (this endpoint is in PUBLIC_PATHS so JWT can't gate it).
    """
    if not ALERTS_WEBHOOK_SECRET:
        raise HTTPException(
            500, "ALERTS_WEBHOOK_SECRET env var not configured on backend"
        )
    if secret != ALERTS_WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid secret query param")
    if not is_do_configured():
        raise HTTPException(
            500,
            "DO not configured — set DO_API_TOKEN + DO_DROPLET_NAME in Coolify",
        )

    try:
        droplet_id = find_droplet_id(DO_DROPLET_NAME)
        if droplet_id is None:
            raise HTTPException(
                404, f"Droplet '{DO_DROPLET_NAME}' not found in DO account"
            )
        snaps = list_droplet_snapshots(droplet_id)
    except DOApiError as e:
        raise HTTPException(502, f"DO API error: {e}")

    return {
        "ok": True,
        "droplet": DO_DROPLET_NAME,
        "droplet_id": droplet_id,
        "snapshot_count": len(snaps),
        "auto_prefix": DO_SNAPSHOT_PREFIX,
        "max_keep": DO_SNAPSHOT_MAX_KEEP,
        "snapshots": [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "created_at": s.get("created_at"),
                "size_gigabytes": s.get("size_gigabytes"),
                "is_auto": isinstance(s.get("name"), str)
                and s["name"].startswith(DO_SNAPSHOT_PREFIX),
            }
            for s in snaps
        ],
    }


@router.get("/auto-rotate")
def trigger_auto_rotate(secret: str = Query("")):
    """
    Manually trigger the same rotation the weekly cron runs.

    Useful for verifying setup the first time without waiting for
    Sunday 03:00 BKK. Posts the same Discord report a cron run would.
    """
    if not ALERTS_WEBHOOK_SECRET:
        raise HTTPException(
            500, "ALERTS_WEBHOOK_SECRET env var not configured on backend"
        )
    if secret != ALERTS_WEBHOOK_SECRET:
        raise HTTPException(401, "Invalid secret query param")
    if not is_do_configured():
        raise HTTPException(
            500,
            "DO not configured — set DO_API_TOKEN + DO_DROPLET_NAME in Coolify",
        )

    try:
        report = rotate_auto_snapshots()
    except DOApiError as e:
        raise HTTPException(502, f"DO API error during rotation: {e}")
    return report
