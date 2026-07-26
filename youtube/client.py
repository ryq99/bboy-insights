"""Thin YouTube Data API v3 client with quota accounting.

Wraps the googleapiclient discovery object so every call is charged against a
running quota estimate (the API has a 10,000 unit/day default). Costs per call:
    search.list         100
    channels.list         1
    videos.list           1
    playlistItems.list    1
"""
import googleapiclient.discovery

from . import config

QUOTA_COSTS = {
    "search": 100,
    "channels": 1,
    "videos": 1,
    "playlistItems": 1,
}


class YouTubeClient:
    def __init__(self, api_key: str | None = None):
        self._yt = googleapiclient.discovery.build(
            "youtube",
            "v3",
            developerKey=api_key or config.api_key(),
            cache_discovery=False,
        )
        self.quota_used = 0

    def _charge(self, resource: str) -> None:
        self.quota_used += QUOTA_COSTS.get(resource, 1)

    def channels_list(self, **kwargs):
        self._charge("channels")
        return self._yt.channels().list(**kwargs).execute()

    def search_list(self, **kwargs):
        self._charge("search")
        return self._yt.search().list(**kwargs).execute()

    def videos_list(self, **kwargs):
        self._charge("videos")
        return self._yt.videos().list(**kwargs).execute()

    def playlist_items_list(self, **kwargs):
        self._charge("playlistItems")
        return self._yt.playlistItems().list(**kwargs).execute()
