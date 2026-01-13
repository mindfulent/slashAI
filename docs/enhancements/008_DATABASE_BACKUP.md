# Database Backup System Implementation Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 1.0.0 |
| Created | 2026-01-12 |
| Implemented | 2026-01-13 |
| Status | **Implemented** |
| Author | Slash + Claude |
| Target Version | v0.10.x (Pre-requisite) |
| Priority | P0 - Critical |

### Implementation Notes

The following changes were made during implementation:

| Spec | Implementation | Reason |
|------|----------------|--------|
| Workflows in slashAI repo | Workflows in **theblockacademy** repo | DO/database secrets already configured there |
| PostgreSQL 16 client | PostgreSQL **18** client | DO managed database is PostgreSQL 18.1 |
| `slashai_*` filename prefix | `tba_*` filename prefix | Database is shared between slashAI and theblockacademy |
| `slashai-images/slashai-db/` path | `<bucket>/db-backups/` path | Simpler path structure |

---

## 1. Problem Statement

### 1.1 Current State

slashAI relies solely on DigitalOcean's automatic managed database backups:

```bash
$ doctl databases backups 45ec9a35-140a-4d58-92ec-9860348d1be5
Size in Gigabytes    Created At
0.044012             2026-01-12 23:12:12 +0000 UTC
0.043469             2026-01-11 23:12:10 +0000 UTC
...
```

**Current protection:**
- Daily automatic backups at ~23:12 UTC
- 7-day retention with point-in-time recovery (PITR)
- No local/downloadable copies
- No on-demand backup capability

### 1.2 The Gap

| Scenario | Risk |
|----------|------|
| Migration at 15:00 UTC fails catastrophically | Lose 16 hours of data (since 23:12 previous night) |
| Need to restore to specific pre-migration state | DO backups are time-based, not event-based |
| DO has an outage affecting backups | No offsite copy exists |
| Need backup older than 7 days | Data is gone |

### 1.3 Requirements

1. **On-demand backup** - Trigger immediately before any migration
2. **Offsite storage** - Independent of DO managed database
3. **Full database dump** - Not just memories table, entire schema
4. **Verifiable completion** - Script waits and confirms success
5. **Restore capability** - Clear path to recover from backup
6. **Automated daily backup** - Supplement DO's backups with offsite copies
7. **Retention policy** - Keep backups for 30+ days

### 1.4 Success Criteria

1. Can trigger backup and wait for completion in < 5 minutes
2. Backup stored in DO Spaces with timestamp
3. Can list available backups and their sizes
4. Can restore to a new database cluster from any backup
5. Daily automated backups running reliably
6. Discord notifications for backup status

---

## 2. Technical Architecture

### 2.1 System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Database Backup System                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  LOCAL MACHINE                   GITHUB ACTIONS                DO SPACES   │
│  ─────────────                   ──────────────                ─────────   │
│                                                                             │
│  ┌─────────────────┐            ┌─────────────────┐         ┌────────────┐ │
│  │ backup_db.py    │───trigger──▶│ db-backup.yml   │         │db-backups/ │ │
│  │                 │            │                 │         │            │ │
│  │ Commands:       │            │ Steps:          │         │ Backups:   │ │
│  │ • backup        │            │ • Checkout      │         │ • pre_*    │ │
│  │ • list          │            │ • Install pg18  │────────▶│ • daily_*  │ │
│  │ • restore       │◀───status──│ • pg_dump -Fc   │         │ • manual_* │ │
│  │                 │            │ • Upload Spaces │         │            │ │
│  └─────────────────┘            │ • Notify Discord│         └────────────┘ │
│                                 │ • Prune old     │                        │
│                                 └─────────────────┘                        │
│                                                                             │
│                                 ┌─────────────────┐                        │
│                                 │ db-restore.yml  │                        │
│                                 │                 │                        │
│                                 │ • Download dump │                        │
│                                 │ • Create new DB │                        │
│                                 │ • pg_restore    │                        │
│                                 │ • Output connstr│                        │
│                                 └─────────────────┘                        │
│                                                                             │
│  ┌─────────────────┐                                                       │
│  │ DO Managed DB   │                                                       │
│  │ tba-db          │◀──────── pg_dump connection ──────────────────────────│
│  │ PostgreSQL 18   │                                                       │
│  └─────────────────┘                                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Backup Types

