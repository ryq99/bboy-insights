"""CLI entry point for the `youtube` console script (explore, ingest)."""

import argparse

from . import config
from . import explore as explore_mod
from . import ingest as ingest_mod


def _add_explore(subparsers) -> argparse.ArgumentParser:
    """Register the `explore` subcommand and its flags."""
    p = subparsers.add_parser(
        "explore", help="Discover bboy channels from seed handles."
    )
    p.add_argument(
        "--seed",
        nargs="+",
        default=["@redbullbcone"],
        help="Seed channel handles (e.g. @redbullbcone @stanceelements).",
    )
    p.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Override derived terms with an explicit keyword list.",
    )
    p.add_argument(
        "--pages",
        type=int,
        default=2,
        help="Search pages per term (50 results/page).",
    )
    p.add_argument(
        "--order",
        default="viewCount",
        choices=["viewCount", "relevance", "date", "rating", "title"],
        help="search.list order.",
    )
    p.add_argument(
        "--max-queries",
        type=int,
        default=12,
        help="Cap on number of query terms.",
    )
    p.add_argument(
        "--recent-n", type=int, default=50, help="Seed videos sampled per seed."
    )
    p.add_argument(
        "--sources",
        nargs="+",
        default=["description"],
        choices=["description", "search"],
        help="Discovery sources (default: description mining; add 'search').",
    )
    p.add_argument(
        "--max-handle-resolves",
        type=int,
        default=50,
        help="Cap on @handles resolved from descriptions (1 quota unit each).",
    )
    p.add_argument(
        "--include-seen",
        action="store_true",
        help="Include channels seen in prior runs (default: new only).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print derived terms + projected quota, then stop.",
    )
    return p


def _add_ingest(subparsers) -> argparse.ArgumentParser:
    """Register the `ingest` subcommand and its flags."""
    p = subparsers.add_parser(
        "ingest",
        help="Breadth-browse curated SEED_CHANNELS (channel + video metadata).",
    )
    p.add_argument(
        "--channels",
        nargs="+",
        default=None,
        help="Channel handles to ingest (default: config.SEED_CHANNELS).",
    )
    p.add_argument(
        "--since",
        default=config.DEFAULT_SINCE,
        choices=[*config.SINCE_WINDOWS, "all"],
        help="Time window of uploads to fetch ('all' = full backfill).",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Re-pull in-window videos for fresh stats (default: skip seen).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve + count videos + project quota, then stop (no write).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Console entry point; dispatches the explore/ingest subcommands."""
    parser = argparse.ArgumentParser(
        prog="youtube", description="bboy-insights YouTube ingestion"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_explore(subparsers)
    _add_ingest(subparsers)
    args = parser.parse_args(argv)

    if args.command == "ingest":
        ingest_mod.ingest(
            handles=args.channels or config.SEED_CHANNELS,
            since=args.since,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
        return 0
    if args.command == "explore":
        explore_mod.explore(
            seeds=args.seed,
            keywords=args.keywords,
            pages=args.pages,
            order=args.order,
            max_queries=args.max_queries,
            recent_n=args.recent_n,
            sources=tuple(args.sources),
            max_handle_resolves=args.max_handle_resolves,
            include_seen=args.include_seen,
            dry_run=args.dry_run,
        )
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2
