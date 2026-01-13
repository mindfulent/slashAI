# Deterministic Export Implementation Specification

## Document Information

| Field | Value |
|-------|-------|
| Version | 0.1.0 |
| Created | 2026-01-12 |
| Status | Draft Specification |
| Author | Slash + Claude |
| Target Version | v0.10.x |
| Priority | P3 - Low (Developer Tooling) |

---

## 1. Problem Statement

### 1.1 Current Behavior

The memory inspector exports JSON, but it's **not deterministic**:

```python
# scripts/memory_inspector.py - current
def export_memories(memories, output_file):
    with open(output_file, 'w') as f:
        json.dump([m.to_dict() for m in memories], f, indent=2, default=str)
```

**Non-deterministic elements:**
- Timestamp formatting varies (str() on datetime)
- Float precision not controlled
- Record order not guaranteed
- Dictionary key order (mostly fixed in Python 3.7+ but not explicit)
- Whitespace formatting

### 1.2 Impact

**Cannot diff exports:**
```bash
$ python memory_inspector.py export --all -o before.json
# ... make changes ...
$ python memory_inspector.py export --all -o after.json
$ diff before.json after.json

# Shows differences even when nothing changed:
# - Timestamp format: "2026-01-12 10:30:45.123456" vs "2026-01-12T10:30:45.123456Z"
# - Float: 0.8500000000001 vs 0.85
# - Order: memory 42 before memory 41
```

**Cannot verify backups:**
```bash
$ sha256sum backup_jan12.json
abc123...
$ sha256sum backup_jan12_copy.json
xyz789...  # Different even if content identical
```

**Test fixtures unstable:**
```python
# tests/fixtures/test_memories.json changes randomly
# CI fails intermittently due to format differences
```

### 1.3 Success Criteria

1. Identical inputs produce byte-identical outputs
2. Exports can be meaningfully diffed
3. SHA-256 hash verifies backup integrity
4. Test fixtures remain stable

---

## 2. Technical Design

### 2.1 Canonical JSON Format

**Rules for deterministic JSON:**

| Element | Rule | Example |
|---------|------|---------|
| Keys | Alphabetically sorted | `{"a": 1, "b": 2}` not `{"b": 2, "a": 1}` |
| Whitespace | Minimal (no indent) | `{"a":1}` not `{ "a": 1 }` |
| Floats | Fixed precision (6 decimals) | `0.850000` not `0.8500000001` |
| Timestamps | ISO 8601 with UTC | `2026-01-12T10:30:45.123Z` |
| Order | By ID (ascending) | Memory 1 before Memory 2 |
| Nulls | Explicit `null` | `"field": null` not omitted |
| Unicode | Unescaped UTF-8 | `"name": "Rén"` not `"name": "\u0052\u00e9n"` |

### 2.2 Output Format

```json
{"memories":[{"confidence":0.850000,"created_at":"2026-01-12T10:30:45.123Z","id":1,"memory_type":"semantic","privacy_level":"global","raw_dialogue":"User: My IGN is CreeperSlayer99","topic_summary":"IGN: CreeperSlayer99","updated_at":"2026-01-12T10:30:45.123Z","user_id":123456789}],"exported_at":"2026-01-12T15:00:00.000Z","version":"1.0.0"}
```

**Note:** Single line, no whitespace, sorted keys.

For human readability, provide optional pretty-print mode:
```bash
$ python memory_inspector.py export --all -o backup.json --pretty
```

---

## 3. Implementation

### 3.1 Canonical Serializer

