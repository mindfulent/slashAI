-- Migration 001: Enable pgvector extension
-- Required for vector similarity search

CREATE EXTENSION IF NOT EXISTS vector;
