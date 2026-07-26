"""CLI entrypoint: installed as the `youtube` console script (see pyproject.toml).

Usage: `youtube <subcommand> ...` (via `uv run youtube ...`).

Subcommands:
    explore   Discover bboy channels from seed handles (Feature a).
"""
import argparse

from . import explore as explore_mod


def _add_explore(subparsers):
    p = subparsers.add_parser("explore", help="Discover bboy channels from seed handles.")
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
    p.add_argument("--pages", type=int, default=2, help="Search pages per term (50 results/page).")
    p.add_argument(
        "--order",
        default="viewCount",
        choices=["viewCount", "relevance", "date", "rating", "title"],
        help="search.list order.",
    )
    p.add_argument("--max-queries", type=int, default=12, help="Cap on number of query terms.")
    p.add_argument("--recent-n", type=int, default=50, help="Seed videos sampled for term derivation.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print derived terms + projected quota, then stop (no search spend).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="youtube", description="bboy-insights YouTube ingestion")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_explore(subparsers)
    args = parser.parse_args(argv)

    if args.command == "explore":
        explore_mod.explore(
            seeds=args.seed,
            keywords=args.keywords,
            pages=args.pages,
            order=args.order,
            max_queries=args.max_queries,
            recent_n=args.recent_n,
            dry_run=args.dry_run,
        )
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2
