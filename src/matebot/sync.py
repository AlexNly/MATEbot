"""Keep a git-backed shot journal in sync with the machine.

Layout of the data repo (created on first sync if missing):
    shots/NNNNNN.slog + NNNNNN.json   raw shot logs + notes
    profiles/<label>.json             brew profiles
    settings.json                     machine settings (credentials redacted)
    docs/                             generated shot-explorer site (GitHub Pages)
"""

from __future__ import annotations

import fcntl
import json
import logging
import re
import subprocess
from pathlib import Path

import aiohttp

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
    client: GaggiMateClient, repo: str | Path, *,
    site_title: str = "Shot Journal", video_keep: int = 15,
) -> bool:
    """Pull, mirror machine state into the repo, regenerate site, commit, push.

    Returns True if a commit was pushed. Raises SyncConflict on rebase conflict.
    Needs a live client session; profile export additionally needs the WS
    connection (skipped gracefully when the socket is down).
    """
    repo = Path(repo)
    shots_dir = repo / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    with open(repo / ".matebot.lock", "w") as lock:
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

        # quarantine legacy corrupted notes first (SPA-fallback downloads, see machine.py)
        for p in shots_dir.glob("*.json"):
            if not p.read_bytes().lstrip().startswith(b"{"):
                log.warning("quarantining corrupt notes file %s", p.name)
                p.rename(p.with_suffix(".json.corrupt"))

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
                    new = json.dumps(notes, indent=1).encode()
                    if not notes_path.exists() or notes_path.read_bytes() != new:
                        notes_path.write_bytes(new)

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
        from .video import prune_videos

        prune_videos(repo, keep=video_keep)
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


async def sync_soon(
    client: GaggiMateClient, repo: str | Path, notify, *,
    site_title: str = "Shot Journal", video_keep: int = 15, state=None, quiet: bool = False,
) -> None:
    """Post-shot sync wrapper: run, report problems, never raise.

    A failed sync is remembered in *state* (``sync_pending``) so the caller
    can retry when the machine comes back online.
    """
    try:
        await sync(client, repo, site_title=site_title, video_keep=video_keep)
    except SyncConflict as exc:
        await notify(f"⚠️ Shot journal sync hit a git conflict — fix it manually:\n{exc}")
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        log.warning("sync failed, machine unreachable: %r", exc)
        if state is not None:
            state.set("sync_pending", True)
        if not quiet:
            await notify(
                "📡 The machine went offline before the journal could sync — "
                "I'll catch up as soon as it's back on."
            )
    except Exception as exc:  # noqa: BLE001
        log.exception("sync failed")
        if state is not None:
            state.set("sync_pending", True)
        if not quiet:
            await notify(f"⚠️ Shot journal sync failed: {exc!r}")
    else:
        if state is not None:
            state.set("sync_pending", False)
