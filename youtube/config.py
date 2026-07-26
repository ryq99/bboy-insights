import os

from dotenv import load_dotenv

load_dotenv()  # read config/secrets from .env in the working dir (repo root under the CLI); see .env.example

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