| Type | Trigger | Naming Convention | Use Case |
|------|---------|-------------------|----------|
| `pre-migration` | Manual | `tba_pre-migration_YYYYMMDD_HHMMSS.dump` | Before schema changes |
| `daily` | Scheduled (6am UTC) | `tba_daily_YYYYMMDD_HHMMSS.dump` | Regular protection |
| `manual` | Manual | `tba_manual_YYYYMMDD_HHMMSS.dump` | Ad-hoc backups |

### 2.3 Storage Structure

```
DO Spaces: <bucket> (configured via DO_SPACES_BUCKET secret)
└── db-backups/
    ├── tba_pre-migration_20260113_150000.dump
    ├── tba_daily_20260113_060000.dump
    ├── tba_daily_20260112_060000.dump
    ├── tba_daily_20260111_060000.dump
    └── ...
```

**Retention policy:** 30 days (configurable via `BACKUP_RETENTION_DAYS`)

---

## 3. GitHub Actions Workflows

### 3.1 Backup Workflow

```yaml
# .github/workflows/db-backup.yml

name: Database Backup

on:
  schedule:
    # Daily at 6:00 AM UTC
    - cron: '0 6 * * *'
  workflow_dispatch:
    inputs:
      backup_type:
        description: 'Backup type'
        required: true
        default: 'manual'
        type: choice
        options:
          - daily
          - pre-migration
          - manual
      notify_discord:
        description: 'Send Discord notification'
        required: false
        default: true
        type: boolean

env:
  SPACES_BUCKET: ${{ secrets.DO_SPACES_BUCKET }}
  SPACES_PREFIX: db-backups
  RETENTION_DAYS: 30

jobs:
  backup:
    runs-on: ubuntu-latest
    outputs:
      filename: ${{ steps.backup.outputs.filename }}
      size_mb: ${{ steps.backup.outputs.size_mb }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          # Install PostgreSQL 16 client
          sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
          wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
          sudo apt-get update
          sudo apt-get install -y postgresql-client-16

          # Install boto3 for Spaces upload
          pip install boto3

      - name: Determine backup type
        id: type
        run: |
          if [ "${{ github.event_name }}" = "schedule" ]; then
            echo "backup_type=daily" >> $GITHUB_OUTPUT
          else
            echo "backup_type=${{ inputs.backup_type }}" >> $GITHUB_OUTPUT
          fi

      - name: Create database backup
        id: backup
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: |
          TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
          BACKUP_TYPE="${{ steps.type.outputs.backup_type }}"
          FILENAME="slashai_${BACKUP_TYPE}_${TIMESTAMP}.dump"

          echo "Creating backup: $FILENAME"

          # Create backup using custom format (compressed, supports parallel restore)
          pg_dump "$DATABASE_URL" \
            --format=custom \
            --verbose \
            --no-owner \
            --no-acl \
            > "$FILENAME"

          # Get file size
          SIZE_BYTES=$(stat -f%z "$FILENAME" 2>/dev/null || stat -c%s "$FILENAME")
          SIZE_MB=$(echo "scale=2; $SIZE_BYTES / 1024 / 1024" | bc)

          echo "Backup complete: $FILENAME ($SIZE_MB MB)"

          echo "filename=$FILENAME" >> $GITHUB_OUTPUT
          echo "size_mb=$SIZE_MB" >> $GITHUB_OUTPUT

      - name: Upload to DO Spaces
        env:
          DO_SPACES_KEY: ${{ secrets.DO_SPACES_KEY }}
          DO_SPACES_SECRET: ${{ secrets.DO_SPACES_SECRET }}
          DO_SPACES_REGION: ${{ secrets.DO_SPACES_REGION }}
        run: |
          python << 'EOF'
          import boto3
          import os
          from botocore.config import Config

          s3 = boto3.client(
              's3',
              endpoint_url=f"https://{os.environ['DO_SPACES_REGION']}.digitaloceanspaces.com",
              aws_access_key_id=os.environ['DO_SPACES_KEY'],
              aws_secret_access_key=os.environ['DO_SPACES_SECRET'],
              config=Config(signature_version='s3v4'),
              region_name=os.environ['DO_SPACES_REGION']
          )

          filename = "${{ steps.backup.outputs.filename }}"
          key = f"${{ env.SPACES_PREFIX }}/{filename}"

          print(f"Uploading {filename} to s3://${{ env.SPACES_BUCKET }}/{key}")

          s3.upload_file(
              filename,
              "${{ env.SPACES_BUCKET }}",
              key,
              ExtraArgs={'ACL': 'private'}
          )

          print("Upload complete")
          EOF

      - name: Prune old backups
        env:
          DO_SPACES_KEY: ${{ secrets.DO_SPACES_KEY }}
          DO_SPACES_SECRET: ${{ secrets.DO_SPACES_SECRET }}
          DO_SPACES_REGION: ${{ secrets.DO_SPACES_REGION }}
        run: |
          python << 'EOF'
          import boto3
          import os
          from datetime import datetime, timezone, timedelta
          from botocore.config import Config

          s3 = boto3.client(
              's3',
              endpoint_url=f"https://{os.environ['DO_SPACES_REGION']}.digitaloceanspaces.com",
              aws_access_key_id=os.environ['DO_SPACES_KEY'],
              aws_secret_access_key=os.environ['DO_SPACES_SECRET'],
              config=Config(signature_version='s3v4'),
              region_name=os.environ['DO_SPACES_REGION']
          )

          bucket = "${{ env.SPACES_BUCKET }}"
          prefix = "${{ env.SPACES_PREFIX }}/"
          retention_days = ${{ env.RETENTION_DAYS }}
          cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

          deleted = 0
          paginator = s3.get_paginator('list_objects_v2')

          for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
              for obj in page.get('Contents', []):
                  if obj['LastModified'].replace(tzinfo=timezone.utc) < cutoff:
                      # Keep pre-migration backups longer (don't auto-delete)
                      if 'pre-migration' not in obj['Key']:
                          print(f"Deleting old backup: {obj['Key']}")
                          s3.delete_object(Bucket=bucket, Key=obj['Key'])
                          deleted += 1

          print(f"Pruned {deleted} old backups (kept pre-migration backups)")
          EOF

      - name: Notify Discord - Success
        if: success() && (github.event_name == 'schedule' || inputs.notify_discord)
        continue-on-error: true
        env:
          DISCORD_BOT_TOKEN: ${{ secrets.DISCORD_BOT_TOKEN }}
          DISCORD_CHANNEL_ID: ${{ secrets.DISCORD_BACKUP_CHANNEL_ID }}
        run: |
          python << 'EOF'
          import urllib.request
          import json
          import os

          msg = f":white_check_mark: **Database backup complete**\n"
          msg += f"Type: `${{ steps.type.outputs.backup_type }}`\n"
          msg += f"File: `${{ steps.backup.outputs.filename }}`\n"
          msg += f"Size: ${{ steps.backup.outputs.size_mb }} MB"

          req = urllib.request.Request(
              f"https://discord.com/api/v10/channels/{os.environ['DISCORD_CHANNEL_ID']}/messages",
              data=json.dumps({'content': msg}).encode(),
              headers={
                  'Authorization': f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
                  'Content-Type': 'application/json',
                  'User-Agent': 'slashAI-Backup (1.0)'
              }
          )
          urllib.request.urlopen(req)
          EOF

      - name: Notify Discord - Failure
        if: failure() && (github.event_name == 'schedule' || inputs.notify_discord)
        continue-on-error: true
        env:
          DISCORD_BOT_TOKEN: ${{ secrets.DISCORD_BOT_TOKEN }}
          DISCORD_CHANNEL_ID: ${{ secrets.DISCORD_BACKUP_CHANNEL_ID }}
        run: |
          python << 'EOF'
          import urllib.request
          import json
          import os

          run_url = "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          msg = f":x: **Database backup failed**\n"
          msg += f"Type: `${{ steps.type.outputs.backup_type }}`\n"
          msg += f"[View logs]({run_url})"

          req = urllib.request.Request(
              f"https://discord.com/api/v10/channels/{os.environ['DISCORD_CHANNEL_ID']}/messages",
              data=json.dumps({'content': msg}).encode(),
              headers={
                  'Authorization': f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
                  'Content-Type': 'application/json',
                  'User-Agent': 'slashAI-Backup (1.0)'
              }
          )
          urllib.request.urlopen(req)
          EOF
```

