-- Migration 014d: Update hybrid_memory_search to return reaction_summary
-- Part of v0.12.0 - Reaction-Based Memory Signals

-- Replace the hybrid_memory_search function to include reaction_summary in output
CREATE OR REPLACE FUNCTION hybrid_memory_search(
    query_text TEXT,
    query_embedding vector(1024),
    p_user_id BIGINT,
    p_context_privacy TEXT,
    p_guild_id BIGINT DEFAULT NULL,
    p_channel_id BIGINT DEFAULT NULL,
    result_limit INT DEFAULT 5,
    candidate_limit INT DEFAULT 20
) RETURNS TABLE (
    id INT,
    user_id BIGINT,
    topic_summary TEXT,
    raw_dialogue TEXT,
    memory_type TEXT,
    privacy_level TEXT,
    confidence FLOAT,
    updated_at TIMESTAMPTZ,
    similarity FLOAT,
    rrf_score FLOAT,
    reaction_summary JSONB  -- NEW: Added for reaction-based retrieval boost
) AS $$
DECLARE
    k CONSTANT INT := 60;  -- RRF smoothing constant
BEGIN
    RETURN QUERY
    WITH
    -- Build privacy filter based on context
    privacy_filter AS (
        SELECT m.id
        FROM memories m
        WHERE
            CASE p_context_privacy
                WHEN 'dm' THEN
                    m.user_id = p_user_id
                WHEN 'channel_restricted' THEN
                    (m.user_id = p_user_id AND m.privacy_level = 'global')
                    OR (m.privacy_level = 'guild_public' AND m.origin_guild_id = p_guild_id)
                    OR (m.user_id = p_user_id AND m.privacy_level = 'channel_restricted'
                        AND m.origin_channel_id = p_channel_id)
                WHEN 'guild_public' THEN
                    (m.user_id = p_user_id AND m.privacy_level = 'global')
                    OR (m.privacy_level = 'guild_public' AND m.origin_guild_id = p_guild_id)
                ELSE FALSE
            END
    ),

    -- Lexical search using ts_rank_cd (cover density)
    lexical AS (
        SELECT
            m.id,
            ts_rank_cd(m.tsv, query) AS lex_score,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(m.tsv, query) DESC) AS lex_rank
        FROM memories m, plainto_tsquery('english', query_text) query
        WHERE m.tsv @@ query
          AND m.id IN (SELECT pf.id FROM privacy_filter pf)
        ORDER BY ts_rank_cd(m.tsv, query) DESC
        LIMIT candidate_limit
    ),

    -- Semantic search using pgvector cosine distance
    semantic AS (
        SELECT
            m.id,
            1 - (m.embedding <=> query_embedding) AS sem_score,
            ROW_NUMBER() OVER (ORDER BY m.embedding <=> query_embedding) AS sem_rank
        FROM memories m
        WHERE m.id IN (SELECT pf.id FROM privacy_filter pf)
        ORDER BY m.embedding <=> query_embedding
        LIMIT candidate_limit
    ),

    -- Reciprocal Rank Fusion
    fused AS (
        SELECT
            COALESCE(l.id, s.id) AS id,
            COALESCE(s.sem_score, 0) AS similarity,
            (COALESCE(1.0 / (k + l.lex_rank), 0) +
             COALESCE(1.0 / (k + s.sem_rank), 0)) AS rrf_score
        FROM lexical l
        FULL OUTER JOIN semantic s ON l.id = s.id
    )

    -- Final result with all memory fields including reaction_summary
    SELECT
        m.id,
        m.user_id,
        m.topic_summary,
        m.raw_dialogue,
        m.memory_type,
        m.privacy_level,
        COALESCE(m.confidence, 0.5)::FLOAT AS confidence,
        m.updated_at,
        f.similarity::FLOAT,
        f.rrf_score::FLOAT,
        m.reaction_summary  -- NEW
    FROM fused f
    JOIN memories m ON f.id = m.id
    ORDER BY f.rrf_score DESC
    LIMIT result_limit;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION hybrid_memory_search IS 'Hybrid search with RRF fusion and reaction metadata (v0.12.0)';
