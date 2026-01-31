import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from collections import deque
from datetime import datetime

from .config import get_settings
from .sonarr import (
    SonarrClient,
    get_episodes_to_remove,
    get_retention_for_series,
)


async def run_scheduled_cleanup(app: FastAPI):
    settings = get_settings()
    if settings.run_interval_hours <= 0:
        return
    while True:
        await asyncio.sleep(settings.run_interval_hours * 3600)
        try:
            sonarr = get_sonarr()
            series = await sonarr.get_series()
            tags = await sonarr.get_tags()
            filtered = SonarrClient.filter_series_with_trimmarr_tags(series, tags)
            if not filtered:
                continue
            effective_dry_run = settings.dry_run
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
                to_unmonitor, to_delete, episodes_deleted = get_episodes_to_remove(
                    episodes, episode_files, keep_seasons, keep_episodes, combined
                )
                if not effective_dry_run:
                    for ef_id in to_delete:
                        await sonarr.delete_episode_file(ef_id)
                        total_deleted += 1
                    if to_unmonitor:
                        await sonarr.set_episode_monitored(to_unmonitor, False)
                        total_unmonitored += len(to_unmonitor)
                else:
                    total_deleted += len(to_delete)
                    total_unmonitored += len(to_unmonitor)
                msg = f"Scheduled: {s['title']}: {'would ' if effective_dry_run else ''}delete {len(to_delete)} files, unmonitor {len(to_unmonitor)} episodes"
                if episodes_deleted:
                    ep_details = "; ".join(
                        f"S{ep.get('seasonNumber', '?')}E{ep.get('episodeNumber', '?')} {ep.get('title', '')}"
                        for ep in episodes_deleted[:10]
                    )
                    if len(episodes_deleted) > 10:
                        ep_details += f" ... +{len(episodes_deleted) - 10} more"
                    msg += f" | Episodes: {ep_details}"
                log("info", msg, series_id=s["id"], series_title=s["title"], dry_run=effective_dry_run)
            log("info", f"Scheduled cleanup complete: {total_deleted} files, {total_unmonitored} episodes across {len(filtered)} series")
        except Exception as e:
            log("error", f"Scheduled cleanup failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.sonarr = None
    settings = get_settings()
    if settings.run_interval_hours > 0:
        log("info", f"Scheduler enabled: cleanup every {settings.run_interval_hours}h")
        asyncio.create_task(run_scheduled_cleanup(app))
    yield
    if app.state.sonarr:
        await app.state.sonarr.close()


app = FastAPI(title="Trimmarr", lifespan=lifespan)

LOG_BUFFER: deque[dict] = deque(maxlen=500)


def log(level: str, message: str, **kwargs):
    entry = {
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "level": level,
        "message": message,
        **kwargs,
    }
    LOG_BUFFER.append(entry)


def get_sonarr() -> SonarrClient:
    s = get_settings()
    if not s.sonarr_api_key:
        raise HTTPException(503, "Sonarr not configured: set SONARR_API_KEY")
    if app.state.sonarr is None:
        app.state.sonarr = SonarrClient(s.sonarr_url, s.sonarr_api_key)
    return app.state.sonarr


@app.get("/api/status")
async def status():
    try:
        sonarr = get_sonarr()
        await sonarr.get_series()
        return {"ok": True, "sonarr": "connected", "dry_run": get_settings().dry_run}
    except HTTPException:
        raise
    except Exception as e:
        log("error", f"Sonarr connection failed: {e}")
        return {
            "ok": False,
            "sonarr": "error",
            "detail": str(e),
            "dry_run": get_settings().dry_run,
        }


@app.get("/api/tags")
async def list_tags():
    sonarr = get_sonarr()
    tags = await sonarr.get_tags()
    return {"tags": [{"id": t["id"], "label": t["label"]} for t in tags]}


@app.get("/api/series")
async def list_series(tag: str | None = None):
    sonarr = get_sonarr()
    series = await sonarr.get_series()
    if tag:
        tags = await sonarr.get_tags()
        series = SonarrClient.filter_series_by_tag(series, tags, tag)
    return {
        "series": [
            {
                "id": s["id"],
                "title": s["title"],
                "monitored": s.get("monitored", True),
                "seasonCount": s.get("seasonCount", 0),
            }
            for s in series
        ]
    }


def _format_retention(rule: dict) -> str:
    s = rule.get("seasons")
    e = rule.get("episodes")
    if s and e:
        return f"{s} season{'s' if s != 1 else ''} + {e} episode{'s' if e != 1 else ''}"
    if s:
        return f"Keep {s} season{'s' if s != 1 else ''}"
    if e:
        return f"Keep {e} episode{'s' if e != 1 else ''}"
    return ""


def _poster_url(series: dict, use_proxy: bool = True) -> str | None:
    for img in series.get("images") or []:
        if img.get("coverType") == "poster":
            if img.get("url"):
                path = img["url"].lstrip("/")
                if use_proxy:
                    return f"/api/proxy/sonarr/{path}"
                return path
            if img.get("remoteUrl"):
                return img["remoteUrl"]
    return None


@app.get("/api/proxy/sonarr/{path:path}")
async def proxy_sonarr_image(path: str):
    settings = get_settings()
    url = f"{settings.sonarr_url.rstrip('/')}/{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(
                url,
                headers={"X-Api-Key": settings.sonarr_api_key},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError:
            raise HTTPException(404, "Image not found")
        return Response(
            content=r.content,
            media_type=r.headers.get("content-type", "image/jpeg"),
        )


@app.get("/api/trimmarr-series")
async def list_trimmarr_series():
    settings = get_settings()
    sonarr = get_sonarr()
    series = await sonarr.get_series()
    tags = await sonarr.get_tags()
    quality_profiles = await sonarr.get_quality_profiles()
    qp_by_id = {qp["id"]: qp.get("name", "") for qp in quality_profiles}
    filtered = SonarrClient.filter_series_with_trimmarr_tags(series, tags)
    result = []
    for s in filtered:
        rule = get_retention_for_series(s, tags)
        if not rule:
            continue
        keep_seasons = rule.get("seasons")
        keep_episodes = rule.get("episodes")
        combined = (keep_seasons, keep_episodes) if keep_seasons and keep_episodes else None
        episodes = await sonarr.get_episodes(s["id"])
        episode_files = await sonarr.get_episode_files(s["id"])
        to_unmonitor, to_delete, episodes_deleted = get_episodes_to_remove(
            episodes, episode_files,
            keep_seasons if not combined else None,
            keep_episodes if not combined else None,
            combined,
        )
        retention_label = _format_retention(rule)
        result.append(
            {
                "id": s["id"],
                "title": s["title"],
                "network": (s.get("network") or "").strip() or None,
                "qualityProfile": qp_by_id.get(s.get("qualityProfileId")) or (s.get("qualityProfile") or {}).get("name"),
                "seasonCount": s.get("seasonCount", 0),
                "episodeFileCount": len(episode_files),
                "totalEpisodeCount": sum(
                    1 for e in episodes if e.get("seasonNumber", 0) >= 0
                ),
                "posterUrl": _poster_url(s),
                "retentionLabel": retention_label,
                "episodesToUnmonitor": len(to_unmonitor),
                "filesToDelete": len(to_delete),
            }
        )
    return {"series": result, "sonarrUrl": settings.sonarr_url}


class CleanupPreviewRequest(BaseModel):
    tag: str
    keep_seasons: int | None = None
    keep_episodes: int | None = None


@app.post("/api/preview")
async def preview_cleanup(req: CleanupPreviewRequest):
    if (req.keep_seasons is None) == (req.keep_episodes is None):
        raise HTTPException(
            400, "Provide exactly one of keep_seasons or keep_episodes"
        )
    sonarr = get_sonarr()
    series = await sonarr.get_series()
    tags = await sonarr.get_tags()
    filtered = SonarrClient.filter_series_by_tag(series, tags, req.tag)
    if not filtered:
        return {"preview": [], "tag": req.tag}

    preview = []
    for s in filtered:
        episodes = await sonarr.get_episodes(s["id"])
        episode_files = await sonarr.get_episode_files(s["id"])
        to_unmonitor, to_delete, _ = get_episodes_to_remove(
            episodes, episode_files, req.keep_seasons, req.keep_episodes
        )
        if to_unmonitor or to_delete:
            preview.append(
                {
                    "series_id": s["id"],
                    "title": s["title"],
                    "episodes_to_unmonitor": len(to_unmonitor),
                    "files_to_delete": len(to_delete),
                }
            )
    return {"preview": preview, "tag": req.tag}


class CleanupExecuteRequest(BaseModel):
    tag: str | None = None
    keep_seasons: int | None = None
    keep_episodes: int | None = None
    series_ids: list[int] | None = None
    dry_run: bool = False


@app.post("/api/cleanup")
async def execute_cleanup(req: CleanupExecuteRequest):
    effective_dry_run = req.dry_run or get_settings().dry_run
    log("info", f"Cleanup started (dry_run={effective_dry_run}, series_ids={req.series_ids})")
    sonarr = get_sonarr()
    series = await sonarr.get_series()
    tags = await sonarr.get_tags()

    if req.series_ids:
        filtered = [
            s for s in series
            if s["id"] in req.series_ids
            and get_retention_for_series(s, tags) is not None
        ]
        use_tag_rules = True
    elif req.tag and (req.keep_seasons is not None or req.keep_episodes is not None):
        if (req.keep_seasons is None) == (req.keep_episodes is None):
            raise HTTPException(
                400, "Provide exactly one of keep_seasons or keep_episodes"
            )
        filtered = SonarrClient.filter_series_by_tag(series, tags, req.tag)
        use_tag_rules = False
    else:
        raise HTTPException(
            400, "Provide series_ids or tag with keep_seasons/keep_episodes"
        )

    if not filtered:
        return {"deleted": 0, "unmonitored": 0, "series_processed": 0}

    total_deleted = 0
    total_unmonitored = 0
    for s in filtered:
        if use_tag_rules:
            rule = get_retention_for_series(s, tags)
            if not rule:
                continue
            keep_seasons = rule.get("seasons")
            keep_episodes = rule.get("episodes")
            combined = (keep_seasons, keep_episodes) if keep_seasons and keep_episodes else None
        else:
            keep_seasons = req.keep_seasons
            keep_episodes = req.keep_episodes
            combined = None

        episodes = await sonarr.get_episodes(s["id"])
        episode_files = await sonarr.get_episode_files(s["id"])
        to_unmonitor, to_delete, episodes_deleted = get_episodes_to_remove(
            episodes, episode_files, keep_seasons, keep_episodes, combined
        )
        if not effective_dry_run:
            for ef_id in to_delete:
                await sonarr.delete_episode_file(ef_id)
                total_deleted += 1
            if to_unmonitor:
                await sonarr.set_episode_monitored(to_unmonitor, False)
                total_unmonitored += len(to_unmonitor)
        else:
            total_deleted += len(to_delete)
            total_unmonitored += len(to_unmonitor)

        msg = f"{s['title']}: {'would ' if effective_dry_run else ''}delete {len(to_delete)} files, unmonitor {len(to_unmonitor)} episodes"
        if episodes_deleted:
            ep_details = "; ".join(
                f"S{ep.get('seasonNumber', '?')}E{ep.get('episodeNumber', '?')} {ep.get('title', '')}"
                for ep in episodes_deleted[:10]
            )
            if len(episodes_deleted) > 10:
                ep_details += f" ... +{len(episodes_deleted) - 10} more"
            msg += f" | Episodes: {ep_details}"
        log("info", msg, series_id=s["id"], series_title=s["title"], dry_run=effective_dry_run)

    return {
        "deleted": total_deleted,
        "unmonitored": total_unmonitored,
        "series_processed": len(filtered),
    }


@app.get("/api/logs")
async def get_logs():
    return {"logs": list(LOG_BUFFER)}


@app.get("/api/debug/episode-structure")
async def debug_episode_structure(series_id: int):
    sonarr = get_sonarr()
    episodes = await sonarr.get_episodes(series_id)
    episode_files = await sonarr.get_episode_files(series_id)
    sample_ep = next((e for e in episodes if e.get("episodeFileId") or e.get("episodeFile")), episodes[0] if episodes else None)
    sample_ef = episode_files[0] if episode_files else None
    return {
        "episode_count": len(episodes),
        "episode_file_count": len(episode_files),
        "sample_episode_keys": list(sample_ep.keys()) if sample_ep else [],
        "sample_episode_file_keys": list(sample_ef.keys()) if sample_ef else [],
        "episode_has_episodeFileId": sample_ep.get("episodeFileId") is not None if sample_ep else False,
        "episode_file_has_episodeIds": "episodeIds" in (sample_ef or {}),
        "episode_file_has_episodeId": "episodeId" in (sample_ef or {}),
    }


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")
