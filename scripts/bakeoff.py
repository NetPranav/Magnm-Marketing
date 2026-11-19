#!/usr/bin/env python3
"""CLI for running and scoring blind model bake-offs."""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENGINE_DIR = os.path.join(_REPO_ROOT, "engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import bakeoff


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run or score a blind Magnm model bake-off.")
    parser.add_argument("--role", default="writer", help="Agent role to benchmark (default: writer).")
    parser.add_argument("--models", help="Comma-separated model IDs to test.")
    parser.add_argument("--briefs", type=int, default=5, help="Number of built-in briefs to run (default: 5).")
    parser.add_argument("--seed", type=int, default=None, help="Optional random seed for blind shuffling.")
    parser.add_argument("--score", metavar="RATINGS_JSON", help="Score a human ratings JSON file.")
    parser.add_argument("--results", metavar="RESULTS_JSON", help="Optional results.json path when using --score.")
    args = parser.parse_args(argv)

    if args.score:
        report = bakeoff.score_bakeoff(args.score, results_path=args.results)
        print(f"Bake-off score summary for role '{report['role']}' ({report['results_path']}):")
        for row in report["ranking"]:
            print(
                f"  - {row['model']}: avg_score={row['avg_score']:.2f} ({row['rated_count']} rated), "
                f"auto_review_warns={row['auto_review_warns']}, median_latency_ms={row['median_latency_ms']}, "
                f"errors={row['errors']}"
            )
        print(f"Suggested primary ({report['role']}): {report['suggested_primary']}")
        print(f"Suggested backup ({report['role']}): {report['suggested_backup']}")
        print("Note: config/models.json was not modified automatically.")
        return 0

    if not args.models:
        parser.error("--models is required unless --score is provided")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    out = bakeoff.run_bakeoff(
        role=args.role,
        models=models,
        briefs_count=args.briefs,
        seed=args.seed,
    )
    print(f"Bake-off complete for role '{out['role']}' ({out['timestamp']})")
    print(f"  results.json: {out['results_path']}")
    print(f"  sheet.md (blind): {out['sheet_path']}")
    for m, st in out["summary"].items():
        print(
            f"  - {m}: median_latency_ms={st['median_latency_ms']}, "
            f"auto_review_warns={st['auto_review_warns']} (warn_briefs={st['warn_briefs']}), "
            f"errors={st['errors']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