```python
# src/memory/export.py

"""
Deterministic Memory Export

Produces canonical JSON output for reproducible exports, diffs, and integrity verification.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class CanonicalJSONEncoder(json.JSONEncoder):
    """JSON encoder that produces deterministic output."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            # Always UTC, ISO 8601 format with 3-digit milliseconds
            if obj.tzinfo is None:
                obj = obj.replace(tzinfo=timezone.utc)
            else:
                obj = obj.astimezone(timezone.utc)
            # Format: 2026-01-12T10:30:45.123Z
            return obj.strftime('%Y-%m-%dT%H:%M:%S.') + f'{obj.microsecond // 1000:03d}Z'

        if isinstance(obj, float):
            # Fixed 6 decimal precision
            return round(obj, 6)

        if isinstance(obj, Enum):
            return obj.value

        if hasattr(obj, 'to_dict'):
            return obj.to_dict()

        if hasattr(obj, '__dict__'):
            return obj.__dict__

        return super().default(obj)


def to_canonical_json(obj: Any, pretty: bool = False) -> str:
    """
    Convert object to canonical, deterministic JSON.

    Args:
        obj: Object to serialize
        pretty: If True, add indentation for readability (not canonical)

    Returns:
        JSON string
    """
    if pretty:
        return json.dumps(
            obj,
            cls=CanonicalJSONEncoder,
            sort_keys=True,
            indent=2,
            ensure_ascii=False
        )
    else:
        return json.dumps(
            obj,
            cls=CanonicalJSONEncoder,
            sort_keys=True,
            separators=(',', ':'),  # No whitespace
            ensure_ascii=False
        )


def normalize_memory_dict(memory: dict) -> dict:
    """
    Normalize a memory dict for canonical output.

    Ensures consistent field ordering and null handling.
    """
    # Define canonical field order
    CANONICAL_FIELDS = [
        'id', 'user_id', 'topic_summary', 'raw_dialogue',
        'memory_type', 'privacy_level', 'origin_channel_id', 'origin_guild_id',
        'source_count', 'confidence', 'decay_policy', 'retrieval_count', 'is_protected',
        'created_at', 'updated_at', 'last_accessed_at'
    ]

    normalized = {}
    for field in CANONICAL_FIELDS:
        if field in memory:
            value = memory[field]
            # Normalize floats
            if isinstance(value, float):
                value = round(value, 6)
            # Normalize None to explicit null (will be "null" in JSON)
            normalized[field] = value
        else:
            normalized[field] = None

    return normalized


@dataclass
class ExportMetadata:
    """Metadata for an export."""
    version: str = "1.0.0"
    exported_at: datetime = None
    memory_count: int = 0
    user_ids: list[int] = None
    content_hash: str = None

    def __post_init__(self):
        if self.exported_at is None:
            self.exported_at = datetime.now(timezone.utc)
        if self.user_ids is None:
            self.user_ids = []


class MemoryExporter:
    """Export memories in deterministic format."""

    def __init__(self, db_pool):
        self.db = db_pool

    async def export_all(self) -> tuple[str, str]:
        """
        Export all memories in canonical format.

        Returns:
            Tuple of (json_content, sha256_hash)
        """
        rows = await self.db.fetch("""
            SELECT * FROM memories ORDER BY id
        """)

        return self._format_export(rows)

    async def export_user(self, user_id: int) -> tuple[str, str]:
        """
        Export memories for a specific user.

        Returns:
            Tuple of (json_content, sha256_hash)
        """
        rows = await self.db.fetch("""
            SELECT * FROM memories WHERE user_id = $1 ORDER BY id
        """, user_id)

        return self._format_export(rows, user_ids=[user_id])

    def _format_export(
        self,
        rows: list,
        user_ids: Optional[list[int]] = None
    ) -> tuple[str, str]:
        """Format rows as canonical JSON with hash."""

        # Normalize each memory
        memories = [normalize_memory_dict(dict(r)) for r in rows]

        # Sort by ID (should already be sorted, but ensure)
        memories.sort(key=lambda m: m['id'])

        # Build export structure
        if user_ids is None:
            user_ids = sorted(set(m['user_id'] for m in memories))

        export_data = {
            'version': '1.0.0',
            'exported_at': datetime.now(timezone.utc),
            'memory_count': len(memories),
            'user_ids': user_ids,
            'memories': memories,
        }

        # Generate canonical JSON
        json_content = to_canonical_json(export_data)

        # Calculate hash
        content_hash = hashlib.sha256(json_content.encode('utf-8')).hexdigest()

        # Add hash to export (re-serialize)
        export_data['content_hash'] = content_hash
        json_content = to_canonical_json(export_data)

        return json_content, content_hash

    def verify_export(self, json_content: str) -> tuple[bool, str]:
        """
        Verify an export's integrity.

        Returns:
            Tuple of (is_valid, message)
        """
        try:
            data = json.loads(json_content)
            claimed_hash = data.get('content_hash')

            if not claimed_hash:
                return False, "No content_hash field"

            # Remove hash and recalculate
            del data['content_hash']
            recalculated = to_canonical_json(data)
            actual_hash = hashlib.sha256(recalculated.encode('utf-8')).hexdigest()

            if actual_hash == claimed_hash:
                return True, f"Valid: {claimed_hash[:16]}..."
            else:
                return False, f"Hash mismatch: expected {claimed_hash[:16]}..., got {actual_hash[:16]}..."

        except Exception as e:
            return False, f"Parse error: {e}"
```

### 3.2 Updated Memory Inspector

