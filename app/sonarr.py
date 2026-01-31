import re
import httpx
from typing import Any


TRIMMARR_TAG_PATTERNS = [
    (re.compile(r"^trimmarr_retain_(\d+)_seasons?$", re.I), "seasons"),
    (re.compile(r"^trimmarr_retain_(\d+)_episodes?$", re.I), "episodes"),
]


def parse_retention_from_tag(tag_label: str) -> tuple[str, int] | None:
    for pattern, mode in TRIMMARR_TAG_PATTERNS:
        m = pattern.match(tag_label.strip())
        if m:
            return (mode, int(m.group(1)))
    return None


def get_retention_for_series(
    series: dict[str, Any],
    tags: list[dict[str, Any]],
) -> dict[str, int] | None:
    tag_id_to_label = {t["id"]: t["label"] for t in tags}
    result: dict[str, int] = {}
    for tag_id in series.get("tags") or []:
        label = tag_id_to_label.get(tag_id, "")
        rule = parse_retention_from_tag(label)
        if rule:
            mode, count = rule
            if count >= 1:
                result[mode] = count
    return result if result else None


class SonarrClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{self.base_url}/api/v3",
                headers=self._headers(),
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_series(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        r = await client.get("/series")
        r.raise_for_status()
        return r.json()

    async def get_tags(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        r = await client.get("/tag")
        r.raise_for_status()
        return r.json()

    async def get_quality_profiles(self) -> list[dict[str, Any]]:
        client = await self._get_client()
        r = await client.get("/qualityprofile")
        r.raise_for_status()
        return r.json()

    async def get_episodes(self, series_id: int) -> list[dict[str, Any]]:
        client = await self._get_client()
        r = await client.get("/episode", params={"seriesId": series_id})
        r.raise_for_status()
        return r.json()

    async def get_episode_files(self, series_id: int) -> list[dict[str, Any]]:
        client = await self._get_client()
        r = await client.get("/episodefile", params={"seriesId": series_id})
        r.raise_for_status()
        return r.json()

    async def delete_episode_file(self, episode_file_id: int) -> None:
        client = await self._get_client()
        r = await client.delete(f"/episodefile/{episode_file_id}")
        r.raise_for_status()

    async def set_episode_monitored(
        self, episode_ids: list[int], monitored: bool
    ) -> None:
        if not episode_ids:
            return
        client = await self._get_client()
        r = await client.put(
            "/episode/monitor",
            json={"episodeIds": episode_ids, "monitored": monitored},
        )
        r.raise_for_status()

    async def update_series(self, series: dict[str, Any]) -> dict[str, Any]:
        client = await self._get_client()
        r = await client.put("/series", json=series)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def filter_series_by_tag(
        series: list[dict[str, Any]],
        tags: list[dict[str, Any]],
        tag_label: str,
        monitored_only: bool = True,
    ) -> list[dict[str, Any]]:
        tag_id = next(
            (t["id"] for t in tags if t.get("label") == tag_label),
            None,
        )
        if tag_id is None:
            return []
        out = [s for s in series if tag_id in (s.get("tags") or [])]
        if monitored_only:
            out = [s for s in out if s.get("monitored", True)]
        return out

    @staticmethod
    def filter_series_with_trimmarr_tags(
        series: list[dict[str, Any]],
        tags: list[dict[str, Any]],
        monitored_only: bool = True,
    ) -> list[dict[str, Any]]:
        out = [
            s
            for s in series
            if get_retention_for_series(s, tags) is not None
        ]
        if monitored_only:
            out = [s for s in out if s.get("monitored", True)]
        return out


def get_episodes_to_remove(
    episodes: list[dict[str, Any]],
    episode_files: list[dict[str, Any]],
    keep_seasons: int | None,
    keep_episodes: int | None,
    keep_seasons_plus_episodes: tuple[int, int] | None = None,
) -> tuple[list[int], list[int], list[dict[str, Any]]]:
    episode_ids_to_unmonitor: list[int] = []
    episode_file_ids_to_delete: list[int] = []
    episodes_to_delete: list[dict[str, Any]] = []
    seen_files: set[int] = set()

    episode_file_by_episode: dict[int, int] = {}
    for ef in episode_files:
        eids = ef.get("episodeIds") or ef.get("episode_ids")
        if not eids:
            eid = ef.get("episodeId") or ef.get("episode_id")
            eids = [eid] if eid is not None else []
        for eid in eids:
            if eid is not None:
                episode_file_by_episode[eid] = ef["id"]

    def add_file_for_episode(ep: dict[str, Any]) -> None:
        ef_id = ep.get("episodeFileId") or ep.get("episode_file_id")
        if ef_id is None and ep.get("episodeFile"):
            ef_id = ep["episodeFile"].get("id") if isinstance(ep["episodeFile"], dict) else None
        if ef_id is None and ep.get("episode_file"):
            ef_id = ep["episode_file"].get("id") if isinstance(ep["episode_file"], dict) else None
        if ef_id is None:
            ef_id = episode_file_by_episode.get(ep["id"])
        if ef_id is not None and ef_id not in seen_files:
            episode_file_ids_to_delete.append(ef_id)
            episode_ids_to_unmonitor.append(ep["id"])
            episodes_to_delete.append(ep)
            seen_files.add(ef_id)

    def _air_date(e):
        return e.get("airDateUtc") or e.get("airDate") or ""
    def _has_file(e):
        return e.get("hasFile", e.get("has_file", bool(e.get("episodeFileId") or e.get("episode_file_id"))))

    seasons_with_files = sorted(
        {e["seasonNumber"] for e in episodes if e["seasonNumber"] >= 0 and _has_file(e)},
        reverse=True,
    )

    if keep_seasons_plus_episodes is not None:
        keep_seasons_count, keep_episodes_count = keep_seasons_plus_episodes
        seasons_full_keep = set(seasons_with_files[:keep_seasons_count])
        boundary_season = seasons_with_files[keep_seasons_count] if keep_seasons_count < len(seasons_with_files) else None
        keep_ids: set[int] = set()
        for e in episodes:
            if e["seasonNumber"] in seasons_full_keep:
                keep_ids.add(e["id"])
            elif boundary_season is not None and e["seasonNumber"] == boundary_season:
                pass
        boundary_episodes = [e for e in episodes if e["seasonNumber"] == boundary_season and _has_file(e) and _air_date(e)]
        boundary_episodes.sort(key=_air_date, reverse=True)
        keep_ids.update(e["id"] for e in boundary_episodes[:keep_episodes_count])
        episodes_to_remove = [e for e in episodes if e["id"] not in keep_ids]
    elif keep_seasons is not None:
        seasons_to_keep = set(seasons_with_files[:keep_seasons])
        episodes_to_remove = [
            e for e in episodes if e["seasonNumber"] not in seasons_to_keep
        ]
    elif keep_episodes is not None and keep_episodes >= 1:
        episodes_with_files = [e for e in episodes if _has_file(e) and _air_date(e)]
        sorted_with_files = sorted(episodes_with_files, key=_air_date, reverse=True)
        keep_ids = {e["id"] for e in sorted_with_files[:keep_episodes]}
        episodes_to_remove = [e for e in episodes if e["id"] not in keep_ids]
    else:
        return [], [], []

    for ep in episodes_to_remove:
        add_file_for_episode(ep)

    return episode_ids_to_unmonitor, episode_file_ids_to_delete, episodes_to_delete
