"""Feature (a): seed-aware bboy channel discovery.

Data API v3 has no "similar channels" endpoint, so we approximate:
  1. Resolve seed handles (e.g. @redbullbcone) -> channel_id + uploads playlist.
  2. Derive query terms from each seed's recent video tags/titles, ranked by
     frequency, unioned with config.BBOY_KEYWORDS.
  3. Run video search for each term; collect the uploader channelIds.
  4. Aggregate + score channels, enrich with channels.list stats.
  5. Write a scored candidate-channels table to S3 for manual curation.
"""
import re
from collections import Counter, defaultdict

import pandas as pd

from . import config, s3io
from .client import YouTubeClient

DATASET = "candidates"

# Tokens too generic to be useful search terms derived from titles.
STOPWORDS = {
    "the", "and", "for", "with", "feat", "vs", "official", "video", "full",
    "hd", "new", "live", "part", "ep", "episode", "final", "finals", "round",
    "top", "best", "of", "in", "at", "on", "to", "a", "an", "by", "x", "ft",
    "highlights", "recap", "day", "world", "com", "www", "http", "https",
}
_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _tokens(text: str) -> list[str]:
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if len(t) > 2 and t not in STOPWORDS
    ]


def resolve_seed(yt: YouTubeClient, handle: str) -> dict | None:
    """Resolve a channel handle to id/title/uploads-playlist, or None if not found."""
    resp = yt.channels_list(
        part="id,snippet,contentDetails",
        forHandle=handle.lstrip("@"),
        maxResults=1,
    )
    items = resp.get("items", [])
    if not items:
        return None
    ch = items[0]
    return {
        "channel_id": ch["id"],
        "title": ch["snippet"]["title"],
        "uploads": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def recent_video_ids(yt: YouTubeClient, uploads_playlist: str, n: int = 50) -> list[str]:
    resp = yt.playlist_items_list(
        part="contentDetails", playlistId=uploads_playlist, maxResults=min(n, 50)
    )
    return [it["contentDetails"]["videoId"] for it in resp.get("items", [])][:n]


def _seed_terms(yt: YouTubeClient, video_ids: list[str]) -> Counter:
    """Frequency-count candidate terms from seed videos' tags and titles."""
    counts: Counter = Counter()
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        resp = yt.videos_list(part="snippet", id=",".join(batch))
        for it in resp.get("items", []):
            snippet = it.get("snippet", {})
            for tag in snippet.get("tags", []) or []:
                counts[tag.strip().lower()] += 2  # tags are curated -> weight higher
            for tok in _tokens(snippet.get("title", "")):
                counts[tok] += 1
    return counts


def derive_query_terms(
    yt: YouTubeClient, seeds: list[dict], max_queries: int, recent_n: int = 50
) -> list[str]:
    """Seed-derived terms first (the seed-aware signal), then keyword anchors, capped."""
    counts: Counter = Counter()
    for seed in seeds:
        vids = recent_video_ids(yt, seed["uploads"], n=recent_n)
        counts.update(_seed_terms(yt, vids))

    ordered: list[str] = []
    seen: set[str] = set()
    for term, _ in counts.most_common():
        if term not in seen:
            ordered.append(term)
            seen.add(term)
    for kw in config.BBOY_KEYWORDS:
        low = kw.lower()
        if low not in seen:
            ordered.append(kw)
            seen.add(low)
    return ordered[:max_queries]


def search_uploaders(
    yt: YouTubeClient, terms: list[str], pages: int, order: str
) -> pd.DataFrame:
    """Video search across terms; return one row per (channel, video, matched term)."""
    rows = []
    for term in terms:
        page_token = None
        for _ in range(pages):
            resp = yt.search_list(
                part="snippet",
                type="video",
                q=term,
                order=order,
                maxResults=50,
                pageToken=page_token,
            )
            for it in resp.get("items", []):
                sn = it.get("snippet", {})
                rows.append(
                    {
                        "channel_id": sn.get("channelId"),
                        "channel_title": sn.get("channelTitle"),
                        "video_id": it.get("id", {}).get("videoId"),
                        "matched_term": term,
                    }
                )
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return pd.DataFrame(rows)


def _is_bboy(title: str, description: str, topics: list[str]) -> bool:
    text = f"{title} {description}".lower()
    if any(kw.lower() in text for kw in config.BBOY_KEYWORDS):
        return True
    return any(("hip_hop" in t.lower() or "dance" in t.lower()) for t in (topics or []))


def enrich_channels(yt: YouTubeClient, channel_ids: list[str]) -> pd.DataFrame:
    """channels.list stats for candidate channels, in batches of 50."""
    rows = []
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i : i + 50]
        resp = yt.channels_list(
            part="snippet,statistics,topicDetails", id=",".join(batch), maxResults=50
        )
        for ch in resp.get("items", []):
            sn = ch.get("snippet", {})
            st = ch.get("statistics", {})
            topics = ch.get("topicDetails", {}).get("topicCategories", [])
            title = sn.get("title", "")
            desc = sn.get("description", "")
            rows.append(
                {
                    "channel_id": ch["id"],
                    "title": title,
                    "description": desc,
                    "country": sn.get("country"),
                    "published_at": sn.get("publishedAt"),
                    "num_subscribers": st.get("subscriberCount"),
                    "num_videos": st.get("videoCount"),
                    "num_views": st.get("viewCount"),
                    "topic_categories": topics,
                    "likely_bboy": _is_bboy(title, desc, topics),
                }
            )
    return pd.DataFrame(rows)


