import time

import awswrangler as wr
import boto3

from . import config


def _session() -> boto3.Session:
    return boto3.Session(region_name=config.AWS_REGION)


def dataset_uri(dataset: str, name: str | None = None) -> str:
    # Layout: s3://{bucket}/{prefix}/{dataset}/{name}.csv (CSV, for existing notebook-reader compat).
    base = f"s3://{config.S3_BUCKET}/{config.S3_PREFIX}/{dataset}"
    return f"{base}/{name}.csv" if name else base


def to_csv(df, dataset: str, name_prefix: str) -> str:
    """Write a DataFrame to s3://.../{dataset}/{name_prefix}_{epoch}.csv. Returns the path."""
    name = f"{name_prefix}_{int(time.time())}"
    path = dataset_uri(dataset, name)
    wr.s3.to_csv(df=df, path=path, index=False, boto3_session=_session())
    return path


def read_csv(dataset: str, pattern: str = "*"):
    """Read s3://.../{dataset}/{pattern}.csv into a DataFrame."""
    return wr.s3.read_csv(dataset_uri(dataset, pattern), boto3_session=_session())
