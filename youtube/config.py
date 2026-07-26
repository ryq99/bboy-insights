"""Configuration loaded from a local `.env` file.

Values (see `.env.example`):
    YT_DATA_API_KEY   YouTube Data API v3 key
    S3_BUCKET         destination bucket (default: bboy-insights)
    S3_PREFIX         key prefix within the bucket (default: youtube_data)
    AWS_REGION        AWS region for boto3/awswrangler (default: us-west-2)
"""
import os

from dotenv import load_dotenv

# Load `.env` from the current working directory (repo root when run via `python -m youtube`).
load_dotenv()

S3_BUCKET = os.getenv("S3_BUCKET", "bboy-insights")
S3_PREFIX = os.getenv("S3_PREFIX", "youtube_data")
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")

# Anchor search terms for bboy channel discovery. Used alongside seed-derived terms.
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
    "power move",
    "Red Bull BC One",
    "Freestyle Session",
    "UDEF",
    "Silverback Open",
]


def api_key() -> str:
    """Return the YouTube Data API key, or raise if it is not configured."""
    key = os.getenv("YT_DATA_API_KEY")
    if not key:
        raise RuntimeError(
            "YT_DATA_API_KEY is not set. Copy .env.example to .env and fill it in."
        )
    return key