### 3.2 Restore Workflow

```yaml
# .github/workflows/db-restore.yml

name: Database Restore

on:
  workflow_dispatch:
    inputs:
      backup_filename:
        description: 'Backup filename to restore (e.g., slashai_pre-migration_20260112_150000.dump)'
        required: true
        type: string
      target_database:
        description: 'Target database name (will be created if not exists)'
        required: true
        default: 'slashai_restored'
        type: string
      confirm:
        description: 'Type RESTORE to confirm'
        required: true
        type: string

jobs:
  restore:
    runs-on: ubuntu-latest
    if: ${{ inputs.confirm == 'RESTORE' }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
          wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
          sudo apt-get update
          sudo apt-get install -y postgresql-client-16
          pip install boto3

      - name: Download backup from Spaces
        env:
          DO_SPACES_KEY: ${{ secrets.DO_SPACES_KEY }}
          DO_SPACES_SECRET: ${{ secrets.DO_SPACES_SECRET }}
          DO_SPACES_REGION: ${{ secrets.DO_SPACES_REGION }}
          DO_SPACES_BUCKET: ${{ secrets.DO_SPACES_BUCKET }}
        run: |
          python << 'EOF'
          import boto3
          import os
          from botocore.config import Config

          s3 = boto3.client(
              's3',
              endpoint_url=f"https://{os.environ['DO_SPACES_REGION']}.digitaloceanspaces.com",
              aws_access_key_id=os.environ['DO_SPACES_KEY'],
              aws_secret_access_key=os.environ['DO_SPACES_SECRET'],
              config=Config(signature_version='s3v4'),
              region_name=os.environ['DO_SPACES_REGION']
          )

          filename = "${{ inputs.backup_filename }}"
          key = f"db-backups/{filename}"

          print(f"Downloading {key}...")
          s3.download_file(os.environ['DO_SPACES_BUCKET'], key, filename)
          print(f"Downloaded: {filename}")
          EOF

      - name: Restore to database
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
        run: |
          FILENAME="${{ inputs.backup_filename }}"
          TARGET_DB="${{ inputs.target_database }}"

          echo "Restoring $FILENAME to database: $TARGET_DB"
          echo "NOTE: This creates/replaces the target database."
          echo ""

          # Extract connection parts from DATABASE_URL
          # Format: postgresql://user:pass@host:port/dbname?sslmode=require

          # For now, output instructions (full automation would require admin access)
          echo "============================================"
          echo "MANUAL RESTORE STEPS:"
          echo "============================================"
          echo ""
          echo "1. Connect to your DO database cluster"
          echo "2. Create a new database: CREATE DATABASE $TARGET_DB;"
          echo "3. Run: pg_restore -d \$NEW_DATABASE_URL -v $FILENAME"
          echo ""
          echo "Or restore to the existing database (DESTRUCTIVE):"
          echo "   pg_restore -d \$DATABASE_URL --clean --if-exists -v $FILENAME"
          echo ""
          echo "Backup file is available as artifact."

      - name: Upload backup as artifact
        uses: actions/upload-artifact@v4
        with:
          name: database-backup
          path: ${{ inputs.backup_filename }}
          retention-days: 7
```

