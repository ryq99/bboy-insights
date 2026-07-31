import re
from collections import Counter

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
        "description": ch["snippet"].get("description", ""),  # channel "about" text — mined for links
        "uploads": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def recent_video_ids(yt: YouTubeClient, uploads_playlist: str, n: int = 50) -> list[str]:
    resp = yt.playlist_items_list(
        part="contentDetails", playlistId=uploads_playlist, maxResults=min(n, 50)
    )
    return [it["contentDetails"]["videoId"] for it in resp.get("items", [])][:n]


# Channel references found in free-text descriptions.
_CHANNEL_ID_RE = re.compile(r"youtube\.com/channel/(UC[0-9A-Za-z_-]{22})")
_HANDLE_RE = re.compile(r"@([A-Za-z0-9._-]{3,30})")
_VIDEO_RE = re.compile(r"(?:youtu\.be/|watch\?v=)([0-9A-Za-z_-]{11})")


def _seed_descriptions(yt: YouTubeClient, seeds: list[dict], recent_n: int) -> str:
    """Concatenate each seed's channel 'about' text and its recent video descriptions."""
    texts = []
    for seed in seeds:
        texts.append(seed.get("description", ""))
        vids = recent_video_ids(yt, seed["uploads"], n=recent_n)
        for i in range(0, len(vids), 50):
            resp = yt.videos_list(part="snippet", id=",".join(vids[i : i + 50]))
            for it in resp.get("items", []):
                texts.append(it.get("snippet", {}).get("description", ""))
    return "\n".join(texts)


def mine_description_channels(
    yt: YouTubeClient, seeds: list[dict], recent_n: int = 50, max_handle_resolves: int = 50
) -> pd.DataFrame:
    """Discover channels linked in seed descriptions (the creator graph): /channel/UC… ids,
    @handles, and video links resolved to their uploader. Returns [channel_id, channel_title, n_refs]."""
    blob = _seed_descriptions(yt, seeds, recent_n)

    refs: Counter = Counter()
    titles: dict[str, str] = {}

    for cid in _CHANNEL_ID_RE.findall(blob):  # direct channel ids — free
        refs[cid] += 1

    handles = list(dict.fromkeys(h.lower() for h in _HANDLE_RE.findall(blob)))[:max_handle_resolves]
    for h in handles:  # @handle -> channel via forHandle (1 unit each, capped)
        try:
            resp = yt.channels_list(part="id,snippet", forHandle=h, maxResults=1)
        except Exception:
            continue
        items = resp.get("items", [])
        if items:
            cid = items[0]["id"]
            refs[cid] += 1
            titles.setdefault(cid, items[0]["snippet"]["title"])

    video_ids = list(dict.fromkeys(_VIDEO_RE.findall(blob)))
    for i in range(0, len(video_ids), 50):  # video link -> uploader channel (batched)
        resp = yt.videos_list(part="snippet", id=",".join(video_ids[i : i + 50]))
        for it in resp.get("items", []):
            sn = it.get("snippet", {})
            cid = sn.get("channelId")
            if cid:
                refs[cid] += 1
                titles.setdefault(cid, sn.get("channelTitle"))

    rows = [
        {"channel_id": cid, "channel_title": titles.get(cid), "n_refs": n}
        for cid, n in refs.items()
    ]
    return pd.DataFrame(rows, columns=["channel_id", "channel_title", "n_refs"])


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


def seen_channel_ids() -> set[str]:
    """channel_ids already written to the candidates dataset in prior runs (empty on first run)."""
    prior = s3io.read_csv_optional(DATASET)
    if prior is None or "channel_id" not in prior.columns:
        return set()
    return set(prior["channel_id"].dropna().astype(str))


def _search_candidates(
    yt: YouTubeClient, resolved, keywords, pages, order, max_queries, recent_n, seed_ids
) -> pd.DataFrame:
    """Keyword-search source: seed-derived (or supplied) terms -> uploader channels, scored."""
    terms = list(dict.fromkeys(keywords))[:max_queries] if keywords else derive_query_terms(
        yt, resolved, max_queries, recent_n=recent_n
    )
    print(f"search: {len(terms)} terms; ~{len(terms) * pages * 100} units -> {terms}")
    hits = search_uploaders(yt, terms, pages=pages, order=order)
    hits = hits[hits["channel_id"].notna() & ~hits["channel_id"].isin(seed_ids)]
    if hits.empty:
        return pd.DataFrame(columns=["channel_id", "channel_title", "match_score", "source"])
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
    return agg.assign(source="search")[["channel_id", "channel_title", "match_score", "source"]]