```python
# scripts/memory_inspector.py - updated export command

import click
import asyncio
import asyncpg
from memory.export import MemoryExporter, to_canonical_json

@click.command()
@click.option('--user-id', type=int, help='Export specific user')
@click.option('--all', 'export_all', is_flag=True, help='Export all memories')
@click.option('-o', '--output', required=True, help='Output file path')
@click.option('--pretty', is_flag=True, help='Pretty-print JSON (not canonical)')
@click.option('--verify', is_flag=True, help='Verify existing export')
async def export(user_id, export_all, output, pretty, verify):
    """Export memories in deterministic format."""

    if verify:
        # Verify mode
        with open(output, 'r', encoding='utf-8') as f:
            content = f.read()

        exporter = MemoryExporter(None)  # No DB needed for verify
        is_valid, message = exporter.verify_export(content)

        if is_valid:
            click.echo(click.style(f"VALID: {message}", fg='green'))
        else:
            click.echo(click.style(f"INVALID: {message}", fg='red'))
            raise SystemExit(1)
        return

    # Export mode
    pool = await asyncpg.create_pool(os.environ['DATABASE_URL'])
    exporter = MemoryExporter(pool)

    if export_all:
        json_content, content_hash = await exporter.export_all()
    elif user_id:
        json_content, content_hash = await exporter.export_user(user_id)
    else:
        click.echo("Error: Specify --user-id or --all")
        raise SystemExit(1)

    # Pretty-print if requested (loses canonical property)
    if pretty:
        data = json.loads(json_content)
        json_content = to_canonical_json(data, pretty=True)

    # Write output
    with open(output, 'w', encoding='utf-8') as f:
        f.write(json_content)
        if not pretty:
            f.write('\n')  # Single trailing newline

    click.echo(f"Exported to {output}")
    click.echo(f"Memories: {json.loads(json_content)['memory_count']}")
    click.echo(f"SHA-256: {content_hash}")

    await pool.close()


@click.command()
@click.argument('file1')
@click.argument('file2')
async def diff(file1, file2):
    """Show differences between two exports."""

    with open(file1, 'r') as f:
        data1 = json.load(f)
    with open(file2, 'r') as f:
        data2 = json.load(f)

    memories1 = {m['id']: m for m in data1['memories']}
    memories2 = {m['id']: m for m in data2['memories']}

    all_ids = set(memories1.keys()) | set(memories2.keys())

    added = []
    removed = []
    changed = []

    for id in sorted(all_ids):
        if id not in memories1:
            added.append(memories2[id])
        elif id not in memories2:
            removed.append(memories1[id])
        elif memories1[id] != memories2[id]:
            changed.append((memories1[id], memories2[id]))

    if added:
        click.echo(click.style(f"\nAdded ({len(added)}):", fg='green'))
        for m in added[:5]:
            click.echo(f"  + {m['id']}: {m['topic_summary'][:50]}...")

    if removed:
        click.echo(click.style(f"\nRemoved ({len(removed)}):", fg='red'))
        for m in removed[:5]:
            click.echo(f"  - {m['id']}: {m['topic_summary'][:50]}...")

    if changed:
        click.echo(click.style(f"\nChanged ({len(changed)}):", fg='yellow'))
        for old, new in changed[:5]:
            click.echo(f"  ~ {old['id']}:")
            if old['topic_summary'] != new['topic_summary']:
                click.echo(f"      summary: '{old['topic_summary'][:30]}...' → '{new['topic_summary'][:30]}...'")
            if old['confidence'] != new['confidence']:
                click.echo(f"      confidence: {old['confidence']:.2f} → {new['confidence']:.2f}")

    if not (added or removed or changed):
        click.echo(click.style("No differences found.", fg='green'))
```

---

## 4. Usage Examples

### 4.1 Basic Export

```bash
# Export all memories
$ python scripts/memory_inspector.py export --all -o backup.json
Exported to backup.json
Memories: 1234
SHA-256: a1b2c3d4e5f6...

# Export specific user
$ python scripts/memory_inspector.py export --user-id 123456789 -o user_backup.json
```

### 4.2 Verify Backup

```bash
# Verify integrity
$ python scripts/memory_inspector.py export --verify backup.json
VALID: a1b2c3d4e5f6...

# Corrupted file
$ python scripts/memory_inspector.py export --verify corrupted.json
INVALID: Hash mismatch: expected a1b2c3d4..., got 9x8y7z6w...
```

### 4.3 Diff Exports