---

## 4. Local CLI Script

### 4.1 Backup Script

```python
# scripts/backup_db.py

#!/usr/bin/env python3
"""
Database Backup CLI

Triggers GitHub Actions backup workflow and waits for completion.

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
```

---

## 5. Secrets Configuration

### 5.1 Required GitHub Secrets

| Secret | Source | Description |
|--------|--------|-------------|
| `DATABASE_URL` | DO Database connection string | `postgresql://user:pass@host:port/db?sslmode=require` |
| `DO_SPACES_KEY` | DO Spaces access key | Already configured for image storage |
| `DO_SPACES_SECRET` | DO Spaces secret key | Already configured for image storage |
| `DO_SPACES_BUCKET` | DO Spaces bucket name | `slashai-images` (existing) |
| `DO_SPACES_REGION` | DO Spaces region | `nyc3` (existing) |
| `DISCORD_BOT_TOKEN` | Discord bot token | Already configured |
| `DISCORD_BACKUP_CHANNEL_ID` | Channel for notifications | New - create a #bot-logs channel |

### 5.2 Getting DATABASE_URL

```bash
# Get connection string from doctl
doctl databases connection 45ec9a35-140a-4d58-92ec-9860348d1be5 --format URI

# Or construct manually:
# postgresql://doadmin:PASSWORD@tba-db-do-user-17011271-0.g.db.ondigitalocean.com:25060/defaultdb?sslmode=require
```

