# Bboy Insights

A data collection and analysis project for the bboy (breakdancing/breaking) community, using YouTube and Instagram as primary data sources.

## Goal

Scrape, store, and analyze content from major bboy channels and accounts to surface insights about the breaking community — top creators, trending content, engagement patterns, and community structure.

## Data Sources

- YouTube
- Instagram

## Setup

Uses [uv](https://docs.astral.sh/uv/) with Python 3.14.

```bash
uv sync                 # create .venv and install dependencies
cp .env.example .env    # then fill in YT_DATA_API_KEY
```

`.env` holds config and secrets (see `.env.example`):

| var | purpose |
|---|---|
| `YT_DATA_API_KEY` | YouTube Data API v3 key |
| `S3_BUCKET` / `S3_PREFIX` | S3 destination (`s3://bboy-insights/youtube_data/`) |
| `AWS_REGION` | region for boto3 / awswrangler |

## YouTube ingestion (`youtube/`)

CLI: `uv run youtube <command>` (installed as a console script by `uv sync`).

### `ingest` — breadth-browse curated channels (catalog)

Builds the bboy-channel **catalog** by browsing a **curated seed list** (`config.SEED_CHANNELS`, edited
by hand) breadth-first: for each channel it writes a one-row channel snapshot and a table of every
video's high-level metadata (title, description, tags, duration, stats, topics, content type) in a time
window. This answers *what content a channel has* — not how metrics change over time (see below).

```bash
# preview: resolve channels, count in-window videos, project quota — writes nothing
uv run youtube ingest --dry-run

# default: last month of uploads for every SEED_CHANNELS entry
uv run youtube ingest

# a specific channel, full-history backfill (same code path, just a wider window)
uv run youtube ingest --channels @redbullbcone --window all_time
```

Key flags: `--channels` (default `config.SEED_CHANNELS`), `--window` (`last_week` / `last_month`
default / `all_time`), `--dry-run`.

**Incremental (always):** each run skips `video_id`s already stored for the channel and writes only the
delta, so daily re-runs never duplicate video rows — they cost only the cheap playlist paging. Video
stats are captured once, at first sighting.

> **Not this command:** tracking how view/like/comment counts (and comments) change *over time* is a
> separate planned time-series ingestion, kept out of `ingest` so the catalog stays dedup-clean.

**Quota:** cheap — per channel ≈ 1 unit (channel meta) + 1 unit/50 videos to list uploads + 1 unit/50
to fetch details. A full ~3,300-video backfill ≈ 135 units (of the 10k/day).

**Output** (S3 + local `data/` mirror, one file per channel per run):
- `channel_metadata/<channel_id>_<ts>.csv` — subs/videos/views, country, description, channel keywords,
  topic categories, uploads playlist.
- `video_details/<channel_id>_<ts>.csv` — one row per video: title, description, tags, `published_at`,
  `duration_sec`, `content_type` (`short`/`video`/`live`/`upcoming`), view/like/comment counts, topic
  categories, `has_captions`, languages, `thumbnail_url`, `definition` (hd/sd), `dimension`,
  `licensed_content`, `license`, `made_for_kids`, `embeddable`, `privacy_status`, `recording_date`,
  `recording_lat`/`recording_lng` (geo, usually null), and live-event timings
  (`live_actual_start`/`_end`, `live_scheduled_start`, `live_concurrent_viewers`).

Read the whole video table with `s3io.read_csv("video_details")`, then dedup on `video_id` keeping the
latest `fetched_at` (backfill + incremental runs accumulate per-channel files).

### `explore` — discover bboy channels (parked)

> **Status: parked.** Description-link mining returns ~0 for media brands like Red Bull BC One (recent
> uploads are hashtag-only Shorts), and the keyword `search` source is noisy. Channels are curated by
> hand in `SEED_CHANNELS` for now; this is kept for reference / future rework.

The YouTube Data API has no "similar channels" endpoint, so discovery is **seed-aware** with two
selectable sources (`--sources`):

- **`description`** (default) — mine channels **linked in the seeds' channel/video descriptions**
  (`/channel/UC…`, `@handles`, video links resolved to their uploader). High precision, cheap, organic.
- **`search`** — derive terms from the seeds' recent video tags/titles and run video search for uploader
  channels. Broader reach, but noisier and ~100 quota units/term (opt-in booster).

Results from the chosen sources are unioned, enriched with channel stats, scored, and written to S3 +
a local `data/` copy for manual curation.

```bash
# preview sources + projected quota, 1 unit api cost
uv run youtube explore --seed @redbullbcone --dry-run

# default: description-link mining (cheap)
uv run youtube explore --seed @redbullbcone

# add the keyword-search booster for more reach
uv run youtube explore --seed @redbullbcone @stanceelements --sources description search --pages 2 --max-queries 12
```

Key flags: `--seed` (one or more handles), `--sources` (`description` default; add `search`),
`--max-handle-resolves` (cap on @handle lookups), `--pages` / `--max-queries` / `--order` / `--keywords`
(search source), `--include-seen`, `--dry-run`.

By default each run returns only channels **not** already in `candidates/` from prior runs ("new since
last time" — deduped against every prior candidate table). Pass `--include-seen` to return all discovered
channels regardless.

**Quota:** description mining is cheap (a few units + ≤`--max-handle-resolves` @handle lookups). The
`search` source dominates cost — `search.list` is 100 units/call, so ~`max_queries × pages × 100`. Use
`--dry-run` to check the projection first.

**Output:** `s3://bboy-insights/youtube_data/candidates/candidate_channels_<ts>.csv` (+ local `data/`
copy) — one row per candidate channel with `source` (`description_link` / `search` / `both`),
`match_score`, subscriber/video/view counts, topic categories, and a `likely_bboy` flag, sorted by score.
