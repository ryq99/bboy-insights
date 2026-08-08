"""Config and secrets from .env, plus seed lists and ingest constants."""

import os

from dotenv import load_dotenv

# Read config/secrets from .env in the working dir; see .env.example.
load_dotenv()

S3_BUCKET = os.getenv("S3_BUCKET", "bboy-insights")
S3_PREFIX = os.getenv("S3_PREFIX", "youtube_data")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

# Curated bboy channels to ingest (handles); maintained by hand.
# Resolved to channel_ids at runtime via channels.list(forHandle=...).
SEED_CHANNELS = [
    "@redbullbcone",
]

# Anchor search terms for discovery. Used alongside seed-derived terms.
BBOY_KEYWORDS = [
    "bboy",
    "b-boy",
    "bgirl",
    "b-girl",
    "breaking",
    "breakdance",
    "breakdancing",
    "cypher",
    "top rock",
    "footwork",
    "freeze",
    "power move",
    "Red Bull BC One",
    "Freestyle Session",
    "UDEF",
    "Silverback Open",
]


# --- youtube.ingest parameters ---

# S3 / local dataset names for the breadth-browse outputs.
CHANNEL_METADATA_DATASET = "channel_metadata"
VIDEO_DETAILS_DATASET = "video_details"

# --window spec -> days back. "all_time" = no cutoff (full backfill).
WINDOW_DAYS = {"last_week": 7, "last_month": 30}
DEFAULT_WINDOW = "last_month"

# YouTube Data API caps list pages / videos.list id batches at 50 items.
API_PAGE_SIZE = 50

# Videos at or under this many seconds are labeled Shorts (no true Shorts flag).
SHORT_MAX_SECONDS = 60

# API "part" field selections per request type.
CHANNEL_PARTS = (
    "snippet,statistics,topicDetails,brandingSettings,contentDetails"
)
VIDEO_PARTS = "snippet,contentDetails,statistics,topicDetails,status"
PLAYLIST_ITEM_PARTS = "contentDetails"


def api_key() -> str:
    """Return the YouTube Data API key, or raise if it is not configured."""
    key = os.getenv("YT_DATA_API_KEY")
    if not key:
        raise RuntimeError(
            "YT_DATA_API_KEY is not set; copy .env.example to .env."
        )
    return key