---

## 6. Pre-Migration Workflow

### 6.1 Checklist

Before any database migration:

```markdown
## Pre-Migration Checklist

- [ ] Create backup:
      ```
      python scripts/backup_db.py backup --type pre-migration
      ```

- [ ] Verify backup completed:
      ```
      python scripts/backup_db.py list
      ```

- [ ] Note the backup filename: `slashai_pre-migration_YYYYMMDD_HHMMSS.dump`

- [ ] Apply migration:
      ```
      psql $DATABASE_URL -f migrations/0XX_migration_name.sql
      ```

- [ ] Verify migration succeeded

- [ ] If rollback needed:
      ```
      gh workflow run db-restore.yml \
        -f backup_filename=slashai_pre-migration_YYYYMMDD_HHMMSS.dump \
        -f target_database=slashai_restored \
        -f confirm=RESTORE
      ```
```

### 6.2 Example Session

```bash
$ python scripts/backup_db.py backup --type pre-migration

=== slashAI Database Backup ===
Type: pre-migration

Triggering pre-migration backup...
Waiting for backup to complete (run #12345678)...
  [0s] Status: queued
  [10s] Status: in_progress
  [45s] Status: in_progress
  [80s] Status: completed

Backup completed successfully!

Backup stored in DO Spaces: db-backups/
Run 'python scripts/backup_db.py list' to see all backups

$ python scripts/backup_db.py list

Filename                                               Size       Created
-----------------------------------------------------------------------------------------------
slashai_pre-migration_20260112_150032.dump            44.12 MB   2026-01-12 15:00:45 UTC
slashai_daily_20260112_060012.dump                    44.01 MB   2026-01-12 06:00:25 UTC
slashai_daily_20260111_060008.dump                    43.89 MB   2026-01-11 06:00:21 UTC

Total: 3 backups

$ # Safe to proceed with migration
$ psql $DATABASE_URL -f migrations/012_add_hybrid_search.sql
```

---

## 7. Restore Procedures

### 7.1 Restore to New Database (Recommended)

```bash
# 1. Download backup via GitHub Actions artifact or directly from Spaces

# 2. Create new database in DO console or via API

# 3. Restore
pg_restore \
  --dbname="postgresql://user:pass@host:port/new_db?sslmode=require" \
  --verbose \
  --no-owner \
  --no-acl \
  slashai_pre-migration_20260112_150032.dump

# 4. Update application to use new database
# 5. Verify application works
# 6. Update secrets/env vars permanently
```

