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

The YouTube Data API has no "similar channels" endpoint, so discovery is **seed-aware** with two
selectable sources (`--sources`):

- **`description`** (default) — mine channels **linked in the seeds' channel/video descriptions**
  (`/channel/UC…`, `@handles`, video links resolved to their uploader). High precision, cheap, organic.
- **`search`** — derive terms from the seeds' recent video tags/titles and run video search for uploader
  channels. Broader reach, but noisier and ~100 quota units/term (opt-in booster).

Results from the chosen sources are unioned, enriched with channel stats, scored, and written to S3 +
a local `data/` copy for manual curation.

```bash
# preview sources + projected quota, spend nothing
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