def explore(
    seeds: list[str],
    keywords: list[str] | None = None,
    pages: int = 2,
    order: str = "viewCount",
    max_queries: int = 12,
    recent_n: int = 50,
    dry_run: bool = False,
) -> pd.DataFrame | None:
    yt = YouTubeClient()

    resolved = []
    seed_ids = set()
    for handle in seeds:
        seed = resolve_seed(yt, handle)
        if not seed:
            print(f"  ! could not resolve seed {handle!r}, skipping")
            continue
        resolved.append(seed)
        seed_ids.add(seed["channel_id"])
        print(f"  seed {handle} -> {seed['channel_id']} ({seed['title']})")

    if keywords:
        terms = list(dict.fromkeys(keywords))[:max_queries]
    else:
        terms = derive_query_terms(yt, resolved, max_queries, recent_n=recent_n)

    projected = yt.quota_used + len(terms) * pages * 100
    print(f"\nDerived {len(terms)} query terms: {terms}")
    print(
        f"Quota so far: {yt.quota_used} | projected after search "
        f"(~{len(terms)}x{pages}x100): ~{projected} units"
    )
    if dry_run:
        print("\n[dry-run] stopping before search — no search quota spent.")
        return None

    print("\nSearching for uploaders...")
    hits = search_uploaders(yt, terms, pages=pages, order=order)
    hits = hits[hits["channel_id"].notna() & ~hits["channel_id"].isin(seed_ids)]

    agg = (
        hits.groupby("channel_id")
        .agg(
            channel_title=("channel_title", "first"),
            n_terms=("matched_term", "nunique"),
            n_videos=("video_id", "nunique"),
        )
        .reset_index()
    )
    agg["match_score"] = agg["n_terms"] * 2 + agg["n_videos"]

    print(f"Found {len(agg)} unique candidate channels; enriching...")
    enriched = enrich_channels(yt, agg["channel_id"].tolist())
    out = (
        agg.merge(enriched, on="channel_id", how="left")
        .sort_values("match_score", ascending=False)
        .reset_index(drop=True)
    )

    path = s3io.to_csv(out, DATASET, "candidate_channels")
    print(f"\nWrote {len(out)} candidates -> {path}")
    print(f"Total quota used this run: {yt.quota_used} units")
    print(out[["channel_id", "title", "match_score", "num_subscribers", "likely_bboy"]].head(15).to_string(index=False))
    return out
