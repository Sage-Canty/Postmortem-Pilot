#!/usr/bin/env python3
"""
postmortem-pilot — AI-powered RCA generator for AWS incidents.
Ingests CloudWatch logs, GitHub Actions deploy history, and alert context,
then uses Claude to draft a structured postmortem.
"""

import argparse
import sys
from datetime import datetime
from src.collector import collect_logs, collect_deploys, build_context
from src.generator import generate_rca
from src.formatter import format_markdown, print_summary


def parse_args():
    parser = argparse.ArgumentParser(
        prog="postmortem-pilot",
        description="Generate RCA drafts from AWS incident data using Claude API"
    )
    parser.add_argument(
        "--log-group",
        required=True,
        help="CloudWatch log group name (e.g. /aws/ecs/my-service)"
    )
    parser.add_argument(
        "--start-time",
        required=True,
        help="Incident start time in ISO 8601 format (e.g. 2026-04-08T02:00:00)"
    )
    parser.add_argument(
        "--end-time",
        required=True,
        help="Incident end time in ISO 8601 format (e.g. 2026-04-08T03:00:00)"
    )
    parser.add_argument(
        "--alert",
        required=True,
        help="Alert description that triggered the incident"
    )
    parser.add_argument(
        "--repo",
        required=False,
        help="GitHub repo in owner/repo format for deploy history (e.g. Sage-Canty/my-app)"
    )
    parser.add_argument(
        "--output",
        required=False,
        default=None,
        help="Output file path for RCA markdown (default: rca_<timestamp>.md)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and display data without calling Claude API"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("\n🔍 postmortem-pilot starting...\n")

    # Parse timestamps
    try:
        start_time = datetime.fromisoformat(args.start_time)
        end_time = datetime.fromisoformat(args.end_time)
    except ValueError as e:
        print(f"❌ Invalid timestamp format: {e}")
        print("   Use ISO 8601 format: 2026-04-08T02:00:00")
        sys.exit(1)

    # Collect CloudWatch logs
    print(f"📋 Collecting logs from {args.log_group}...")
    logs = collect_logs(args.log_group, start_time, end_time)
    print(f"   Found {len(logs)} log events\n")

    # Collect GitHub Actions deploy history
    deploys = []
    if args.repo:
        print(f"🚀 Collecting deploy history from {args.repo}...")
        deploys = collect_deploys(args.repo, start_time)
        print(f"   Found {len(deploys)} recent deploys\n")
    else:
        print("⚠️  No --repo provided, skipping deploy history\n")

    # Build context object
    context = build_context(
        log_group=args.log_group,
        start_time=start_time,
        end_time=end_time,
        alert=args.alert,
        logs=logs,
        deploys=deploys
    )

    # Dry run — show collected data and exit
    if args.dry_run:
        print("=" * 60)
        print("DRY RUN — collected context (Claude API not called)")
        print("=" * 60)
        print_summary(context)
        sys.exit(0)

    # Generate RCA via Claude
    print("🤖 Generating RCA with Claude...\n")
    rca_content = generate_rca(context)

    # Format and write output
    output_path = args.output or f"rca_{start_time.strftime('%Y%m%d_%H%M%S')}.md"
    markdown = format_markdown(rca_content, context)

    with open(output_path, "w") as f:
        f.write(markdown)

    print(f"✅ RCA written to {output_path}\n")


if __name__ == "__main__":
    main()
