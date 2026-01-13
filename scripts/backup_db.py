#!/usr/bin/env python3
"""
Database Backup CLI

Triggers GitHub Actions backup workflow in theblockacademy repo and waits for completion.
The workflow runs in theblockacademy because that's where the DO/database secrets are configured.

Usage:
    # Create pre-migration backup (before schema changes)
    python scripts/backup_db.py backup --type pre-migration

    # Create manual backup
    python scripts/backup_db.py backup --type manual

    # List available backups
    python scripts/backup_db.py list

    # Show backup details
    python scripts/backup_db.py info slashai_pre-migration_20260112_150000.dump
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime


def run_gh(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a gh CLI command."""
    cmd = ['gh'] + args
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True)
    else:
        return subprocess.run(cmd)


def check_gh_auth():
    """Verify gh CLI is authenticated."""
    result = run_gh(['auth', 'status'])
    if result.returncode != 0:
        print("Error: GitHub CLI not authenticated.", file=sys.stderr)
        print("Run: gh auth login", file=sys.stderr)
        sys.exit(1)


def trigger_backup(backup_type: str, notify: bool = True) -> str:
    """Trigger backup workflow and return run ID."""
    print(f"Triggering {backup_type} backup...")

    result = run_gh([
        'workflow', 'run', 'db-backup.yml',
        '-R', 'mindfulent/theblockacademy',
        '-f', f'backup_type={backup_type}',
        '-f', f'notify_discord={str(notify).lower()}'
    ])

    if result.returncode != 0:
        print(f"Error triggering workflow: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Wait a moment for the run to be created
    time.sleep(3)

    # Get the run ID
    result = run_gh([
        'run', 'list',
        '-R', 'mindfulent/theblockacademy',
        '--workflow=db-backup.yml',
        '--limit=1',
        '--json=databaseId,status,createdAt'
    ])

    if result.returncode != 0:
        print(f"Error getting run ID: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    runs = json.loads(result.stdout)
    if not runs:
        print("Error: No workflow run found", file=sys.stderr)
        sys.exit(1)

    return str(runs[0]['databaseId'])


def wait_for_completion(run_id: str, timeout: int = 300) -> bool:
    """Wait for workflow run to complete. Returns True if successful."""
    print(f"Waiting for backup to complete (run #{run_id})...")

    start_time = time.time()
    last_status = None

    while time.time() - start_time < timeout:
        result = run_gh([
            'run', 'view', run_id,
            '-R', 'mindfulent/theblockacademy',
            '--json=status,conclusion'
        ])

        if result.returncode != 0:
            time.sleep(5)
            continue

        data = json.loads(result.stdout)
        status = data.get('status', 'unknown')
        conclusion = data.get('conclusion', '')

        if status != last_status:
            elapsed = int(time.time() - start_time)
            print(f"  [{elapsed}s] Status: {status}")
            last_status = status

        if status == 'completed':
            if conclusion == 'success':
                print(f"\nBackup completed successfully!")
                return True
            else:
                print(f"\nBackup failed with conclusion: {conclusion}", file=sys.stderr)
                return False

        time.sleep(10)

    print(f"\nTimeout waiting for backup to complete", file=sys.stderr)
    return False


def get_backup_info(run_id: str) -> dict:
    """Get backup details from completed run."""
    result = run_gh([
        'run', 'view', run_id,
        '-R', 'mindfulent/theblockacademy',
        '--json=jobs'
    ])

    if result.returncode != 0:
        return {}

    data = json.loads(result.stdout)
    # Parse job outputs for filename and size
    # This is simplified - actual implementation would parse job logs
    return data


def list_backups():
    """List available backups from DO Spaces."""
    print("Listing backups from DO Spaces...")
    print("(Requires boto3 and DO_SPACES_* environment variables)")
    print("")

    try:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            's3',
            endpoint_url=f"https://{os.environ['DO_SPACES_REGION']}.digitaloceanspaces.com",
            aws_access_key_id=os.environ['DO_SPACES_KEY'],
            aws_secret_access_key=os.environ['DO_SPACES_SECRET'],
            config=Config(signature_version='s3v4'),
            region_name=os.environ['DO_SPACES_REGION']
        )

        bucket = os.environ['DO_SPACES_BUCKET']
        prefix = 'db-backups/'

        backups = []
        paginator = s3.get_paginator('list_objects_v2')

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                name = obj['Key'].replace(prefix, '')
                size_mb = obj['Size'] / 1024 / 1024
                modified = obj['LastModified'].strftime('%Y-%m-%d %H:%M:%S UTC')
                backups.append((name, size_mb, modified))

        if not backups:
            print("No backups found.")
            return

        print(f"{'Filename':<55} {'Size':>10} {'Created':<25}")
        print("-" * 95)

        for name, size_mb, modified in sorted(backups, reverse=True):
            print(f"{name:<55} {size_mb:>8.2f} MB {modified:<25}")

        print(f"\nTotal: {len(backups)} backups")

    except ImportError:
        print("Error: boto3 required. Install with: pip install boto3", file=sys.stderr)
        sys.exit(1)
    except KeyError as e:
        print(f"Error: Missing environment variable: {e}", file=sys.stderr)
        print("Required: DO_SPACES_REGION, DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_BUCKET")
        sys.exit(1)


def cmd_backup(args):
    """Handle backup command."""
    check_gh_auth()

    print(f"")
    print(f"=== slashAI Database Backup ===")
    print(f"Type: {args.type}")
    print(f"")

    run_id = trigger_backup(args.type, notify=not args.quiet)

    if wait_for_completion(run_id, timeout=args.timeout):
        print(f"")
        print(f"Backup stored in DO Spaces: db-backups/")
        print(f"Run 'python scripts/backup_db.py list' to see all backups")
        sys.exit(0)
    else:
        print(f"")
        print(f"View logs: gh run view {run_id}")
        sys.exit(1)


def cmd_list(args):
    """Handle list command."""
    list_backups()


def main():
    parser = argparse.ArgumentParser(
        description='slashAI Database Backup CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Before running a migration:
    python scripts/backup_db.py backup --type pre-migration

    # Create a manual backup:
    python scripts/backup_db.py backup --type manual

    # List all available backups:
    python scripts/backup_db.py list
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Backup command
    backup_parser = subparsers.add_parser('backup', help='Create a database backup')
    backup_parser.add_argument(
        '--type', '-t',
        choices=['pre-migration', 'manual', 'daily'],
        default='manual',
        help='Backup type (default: manual)'
    )
    backup_parser.add_argument(
        '--timeout',
        type=int,
        default=300,
        help='Timeout in seconds (default: 300)'
    )
    backup_parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Skip Discord notification'
    )
    backup_parser.set_defaults(func=cmd_backup)

    # List command
    list_parser = subparsers.add_parser('list', help='List available backups')
    list_parser.set_defaults(func=cmd_list)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == '__main__':
    main()
