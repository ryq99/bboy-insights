import re
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config, s3io
from .client import YouTubeClient

CHANNEL_DATASET = "channel_metadata"
VIDEO_DATASET = "video_details"

# Window spec -> number of days back. "all" means no cutoff (full backfill).
_SINCE_DAYS = {"3m": 90, "6m": 180, "12m": 365, "1y": 365}


def parse_since(spec: str) -> datetime | None:
    """Window spec -> UTC cutoff datetime, or None for 'all' (full backfill). One knob lets the
    same code path serve the recent window and a full backfill."""
    if spec == "all":
        return None
    days = _SINCE_DAYS.get(spec)
    if days is None:
        raise ValueError(f"unknown --since {spec!r}; use one of {sorted(_SINCE_DAYS)} or 'all'")
    return datetime.now(timezone.utc) - timedelta(days=days)


# ISO-8601 duration (PnDTnHnMnS) -> seconds. YouTube durations top out at hours for our content.
_DUR_RE = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _duration_seconds(iso: str) -> int | None:
    m = _DUR_RE.fullmatch(iso or "")
    if not m:
        return None
    days, hours, mins, secs = (int(g or 0) for g in m.groups())
    return ((days * 24 + hours) * 60 + mins) * 60 + secs


def _parse_ts(iso: str) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def resolve_channel(yt: YouTubeClient, handle: str) -> dict | None:
    """Resolve a handle to a full channel snapshot (metadata + uploads playlist), or None."""
    resp = yt.channels_list(
        part="snippet,statistics,topicDetails,brandingSettings,contentDetails",
        forHandle=handle.lstrip("@"),
        maxResults=1,
    )
    items = resp.get("items", [])
    if not items:
        return None
    ch = items[0]
    sn = ch.get("snippet", {})
    st = ch.get("statistics", {})
    branding = ch.get("brandingSettings", {}).get("channel", {})
    return {
        "channel_id": ch["id"],
        "handle": handle,
        "title": sn.get("title"),
        "description": sn.get("description"),
        "custom_url": sn.get("customUrl"),
        "country": sn.get("country"),
        "published_at": sn.get("publishedAt"),
        "keywords": branding.get("keywords"),  # space/quote-delimited channel keywords
        "num_subscribers": st.get("subscriberCount"),
        "num_videos": st.get("videoCount"),
        "num_views": st.get("viewCount"),
        "topic_categories": ch.get("topicDetails", {}).get("topicCategories", []),
        "uploads_playlist": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def channel_video_ids(yt: YouTubeClient, uploads: str, since: datetime | None) -> list[str]:
    """Page the uploads playlist newest-first, collecting video ids until videos predate `since`
    (or the playlist is exhausted when since is None). 1 quota unit per 50-video page."""
    ids: list[str] = []
    page_token = None
    while True:
        resp = yt.playlist_items_list(
            part="contentDetails",
            playlistId=uploads,
            maxResults=50,
            pageToken=page_token,
        )
        stop = False
        for it in resp.get("items", []):
            cd = it.get("contentDetails", {})
            vid = cd.get("videoId")
            if not vid:
                continue
            if since is not None:
                published = _parse_ts(cd.get("videoPublishedAt"))
                if published is not None and published < since:
                    stop = True
                    break  # newest-first, so everything after this is older too
            ids.append(vid)
        page_token = resp.get("nextPageToken")
        if stop or not page_token:
            break
    return ids


def fetch_video_details(yt: YouTubeClient, video_ids: list[str]) -> pd.DataFrame:
    """Batch videos.list (<=50 ids/call) into one row per video of high-level metadata."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = yt.videos_list(
            part="snippet,contentDetails,statistics,topicDetails,status",
            id=",".join(batch),
            maxResults=50,
        )
        for it in resp.get("items", []):
            sn = it.get("snippet", {})
            cd = it.get("contentDetails", {})
            st = it.get("statistics", {})
            duration_sec = _duration_seconds(cd.get("duration", ""))
            live = sn.get("liveBroadcastContent", "none")
            # API exposes no true Shorts flag; approximate via duration/live status.
            if live in ("live", "upcoming"):
                content_type = live
            elif duration_sec is not None and duration_sec <= 60:
                content_type = "short"
            else:
                content_type = "video"
            rows.append(
                {
                    "channel_id": sn.get("channelId"),
                    "video_id": it["id"],
                    "title": sn.get("title"),
                    "description": sn.get("description"),
                    "tags": sn.get("tags", []),
                    "published_at": sn.get("publishedAt"),
                    "duration_sec": duration_sec,
                    "content_type": content_type,
                    "category_id": sn.get("categoryId"),
                    "live_broadcast_content": live,
                    "view_count": st.get("viewCount"),
                    "like_count": st.get("likeCount"),
                    "comment_count": st.get("commentCount"),
                    "topic_categories": it.get("topicDetails", {}).get("topicCategories", []),
                    "has_captions": cd.get("caption") == "true",
                    "default_language": sn.get("defaultLanguage"),
                    "default_audio_language": sn.get("defaultAudioLanguage"),
                    "fetched_at": fetched_at,
                }
            )
    return pd.DataFrame(rows)


def _seen_video_ids(channel_id: str) -> set[str]:
    """video_ids already stored for this channel in prior runs (empty on first run)."""
    prior = s3io.read_csv_optional(VIDEO_DATASET, pattern=f"{channel_id}_*")
    if prior is None or "video_id" not in prior.columns:
        return set()
    return set(prior["video_id"].dropna().astype(str))


def ingest_channel(
    yt: YouTubeClient, handle: str, since: datetime | None, refresh: bool
) -> tuple[dict, pd.DataFrame] | None:
    """Resolve one channel and fetch its in-window video metadata. Incremental by default (skips
    video_ids already stored for the channel); `refresh` re-pulls all in-window videos to update stats."""
    channel = resolve_channel(yt, handle)
    if not channel:
        print(f"  ! could not resolve {handle!r}, skipping")
        return None
    print(f"  {handle} -> {channel['channel_id']} ({channel['title']})")

    ids = channel_video_ids(yt, channel["uploads_playlist"], since)
    print(f"    {len(ids)} videos in window")

    if not refresh:
        seen = _seen_video_ids(channel["channel_id"])
        if seen:
            new_ids = [v for v in ids if v not in seen]
            print(f"    {len(new_ids)} new ({len(ids) - len(new_ids)} already stored, skipped)")
            ids = new_ids

    videos = fetch_video_details(yt, ids) if ids else pd.DataFrame()
    return channel, videos


def ingest(
    handles: list[str] | None = None,
    since: str = "3m",
    refresh: bool = False,
    dry_run: bool = False,
) -> None:
    """Breadth-first browse of curated channels: per channel, write a one-row channel snapshot and a
    table of in-window video metadata to S3 + local data/. `dry_run` resolves + counts + projects
    quota without writing. `--since all` reuses the same path for a full backfill."""
    yt = YouTubeClient()
    handles = handles or config.SEED_CHANNELS
    cutoff = parse_since(since)
    window = "all history" if cutoff is None else f"since {cutoff.date()} ({since})"
    print(f"Ingesting {len(handles)} channel(s), window: {window}")

    for handle in handles:
        if dry_run:
            channel = resolve_channel(yt, handle)
            if not channel:
                print(f"  ! could not resolve {handle!r}")
                continue
            ids = channel_video_ids(yt, channel["uploads_playlist"], cutoff)
            # 1 (resolve) already spent + list pages + ceil(n/50) detail batches for a live run.
            projected = 1 + -(-len(ids) // 50) + -(-len(ids) // 50)
            print(
                f"  {handle} -> {channel['channel_id']} ({channel['title']}): "
                f"{len(ids)} videos in window; ~{projected} units for a live run"
            )
            continue

        result = ingest_channel(yt, handle, cutoff, refresh)
        if result is None:
            continue
        channel, videos = result

        cdf = pd.DataFrame([{**channel, "fetched_at": datetime.now(timezone.utc).isoformat()}])
        cpath = s3io.to_csv(cdf, CHANNEL_DATASET, channel["channel_id"])
        print(f"    channel meta -> {cpath}")

        if videos.empty:
            print("    no new videos to write")
        else:
            vpath = s3io.to_csv(videos, VIDEO_DATASET, channel["channel_id"])
            counts = videos["content_type"].value_counts().to_dict()
            print(f"    {len(videos)} videos -> {vpath}  {counts}")

    print(f"\nTotal quota used this run: {yt.quota_used} units")
