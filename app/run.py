import asyncio
import sys

from .config import get_settings
from .sonarr import (
    SonarrClient,
    get_episodes_to_remove,
    get_retention_for_series,
)


async def run_cleanup_once(sonarr: SonarrClient, settings) -> tuple[int, int, int]:
    series = await sonarr.get_series()
    tags = await sonarr.get_tags()
    filtered = SonarrClient.filter_series_with_trimmarr_tags(series, tags)
    if not filtered:
        return 0, 0, 0
    total_deleted = 0
    total_unmonitored = 0
    for s in filtered:
        rule = get_retention_for_series(s, tags)
        if not rule:
            continue
        keep_seasons = rule.get("seasons")
        keep_episodes = rule.get("episodes")
        combined = (keep_seasons, keep_episodes) if keep_seasons and keep_episodes else None
        episodes = await sonarr.get_episodes(s["id"])
        episode_files = await sonarr.get_episode_files(s["id"])
        to_unmonitor, to_delete, _ = get_episodes_to_remove(
            episodes, episode_files, keep_seasons, keep_episodes, combined
        )
        if not settings.dry_run:
            for ef_id in to_delete:
                await sonarr.delete_episode_file(ef_id)
                total_deleted += 1
            if to_unmonitor:
                await sonarr.set_episode_monitored(to_unmonitor, False)
                total_unmonitored += len(to_unmonitor)
        else:
            total_deleted += len(to_delete)
            total_unmonitored += len(to_unmonitor)
    return total_deleted, total_unmonitored, len(filtered)


async def main():
    settings = get_settings()
    if not settings.sonarr_api_key:
        print("TRIMMARR_RUN requires SONARR_API_KEY", file=sys.stderr)
        sys.exit(1)
    sonarr = SonarrClient(settings.sonarr_url, settings.sonarr_api_key)
    try:
        deleted, unmonitored, processed = await run_cleanup_once(sonarr, settings)
        mode = "would " if settings.dry_run else ""
        print(f"Cleanup: {mode}deleted {deleted} files, {mode}unmonitored {unmonitored} episodes across {processed} series")
    finally:
        await sonarr.close()


if __name__ == "__main__":
    asyncio.run(main())
