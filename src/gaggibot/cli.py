"""gaggibot CLI: run / decode / sitegen / sync."""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys

from . import __version__
from .config import Config
from .slog import SlogError, parse_slog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gaggibot",
        description="The proactive companion for GaggiMate espresso machines.",
    )
    parser.add_argument("--version", action="version", version=f"gaggibot {__version__}")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="watch the machine, message after every shot")
    p_run.add_argument("--replay", help="JSONL frame capture instead of the live machine")
    p_run.add_argument("--dry-run", action="store_true", help="log instead of messaging")

    p_dec = sub.add_parser("decode", help="decode a .slog file to JSON or CSV")
    p_dec.add_argument("file", type=pathlib.Path)
    p_dec.add_argument("--csv", action="store_true")

    p_site = sub.add_parser("sitegen", help="generate the static shot-explorer site")
    p_site.add_argument("shots_dir", type=pathlib.Path)
    p_site.add_argument("-o", "--out", type=pathlib.Path, required=True)
    p_site.add_argument("--title", default="Shot Journal")

    sub.add_parser("sync", help="one-off sync of the data repo (shots, profiles, site)")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.load(args.config)

    if args.cmd == "decode":
        try:
            shot = parse_slog(args.file.read_bytes())
        except SlogError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(shot.to_csv() if args.csv else shot.to_json(indent=1))
        return 0

    if args.cmd == "sitegen":
        from .sitegen import generate

        count = generate(args.shots_dir, args.out, title=args.title)
        print(f"{count} shots -> {args.out}")
        return 0

    if args.cmd == "sync":
        return asyncio.run(_sync(config))

    return asyncio.run(_run(config, replay=args.replay, dry_run=args.dry_run))


async def _sync(config: Config) -> int:
    from .machine import GaggiMateClient
    from .sync import sync

    if not config.data_repo:
        print("error: GAGGIBOT_DATA_REPO / data_repo not configured", file=sys.stderr)
        return 1
    async with GaggiMateClient(config.machine_host) as client:
        # WS connection (for profiles) is optional here; HTTP does the rest.
        pushed = await sync(client, config.data_repo, site_title=config.site_title)
    print("synced" if pushed else "nothing new")
    return 0


async def _run(config: Config, *, replay: str | None, dry_run: bool) -> int:
    from .commands import CommandRouter, make_frame_cache
    from .conversation import Conversation
    from .machine import GaggiMateClient
    from .messengers.base import TextReply
    from .state import State
    from .sync import sync_soon
    from .watcher import ShotWatcher, replay_frames

    log = logging.getLogger("gaggibot")
    state = State(pathlib.Path(config.state_dir) / "state.json")

    async with GaggiMateClient(config.machine_host) as client:
        if dry_run:

            class _DryMessenger:
                async def start(self): ...
                async def stop(self): ...
                async def send(self, text, options=None):
                    log.info("DRY SEND: %s %s", text, [o.label for o in options or []])
                    return "0"
                async def edit(self, ref, text, options=None): ...
                def events(self):
                    return _never()

            messenger = _DryMessenger()
        else:
            from .messengers import create_messenger

            messenger = create_messenger(config)

        def schedule_sync():
            if config.sync_enabled and config.data_repo:
                asyncio.create_task(
                    sync_soon(
                        client, config.data_repo, messenger.send,
                        site_title=config.site_title,
                    )
                )

        async def save_notes(shot_id: int, notes: dict) -> bool:
            from .bags import track_shot
            from .hints import make_hint

            for attempt in range(3):
                try:
                    resp = await client.notes_save(shot_id, notes)
                    log.info("notes saved for %06d: %s", shot_id, resp.get("msg", "?"))
                    schedule_sync()  # push notes + regenerated journal
                    if config.hints_enabled:
                        hint = make_hint(notes)
                        if hint:
                            await messenger.send(hint)
                    bag_msg = track_shot(state, notes)  # no-op unless a bag is registered
                    if bag_msg:
                        await messenger.send(bag_msg)
                    return True
                except Exception as exc:  # noqa: BLE001
                    log.warning("notes save attempt %d failed: %s", attempt + 1, exc)
                    await asyncio.sleep(5 * (attempt + 1))
            return False

        convo = Conversation(messenger, state, save_notes)
        cache_frame, latest_frame = make_frame_cache()
        router = CommandRouter(client, state, convo, messenger, config, latest_frame)
        # messenger APIs can be flaky at boot; retry instead of crash-looping
        for attempt in range(8):
            try:
                await messenger.start()
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 7:
                    raise
                log.warning("messenger start failed (%s); retry in %ds", exc, 10 * (attempt + 1))
                await asyncio.sleep(10 * (attempt + 1))
        try:
            await convo.resume_if_pending()

            async def pump_events():
                async for event in messenger.events():
                    try:
                        if isinstance(event, TextReply) and event.text.strip().startswith("/"):
                            if await router.handle(event.text):
                                continue
                        await convo.handle_event(event)
                    except Exception:  # noqa: BLE001 - a flaky send must not kill the bot
                        log.exception("event handling failed")

            async def pump_shots():
                async def tee(source):
                    async for frame in source:
                        cache_frame(frame)
                        await router.on_frame(frame)
                        yield frame

                frames = tee(
                    replay_frames(replay) if replay else client.status_stream()
                )
                watcher = ShotWatcher(
                    client,
                    min_duration_s=config.min_shot_s,
                    ignore_profiles=config.ignore_profiles,
                    last_known_id=state.get("last_shot_id", -1),
                )
                async for shot in watcher.shots(frames):
                    state.update(
                        last_shot_id=shot.entry.id,
                        last_shot={
                            "shot_id": shot.entry.id,
                            "profile": shot.profile_label or shot.entry.profile_name,
                            "duration_ms": shot.duration_ms,
                            "volume_g": shot.entry.volume_g,
                        },
                    )
                    try:
                        await convo.start_shot(
                            shot.entry.id,
                            shot.profile_label or shot.entry.profile_name,
                            shot.duration_ms,
                            shot.entry.volume_g,
                        )
                    except Exception:  # noqa: BLE001 - keep watching even if messaging fails
                        log.exception("questionnaire start failed (state kept for resume)")
                    schedule_sync()  # archive the .slog right away

            tasks = [asyncio.create_task(pump_events()), asyncio.create_task(pump_shots())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
        finally:
            await messenger.stop()
    return 0


async def _never():
    if False:  # pragma: no cover - typed empty async generator
        yield
    await asyncio.Event().wait()


if __name__ == "__main__":
    raise SystemExit(main())
