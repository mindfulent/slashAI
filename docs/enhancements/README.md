# slashAI Enhancement Specifications

This directory contains implementation specifications for slashAI features—both implemented and planned.

## Enhancement Index

| # | Name | Version | Status | Priority |
|---|------|---------|--------|----------|
| [001](./001_MEMORY_ATTRIBUTION.md) | Memory Attribution | v0.9.10 | ✅ Implemented | — |
| [002](./002_MEMORY_MANAGEMENT.md) | Memory Management | v0.9.11 | ✅ Implemented | — |
| [003](./003_AGENTIC_TOOLS.md) | Agentic Tools | v0.9.12 | ✅ Implemented | — |
| [004](./004_ANALYTICS.md) | Analytics | v0.9.16 | ✅ Implemented | — |
| [005](./005_REMINDERS.md) | Reminders | v0.9.17 | ✅ Implemented | — |
| [006](./006_META_MEMORY.md) | Meta Memory (Introspection) | v0.9.20 | ✅ Implemented | — |
| [007](./007_IMAGE_MEMORY_FIXES.md) | Image Memory Fixes | v0.9.22 | ✅ Implemented | — |
| [008](./008_DATABASE_BACKUP.md) | Database Backup | v0.10.0 | ✅ Implemented | — |
| [009](./009_GITHUB_DOC_READER.md) | GitHub Doc Reader | v0.10.0 | ✅ Implemented | — |
| [010](./010_HYBRID_SEARCH.md) | Hybrid Search | v0.10.0 | ✅ Implemented | — |
| [011](./011_CONFIDENCE_DECAY.md) | Confidence Decay | v0.10.1 | ✅ Implemented | — |
| [012](./012_DETERMINISTIC_EXPORT.md) | Deterministic Export | — | 📋 Draft | P3 Low |
| [013](./013_AUDIT_LOG.md) | Audit Log / Time-Travel | — | 📋 Draft | P2 Medium |
| — | [Rate Limiting](./RATELIMIT.md) | — | 📋 Draft | TBD |

## Quick Summary

### Implemented Features

**001 - Memory Attribution** (v0.9.10)
Fixed cross-user memory confusion by adding clear ownership attribution. Memories now show whose context they belong to.

**002 - Memory Management** (v0.9.11)
Discord slash commands (`/memories`) for users to view, search, and delete their own memories.

**003 - Agentic Tools** (v0.9.12)
Owner-only Discord tools allowing Claude to send messages, read channels, and describe images through natural conversation.

**004 - Analytics** (v0.9.16)
PostgreSQL-based analytics tracking with `/analytics` slash commands and CLI tools for monitoring bot usage.

**005 - Reminders** (v0.9.17)
Scheduled reminder system with natural language parsing, CRON support, and timezone awareness.

**006 - Meta Memory** (v0.9.20)
Memory introspection—Claude now sees metadata (relevance, confidence, privacy, recency) and can search memories explicitly.

**007 - Image Memory Fixes** (v0.9.22)
Fixed image retrieval gap, threshold calibration, and system prompt accuracy for the image memory system.

**008 - Database Backup** (v0.10.0)
On-demand backup system using GitHub Actions and DO Spaces. Backups stored in DigitalOcean Spaces with 30-day retention.

**009 - GitHub Doc Reader** (v0.10.0)
Read-only access to slashAI documentation via GitHub API. Enables the bot to reference its own specs.

**010 - Hybrid Search** (v0.10.0)
Combines lexical (BM25-style) and semantic search using Reciprocal Rank Fusion. Solves exact-match queries for player names, coordinates, mod names.

**011 - Confidence Decay** (v0.10.1)
Relevance-weighted decay based on retrieval frequency. Frequently-accessed memories resist decay; rarely-used memories fade. Background job runs every 6 hours.

### Upcoming Features

**012 - Deterministic Export** (P3)
Canonical JSON format for reproducible exports. Enables git-based tracking and backup verification.

**013 - Audit Log** (P2)
Trigger-based history table capturing all memory operations. Enables debugging and rollback.

## Implementation Roadmap

### Phase 1: v0.10.x ✅ Complete

```
├── 008: Database Backup ✅
├── 009: GitHub Doc Reader ✅
├── 010: Hybrid Search ✅
│   └── Migration 012: tsvector + GIN index
└── 011: Confidence Decay ✅
    └── Migration 013: decay_policy, retrieval_count
```

### Phase 2: v0.11.x

```
├── 012: Deterministic Export
├── 013: Audit Log
│   └── Migration 014: memories_history + trigger
└── Rate Limiting (TBD)
```

## Related Documentation

- **Research**: See [/docs/research/](../research/) for background analysis
  - [MEMVID_COMPARISON.md](../research/MEMVID_COMPARISON.md) - Comparison with Memvid
  - [MEMVID_LESSONS_ANALYSIS.md](../research/MEMVID_LESSONS_ANALYSIS.md) - Lessons learned

- **Reference**: Core documentation in [/docs/](../)
  - [MEMORY_TECHSPEC.md](../MEMORY_TECHSPEC.md) - Technical specification
  - [MEMORY_PRIVACY.md](../MEMORY_PRIVACY.md) - Privacy model

## Database Migrations

| Migration | Enhancement | Description |
|-----------|-------------|-------------|
| 001-004 | Core Memory | pgvector, memories table, sessions, indexes |
| 005-007 | Image Memory | build_clusters, image_observations, moderation |
| 008 | Memory Management | deletion_log |
| 009 | Analytics | analytics_events |
| 010-011 | Reminders | scheduled_reminders, user_settings |
| 012 | Hybrid Search (010) | tsvector column, GIN index, trigger |
| 013 | Confidence Decay (011) | decay_policy, retrieval_count, is_protected |
| 014 | Audit Log (013) | memories_history table with trigger |

*Note: GitHub Doc Reader (009) and Deterministic Export (012) require no migrations.*

## Version History

| Date | Change |
|------|--------|
| 2026-01-12 | Reorganized docs structure, added numbering scheme |
| 2026-01-12 | Added DATABASE_BACKUP_SPEC (008) |
| 2026-01-12 | Created Memvid-inspired specs (010-013) |
| 2026-01-12 | Added GitHub Doc Reader (009) |
