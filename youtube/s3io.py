"""Read/write helpers for the CSV datasets in S3, mirrored to local data/."""

import time
from pathlib import Path

import awswrangler as wr
import boto3
import pandas as pd

from . import config

LOCAL_DATA_DIR = Path("data")  # local mirror of the S3 datasets; gitignored


def _session() -> boto3.Session:
    """Return a boto3 session pinned to the configured AWS region."""
    return boto3.Session(region_name=config.AWS_REGION)


def dataset_uri(dataset: str, name: str | None = None) -> str:
    """Build the S3 URI for a dataset, or a single object when `name` is given.

    Layout: s3://{bucket}/{prefix}/{dataset}/{name}.csv.
    """
    base = f"s3://{config.S3_BUCKET}/{config.S3_PREFIX}/{dataset}"
    return f"{base}/{name}.csv" if name else base


def to_csv(df: pd.DataFrame, dataset: str, name_prefix: str) -> str:
    """Write a DataFrame to {dataset}/{name_prefix}_{epoch}.csv (S3 + local).

    Returns the S3 path.
    """
    name = f"{name_prefix}_{int(time.time())}"

    local_path = LOCAL_DATA_DIR / dataset / f"{name}.csv"
    local_path.parent.mkdir(parents=True, exist_ok=True)  # gitignored
    df.to_csv(local_path, index=False)

    path = dataset_uri(dataset, name)
    wr.s3.to_csv(df=df, path=path, index=False, boto3_session=_session())
    return path


def read_csv(dataset: str, pattern: str = "*") -> pd.DataFrame:
    """Read s3://.../{dataset}/{pattern}.csv into a DataFrame."""
    return wr.s3.read_csv(
        dataset_uri(dataset, pattern), boto3_session=_session()
    )


def read_csv_optional(dataset: str, pattern: str = "*") -> pd.DataFrame | None:
    """Like read_csv, but return None when nothing matches yet."""
    try:
        return read_csv(dataset, pattern)
    except wr.exceptions.NoFilesFound:
        return None


def seen_ids(dataset: str, column: str, pattern: str = "*") -> set[str]:
    """Set of `column` values already stored in `dataset` (empty first run)."""
    prior = read_csv_optional(dataset, pattern)
    if prior is None or column not in prior.columns:
        return set()
    return set(prior[column].dropna().astype(str))
