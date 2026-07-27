import time
from pathlib import Path

import awswrangler as wr
import boto3

from . import config

LOCAL_DATA_DIR = Path("data")  # local mirror of the S3 datasets; gitignored


def _session() -> boto3.Session:
    return boto3.Session(region_name=config.AWS_REGION)


def dataset_uri(dataset: str, name: str | None = None) -> str:
    # Layout: s3://{bucket}/{prefix}/{dataset}/{name}.csv (CSV, for existing notebook-reader compat).
    base = f"s3://{config.S3_BUCKET}/{config.S3_PREFIX}/{dataset}"
    return f"{base}/{name}.csv" if name else base


def to_csv(df, dataset: str, name_prefix: str) -> str:
    """Write a DataFrame to s3://.../{dataset}/{name_prefix}_{epoch}.csv plus a local copy under
    data/{dataset}/ (same filename). Returns the S3 path."""
    name = f"{name_prefix}_{int(time.time())}"

    local_path = LOCAL_DATA_DIR / dataset / f"{name}.csv"
    local_path.parent.mkdir(parents=True, exist_ok=True)  # data/{dataset}/ — gitignored
    df.to_csv(local_path, index=False)

    path = dataset_uri(dataset, name)
    wr.s3.to_csv(df=df, path=path, index=False, boto3_session=_session())
    return path


def read_csv(dataset: str, pattern: str = "*"):
    """Read s3://.../{dataset}/{pattern}.csv into a DataFrame."""
    return wr.s3.read_csv(dataset_uri(dataset, pattern), boto3_session=_session())


def read_csv_optional(dataset: str, pattern: str = "*"):
    """Like read_csv, but return None when no matching objects exist yet (e.g. first run)."""
    try:
        return read_csv(dataset, pattern)
    except wr.exceptions.NoFilesFound:
        return None