### 7.2 Restore to Existing Database (Destructive)

```bash
# WARNING: This will DROP and recreate all objects

pg_restore \
  --dbname="$DATABASE_URL" \
  --clean \
  --if-exists \
  --verbose \
  --no-owner \
  --no-acl \
  slashai_pre-migration_20260112_150032.dump
```

---

## 8. Testing Strategy

### 8.1 Initial Setup Tests

1. **Workflow triggers correctly**
   - Manual trigger via `gh workflow run`
   - Verify run starts and completes

2. **Backup created and uploaded**
   - Check DO Spaces for new file
   - Verify file size matches expectations (~44 MB)

3. **Restore works**
   - Download backup
   - Restore to local PostgreSQL
   - Verify data integrity

### 8.2 Ongoing Verification

```bash
# Weekly: Verify backups are running
$ python scripts/backup_db.py list
# Should show daily backups for past 7 days

# Monthly: Test restore to local
$ # Download latest backup
$ # Restore to local postgres
$ # Run a few queries to verify data
```

---

## 9. Rollout Plan

### Phase 1: Setup Secrets
1. Get `DATABASE_URL` from DO console
2. Add to GitHub repository secrets
3. Verify `DO_SPACES_*` secrets already exist
4. Create `DISCORD_BACKUP_CHANNEL_ID` (optional)

### Phase 2: Deploy Workflows
1. Create `.github/workflows/db-backup.yml`
2. Create `.github/workflows/db-restore.yml`
3. Create `scripts/backup_db.py`
4. Push to main branch

### Phase 3: Test
1. Trigger manual backup: `python scripts/backup_db.py backup --type manual`
2. Verify backup in DO Spaces
3. Download and verify backup file locally
4. Test restore to local PostgreSQL (optional but recommended)

### Phase 4: Enable Daily Backups
1. Verify first scheduled run at 6am UTC
2. Monitor Discord notifications
3. Check retention/pruning works after 30 days

---

## 10. Cost Analysis

| Component | Cost | Notes |
|-----------|------|-------|
| GitHub Actions | Free | Within free tier minutes |
| DO Spaces storage | ~$0.02/GB/month | 44 MB × 30 days = 1.3 GB = ~$0.03/month |
| DO Spaces transfer | ~$0.01/GB | Minimal for backup/restore |
| **Total** | **~$0.05/month** | Negligible |

---

## 11. Open Questions

1. **Which Discord channel for notifications?**
   - Create new `#bot-logs` channel?
   - Use existing channel?

2. **Retention for pre-migration backups?**
   - Currently: Never auto-delete
   - May need manual cleanup policy

3. **Should restore be fully automated?**
   - Current: Provides instructions + artifact
   - Could: Auto-create new DO database cluster

---

## Appendix A: File Locations

| File | Repository | Purpose |
|------|------------|---------|
| `.github/workflows/db-backup.yml` | theblockacademy | Backup workflow |
| `.github/workflows/db-restore.yml` | theblockacademy | Restore workflow |
| `scripts/backup_db.py` | slashAI | Local CLI tool |
| `docs/enhancements/008_DATABASE_BACKUP.md` | slashAI | This document |

## Appendix B: References

- [DigitalOcean Managed Database Backups](https://docs.digitalocean.com/products/databases/postgresql/how-to/restore-from-backups/)
- [pg_dump Documentation](https://www.postgresql.org/docs/current/app-pgdump.html)
- [pg_restore Documentation](https://www.postgresql.org/docs/current/app-pgrestore.html)
- [GitHub Actions workflow_dispatch](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#workflow_dispatch)

## Appendix C: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-01-13 | Slash + Claude | Implemented - workflows in theblockacademy, PG18, tba_* naming |
| 0.1.0 | 2026-01-12 | Slash + Claude | Initial specification |