```bash
# Compare two exports
$ python scripts/memory_inspector.py diff before.json after.json

Added (3):
  + 1235: IGN: NewPlayer123...
  + 1236: User plays on TBA server...
  + 1237: User prefers dark oak...

Removed (1):
  - 1100: Old temporary memory...

Changed (5):
  ~ 1050:
      confidence: 0.95 → 0.85
  ~ 1051:
      summary: 'User building farm...' → 'User built witch farm...'
```

### 4.4 Git Integration

```bash
# Add export to git
$ python scripts/memory_inspector.py export --all -o data/memories.json
$ git add data/memories.json
$ git commit -m "Backup memories 2026-01-12"

# Later: see what changed
$ git diff data/memories.json
```

---

## 5. Testing

### 5.1 Unit Tests

```python
# tests/test_export.py

class TestCanonicalJSON:
    def test_deterministic_output(self):
        """Same input produces identical output."""
        data = {'b': 2, 'a': 1, 'timestamp': datetime(2026, 1, 12, 10, 30, 45, 123000)}

        output1 = to_canonical_json(data)
        output2 = to_canonical_json(data)

        assert output1 == output2

    def test_sorted_keys(self):
        """Keys are alphabetically sorted."""
        data = {'z': 1, 'a': 2, 'm': 3}
        output = to_canonical_json(data)
        assert output == '{"a":2,"m":3,"z":1}'

    def test_float_precision(self):
        """Floats are rounded to 6 decimals."""
        data = {'value': 0.1234567890123}
        output = to_canonical_json(data)
        assert '"value":0.123457' in output

    def test_timestamp_format(self):
        """Timestamps are ISO 8601 UTC."""
        data = {'time': datetime(2026, 1, 12, 10, 30, 45, 123000)}
        output = to_canonical_json(data)
        assert '"time":"2026-01-12T10:30:45.123Z"' in output


class TestExportVerify:
    def test_verify_valid(self, exporter, sample_export):
        """Valid exports pass verification."""
        is_valid, _ = exporter.verify_export(sample_export)
        assert is_valid

    def test_verify_corrupted(self, exporter, sample_export):
        """Corrupted exports fail verification."""
        corrupted = sample_export.replace('"confidence":0.85', '"confidence":0.99')
        is_valid, _ = exporter.verify_export(corrupted)
        assert not is_valid


class TestExportDiff:
    def test_detect_additions(self):
        """Detects added memories."""

    def test_detect_removals(self):
        """Detects removed memories."""

    def test_detect_changes(self):
        """Detects changed memories."""

    def test_identical_exports(self):
        """Identical exports show no diff."""
```

---

## 6. Rollout

### Phase 1: Implementation
1. Create `src/memory/export.py`
2. Update `memory_inspector.py` with new export/diff/verify commands
3. Add tests

### Phase 2: Testing
1. Run unit tests
2. Manual testing with real data
3. Verify git diff works correctly

### Phase 3: Deployment
1. Deploy (no migration needed)
2. Create initial canonical export
3. Commit to git as baseline

---

## 7. Future Enhancements

### 7.1 Import Support

```python
async def import_from_export(self, json_content: str) -> int:
    """Import memories from a canonical export."""
    data = json.loads(json_content)

    # Verify integrity
    is_valid, _ = self.verify_export(json_content)
    if not is_valid:
        raise ValueError("Export failed integrity check")

    count = 0
    for m in data['memories']:
        # Insert or update
        await self.db.execute(...)
        count += 1

    return count
```

### 7.2 Incremental Exports

Only export memories changed since last export:

```python
async def export_since(self, since: datetime) -> tuple[str, str]:
    """Export memories changed since timestamp."""
    rows = await self.db.fetch("""
        SELECT * FROM memories
        WHERE updated_at > $1
        ORDER BY id
    """, since)
    return self._format_export(rows)
```

### 7.3 Streaming Export for Large Datasets

```python
async def export_stream(self, output_file: str, batch_size: int = 1000):
    """Stream export for very large datasets."""
    # Write header
    # Stream batches
    # Calculate rolling hash
    # Write footer with final hash
```

---

## Appendix A: JSON Canonicalization Standards

- [RFC 8785: JSON Canonicalization Scheme (JCS)](https://datatracker.ietf.org/doc/html/rfc8785)
- [JSON Canonical Form](https://wiki.laptop.org/go/Canonical_JSON)

Our implementation is inspired by but simpler than JCS, focused on practical reproducibility for our use case.

## Appendix B: Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-01-12 | Slash + Claude | Initial specification |
