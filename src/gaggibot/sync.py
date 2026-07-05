"""Keep a git-backed shot journal in sync with the machine.

Layout of the data repo (created on first sync if missing):
    shots/NNNNNN.slog + NNNNNN.json   raw shot logs + notes
    profiles/<label>.json             brew profiles
    settings.json                     machine settings (credentials redacted)
    docs/                             generated shot-explorer site (GitHub Pages)
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import re
import subprocess
from pathlib import Path

from .machine import GaggiMateClient, MachineError
from .sitegen import generate

log = logging.getLogger(__name__)


class SyncConflict(RuntimeError):
    """Manual edits conflict with the incoming sync; resolve by hand."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _safe_name(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]", "_", label).strip() or "unnamed"


async def sync(
    client: GaggiMateClient, repo: str | Path, *, site_title: str = "Shot Journal"
) -> bool:
    """Pull, mirror machine state into the repo, regenerate site, commit, push.

    Returns True if a commit was pushed. Raises SyncConflict on rebase conflict.
    Needs a live client session; profile export additionally needs the WS
    connection (skipped gracefully when the socket is down).
    """
    repo = Path(repo)
    shots_dir = repo / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    with open(repo / ".gaggibot.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("another sync is running; skipping")
            return False

        if (repo / ".git").exists() and _git(repo, "remote").stdout.strip():
            pull = _git(repo, "pull", "--rebase", "--autostash")
            if pull.returncode != 0:
                _git(repo, "rebase", "--abort")
                raise SyncConflict(pull.stderr.strip()[-500:])

        # --- shots ---
        index = await client.fetch_index()
        for entry in index.entries:
            if entry.deleted or not entry.completed:
                continue
            slog_path = shots_dir / f"{entry.padded_id}.slog"
            if not slog_path.exists():
                try:
                    slog_path.write_bytes(await client.fetch_slog(entry.id))
                    log.info("downloaded shot %s", entry.padded_id)
                except MachineError as exc:
                    log.warning("%s", exc)
                    continue
            notes_path = shots_dir / f"{entry.padded_id}.json"
            if entry.has_notes or not notes_path.exists():
                notes = await client.fetch_notes(entry.id)
                if notes is not None:
                    new = json.dumps(notes, indent=1)
                    if not notes_path.exists() or notes_path.read_text() != new:
                        notes_path.write_text(new)

        # quarantine legacy corrupted notes (SPA-fallback downloads, see machine.py)
        for p in shots_dir.glob("*.json"):
            if not p.read_bytes().lstrip().startswith(b"{"):
                log.warning("quarantining corrupt notes file %s", p.name)
                p.rename(p.with_suffix(".json.corrupt"))

        # --- profiles + settings (best effort) ---
        try:
            profiles = await client.profiles_list()
            pdir = repo / "profiles"
            pdir.mkdir(exist_ok=True)
            for prof in profiles:
                (pdir / f"{_safe_name(prof.get('label', 'unnamed'))}.json").write_text(
                    json.dumps(prof, indent=2, sort_keys=True)
                )
        except MachineError as exc:
            log.info("profiles skipped (%s)", exc)
        try:
            settings = await client.fetch_settings(redact=True)
            (repo / "settings.json").write_text(json.dumps(settings, indent=2, sort_keys=True))
        except Exception as exc:  # noqa: BLE001
            log.info("settings skipped (%s)", exc)

        # --- site ---
        generate(shots_dir, repo / "docs", title=site_title)

        # --- commit + push ---
        if not (repo / ".git").exists():
            _git(repo, "init", "-b", "main")
        _git(repo, "add", "-A")
        if not _git(repo, "status", "--porcelain").stdout.strip():
            log.info("nothing to commit")
            return False
        latest = max((e.id for e in index.entries), default=0)
        commit = _git(repo, "commit", "-m", f"sync: through shot {latest:06d}")
        if commit.returncode != 0:
            log.error("commit failed: %s", commit.stderr.strip())
            return False
        if _git(repo, "remote").stdout.strip():
            push = _git(repo, "push")
            if push.returncode != 0:
                log.error("push failed: %s", push.stderr.strip()[-300:])
        return True


async def sync_soon(client: GaggiMateClient, repo: str | Path, notify) -> None:
    """Post-shot sync wrapper: run, report conflicts to the user, never raise."""
    try:
        await sync(client, repo)
    except SyncConflict as exc:
        await notify(f"⚠️ Shot journal sync hit a git conflict — fix it manually:\n{exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("sync failed")
        await notify(f"⚠️ Shot journal sync failed: {exc}")
    else:
        await asyncio.sleep(0)
