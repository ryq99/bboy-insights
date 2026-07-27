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

### `explore` — discover bboy channels

The YouTube Data API has no "similar channels" endpoint, so discovery is **seed-aware**: it takes a seed
channel handle, derives search terms from that channel's recent video tags/titles (plus a bboy keyword
list), runs video search, and collects the uploader channels. Results are enriched with channel stats,
scored, and written to S3 for manual curation.

```bash
# preview the derived terms + projected quota, spend nothing
uv run youtube explore --seed @redbullbcone --dry-run

# small live run (~300 quota units)
uv run youtube explore --seed @redbullbcone --pages 1 --max-queries 3

# broader sweep with multiple seeds
uv run youtube explore --seed @redbullbcone @stanceelements --pages 2 --max-queries 12
```

Key flags: `--seed` (one or more handles), `--pages` (search pages/term), `--max-queries` (cap on terms),
`--order` (`viewCount` default), `--keywords` (override derived terms), `--include-seen`, `--dry-run`.

By default each run returns only channels **not** already in `candidates/` from prior runs ("new since
last time" — deduped against every prior candidate table). Pass `--include-seen` to return all discovered
channels regardless.

**Quota:** `search.list` costs 100 units/call (10,000/day default), so a run costs
roughly `max_queries × pages × 100`. Use `--dry-run` to check the projection first.

**Output:** `s3://bboy-insights/youtube_data/candidates/candidate_channels_<ts>.csv` — one row per
candidate channel with `match_score`, subscriber/video/view counts, topic categories, and a
`likely_bboy` flag, sorted by score.
