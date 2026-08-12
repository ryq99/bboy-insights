"""Breadth-first browse of curated channels into channel/video datasets."""

import itertools
import re
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config, s3io
from .client import YouTubeClient


def parse_window(spec: str) -> datetime | None:
    """Window spec -> UTC cutoff, or None for 'all_time' (full backfill)."""
    if spec == "all_time":
        return None
    days = config.WINDOW_DAYS.get(spec)
    if days is None:
        raise ValueError(
            f"bad --window {spec!r}; use {[*config.WINDOW_DAYS, 'all_time']}"
        )
    return datetime.now(timezone.utc) - timedelta(days=days)


# ISO-8601 duration (PnDTnHnMnS) -> seconds.
_DUR_RE = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _duration_seconds(iso: str) -> int | None:
    """ISO-8601 duration (PnDTnHnMnS) to seconds, or None if unparseable."""
    m = _DUR_RE.fullmatch(iso or "")
    if not m:
        return None
    days, hours, mins, secs = (int(g or 0) for g in m.groups())
    return ((days * 24 + hours) * 60 + mins) * 60 + secs


def _parse_ts(iso: str) -> datetime | None:
    """ISO-8601 timestamp to an aware datetime, or None if empty."""
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def resolve_channel(yt: YouTubeClient, handle: str) -> dict | None:
    """Resolve a handle to a channel snapshot (metadata + uploads), or None."""
    resp = yt.channels_list(
        part=config.CHANNEL_PARTS,
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
        # space/quote-delimited channel keywords
        "keywords": branding.get("keywords"),
        "num_subscribers": st.get("subscriberCount"),
        "num_videos": st.get("videoCount"),
        "num_views": st.get("viewCount"),
        "topic_categories": ch.get("topicDetails", {}).get(
            "topicCategories", []
        ),
        "uploads_playlist": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def channel_video_ids(
    yt: YouTubeClient,
    uploads: str,
    since: datetime | None,
    seen: set[str] | None = None,
) -> list[str]:
    """Collect new uploads video ids newest-first until they predate `since`.

    Pages to the end when `since` is None. When `seen` (already-stored ids) is
    given, skip those ids and stop paging after `config.STOP_AFTER_SEEN`
    consecutive stored ids -- uploads are newest-first, so a contiguous stored
    block means everything older is stored too (valid once the channel has had
    one full all_time ingest). 1 quota unit per 50-video page.
    """
    seen = seen or set()
    ids: list[str] = []
    page_token = None
    consecutive_seen = 0
    while True:
        resp = yt.playlist_items_list(
            part=config.PLAYLIST_ITEM_PARTS,
            playlistId=uploads,
            maxResults=config.API_PAGE_SIZE,
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
            if vid in seen:
                consecutive_seen += 1
                if consecutive_seen >= config.STOP_AFTER_SEEN:
                    stop = True
                    break
                continue
            consecutive_seen = 0
            ids.append(vid)
        page_token = resp.get("nextPageToken")
        if stop or not page_token:
            break
    return ids


# Thumbnail resolutions, best first.
_THUMB_RES = ("maxres", "standard", "high", "medium", "default")


def _best_thumbnail(thumbs: dict) -> str | None:
    """Highest-resolution thumbnail URL available, or None."""
    for res in _THUMB_RES:
        url = thumbs.get(res, {}).get("url")
        if url:
            return url
    return None


def fetch_video_details(
    yt: YouTubeClient, video_ids: list[str]
) -> pd.DataFrame:
    """Batch videos.list into one row per video of high-level metadata."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for batch in itertools.batched(video_ids, config.API_PAGE_SIZE):
        resp = yt.videos_list(
            part=config.VIDEO_PARTS,
            id=",".join(batch),
            maxResults=config.API_PAGE_SIZE,
        )
        for it in resp.get("items", []):
            sn = it.get("snippet", {})
            cd = it.get("contentDetails", {})
            st = it.get("statistics", {})
            status = it.get("status", {})
            rec = it.get("recordingDetails", {})
            loc = rec.get("location", {})
            lsd = it.get("liveStreamingDetails", {})
            duration_sec = _duration_seconds(cd.get("duration", ""))
            live = sn.get("liveBroadcastContent", "none")
            # No true Shorts flag; approximate via duration/live status.
            if live in ("live", "upcoming"):
                content_type = live
            elif (
                duration_sec is not None
                and duration_sec <= config.SHORT_MAX_SECONDS
            ):
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
                    "topic_categories": it.get("topicDetails", {}).get(
                        "topicCategories", []
                    ),
                    "has_captions": cd.get("caption") == "true",
                    "default_language": sn.get("defaultLanguage"),
                    "default_audio_language": sn.get("defaultAudioLanguage"),
                    "thumbnail_url": _best_thumbnail(sn.get("thumbnails", {})),
                    "definition": cd.get("definition"),
                    "dimension": cd.get("dimension"),
                    "licensed_content": cd.get("licensedContent"),
                    "license": status.get("license"),
                    "made_for_kids": status.get("madeForKids"),
                    "embeddable": status.get("embeddable"),
                    "privacy_status": status.get("privacyStatus"),
                    "recording_date": rec.get("recordingDate"),
                    "recording_lat": loc.get("latitude"),
                    "recording_lng": loc.get("longitude"),
                    "live_actual_start": lsd.get("actualStartTime"),
                    "live_actual_end": lsd.get("actualEndTime"),
                    "live_scheduled_start": lsd.get("scheduledStartTime"),
                    "live_concurrent_viewers": lsd.get("concurrentViewers"),
                    "fetched_at": fetched_at,
                }
            )
    return pd.DataFrame(rows)


def ingest_channel(
    yt: YouTubeClient, handle: str, cutoff: datetime | None
) -> tuple[dict, pd.DataFrame] | None:
    """Resolve one channel and fetch metadata for its new in-window videos.

    Always incremental: videos already stored for the channel are skipped.
    """
    channel = resolve_channel(yt, handle)
    if not channel:
        print(f"  ! could not resolve {handle!r}, skipping")
        return None
    print(f"  {handle} -> {channel['channel_id']} ({channel['title']})")

    seen = s3io.seen_ids(
        config.VIDEO_DETAILS_DATASET,
        "video_id",
        pattern=f"{channel['channel_id']}_*",
    )
    ids = channel_video_ids(yt, channel["uploads_playlist"], cutoff, seen=seen)
    print(f"    {len(ids)} new videos")

    videos = fetch_video_details(yt, ids) if ids else pd.DataFrame()
    return channel, videos


def ingest(
    handles: list[str] | None = None,
    window: str = config.DEFAULT_WINDOW,
    dry_run: bool = False,
) -> None:
    """Browse each curated channel: snapshot plus new in-window video metadata.

    `dry_run` previews without writing; `all_time` backfills everything.
    """
    yt = YouTubeClient()
    handles = handles or config.SEED_CHANNELS
    cutoff = parse_window(window)
    label = (
        "all history" if cutoff is None else f"since {cutoff.date()} ({window})"
    )
    print(f"Ingesting {len(handles)} channel(s), window: {label}")

    for handle in handles:
        if dry_run:
            channel = resolve_channel(yt, handle)
            if not channel:
                print(f"  ! could not resolve {handle!r}")
                continue
            seen = s3io.seen_ids(
                config.VIDEO_DETAILS_DATASET,
                "video_id",
                pattern=f"{channel['channel_id']}_*",
            )
            ids = channel_video_ids(
                yt, channel["uploads_playlist"], cutoff, seen=seen
            )
            # resolve + paging already spent; +ceil(new/page) to fetch details.
            detail_units = -(-len(ids) // config.API_PAGE_SIZE)
            print(
                f"  {handle} -> {channel['channel_id']} ({channel['title']}): "
                f"{len(ids)} new videos; ~{detail_units} more units to fetch"
            )
            continue

        result = ingest_channel(yt, handle, cutoff)
        if result is None:
            continue
        channel, videos = result

        cdf = pd.DataFrame(
            [{**channel, "fetched_at": datetime.now(timezone.utc).isoformat()}]
        )
        cpath = s3io.to_csv(
            cdf, config.CHANNEL_METADATA_DATASET, channel["channel_id"]
        )
        print(f"    channel meta -> {cpath}")

        if videos.empty:
            print("    no new videos to write")
        else:
            vpath = s3io.to_csv(
                videos, config.VIDEO_DETAILS_DATASET, channel["channel_id"]
            )
            counts = videos["content_type"].value_counts().to_dict()
            print(f"    {len(videos)} videos -> {vpath}  {counts}")

    print(f"\nTotal quota used this run: {yt.quota_used} units")