def explore(
    seeds: list[str],
    keywords: list[str] | None = None,
    pages: int = 2,
    order: str = "viewCount",
    max_queries: int = 12,
    recent_n: int = 50,
    sources: tuple[str, ...] = ("description",),
    max_handle_resolves: int = 50,
    include_seen: bool = False,
    dry_run: bool = False,
) -> pd.DataFrame | None:
    """Seed-aware bboy channel discovery. Data API v3 has no "similar channels" endpoint, so we
    combine two selectable `sources`: description-link mining (channels linked in the seeds'
    channel/video descriptions — high precision) and keyword search (video search on seed-derived
    terms — broader reach). Results are unioned, deduped to new-since-last-run (unless
    `include_seen`), enriched, scored, and written to S3 + a local data/ copy. `dry_run` previews
    without spending search quota.
    """
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

    if not resolved:
        print("No seeds resolved — nothing to do.")
        return None

    if dry_run:
        print(f"\n[dry-run] sources={list(sources)}")
        if "description" in sources:
            print(
                f"  description: mine channel 'about' + up to {recent_n} recent video descriptions "
                f"per seed (cheap; resolves up to {max_handle_resolves} @handles)."
            )
        if "search" in sources:
            terms = (
                list(dict.fromkeys(keywords))[:max_queries]
                if keywords
                else derive_query_terms(yt, resolved, max_queries, recent_n=recent_n)
            )
            print(f"  search: {len(terms)} terms {terms} -> ~{len(terms) * pages * 100} search units.")
        print("Stopping before search/enrichment/write.")
        return None

    frames = []
    if "description" in sources:
        d = mine_description_channels(yt, resolved, recent_n=recent_n, max_handle_resolves=max_handle_resolves)
        d = d[d["channel_id"].notna() & ~d["channel_id"].isin(seed_ids)]
        print(f"description: found {len(d)} linked channels.")
        if not d.empty:
            frames.append(
                d.rename(columns={"n_refs": "match_score"}).assign(source="description_link")[
                    ["channel_id", "channel_title", "match_score", "source"]
                ]
            )
    if "search" in sources:
        s = _search_candidates(yt, resolved, keywords, pages, order, max_queries, recent_n, seed_ids)
        if not s.empty:
            frames.append(s)

    if not frames:
        print("\nNo candidate channels found — nothing written.")
        print(f"Total quota used this run: {yt.quota_used} units")
        return None

    combined = (
        pd.concat(frames, ignore_index=True)
        .groupby("channel_id")
        .agg(
            channel_title=("channel_title", "first"),
            match_score=("match_score", "sum"),
            source=("source", lambda s: "both" if s.nunique() > 1 else s.iloc[0]),
        )
        .reset_index()
    )
    print(f"Found {len(combined)} unique candidate channels (excluding seeds).")

    if not include_seen:
        seen = seen_channel_ids()
        if seen:
            before = len(combined)
            combined = combined[~combined["channel_id"].isin(seen)].reset_index(drop=True)
            print(
                f"Excluded {before - len(combined)} previously-seen "
                f"({len(seen)} known); {len(combined)} new since last run."
            )

    if combined.empty:
        print("\nNo new channels since last run — nothing written.")
        print(f"Total quota used this run: {yt.quota_used} units")
        return None

    print(f"Enriching {len(combined)} channels...")
    enriched = enrich_channels(yt, combined["channel_id"].tolist())
    out = (
        combined.merge(enriched, on="channel_id", how="left")
        .sort_values("match_score", ascending=False)
        .reset_index(drop=True)
    )

    path = s3io.to_csv(out, DATASET, "candidate_channels")
    print(f"\nWrote {len(out)} new candidates -> {path}")
    print(f"Total quota used this run: {yt.quota_used} units")
    cols = ["channel_id", "title", "source", "match_score", "num_subscribers", "likely_bboy"]
    print(out[cols].head(15).to_string(index=False))
    return out
