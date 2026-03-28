-- Migration 017: Backfill agent_id and enforce strict isolation
-- All existing memories were created by the main slashAI bot.
-- Tag them with agent_id='slashai' so no memories have NULL agent_id.
-- Also updates hybrid_memory_search() to use strict equality (no NULL fallthrough).

UPDATE memories SET agent_id = 'slashai' WHERE agent_id IS NULL;

-- Recreate hybrid_memory_search with strict agent_id filter.
-- Must DROP first because return type changed in a previous migration.
DROP FUNCTION IF EXISTS hybrid_memory_search(text,vector,bigint,text,bigint,bigint,integer,integer,text);

CREATE OR REPLACE FUNCTION hybrid_memory_search(
    p_query TEXT,
    p_embedding vector(1024),
    p_user_id BIGINT,
    p_context_privacy TEXT,
    p_guild_id BIGINT DEFAULT NULL,
    p_channel_id BIGINT DEFAULT NULL,
    result_limit INT DEFAULT 5,
    candidate_limit INT DEFAULT 20,
    p_agent_id TEXT DEFAULT NULL
)
RETURNS TABLE (
    id INT,
    user_id BIGINT,
    topic_summary TEXT,
    raw_dialogue TEXT,
    memory_type TEXT,
    confidence FLOAT,
    privacy_level TEXT,
    origin_channel_id BIGINT,
    origin_guild_id BIGINT,
    source_count INT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    last_accessed_at TIMESTAMPTZ,
    agent_id TEXT,
    source_platform TEXT,
    similarity FLOAT,
    rrf_score FLOAT,
    semantic_rank INT,
    lexical_rank INT,
    reaction_summary TEXT
) AS $$
BEGIN
    RETURN QUERY
    WITH privacy_filter AS (
        SELECT m.*
        FROM memories m
        WHERE m.user_id = p_user_id
          AND (
            CASE p_context_privacy
                WHEN 'dm' THEN m.privacy_level IN ('dm', 'global')
                WHEN 'channel_restricted' THEN
                    (m.privacy_level = 'channel_restricted' AND m.origin_channel_id = p_channel_id)
                    OR m.privacy_level = 'global'
                WHEN 'guild_public' THEN
                    (m.privacy_level IN ('guild_public', 'global'))
                    OR (m.privacy_level = 'guild_public' AND m.origin_guild_id = p_guild_id)
                ELSE m.privacy_level = 'global'
            END
          )
          -- Strict agent isolation: each agent only sees its own memories
          AND m.agent_id = p_agent_id
    ),
    semantic AS (
        SELECT pf.id, ROW_NUMBER() OVER (ORDER BY pf.embedding <=> p_embedding) AS rank
        FROM privacy_filter pf
        ORDER BY pf.embedding <=> p_embedding
        LIMIT candidate_limit
    ),
    lexical AS (
        SELECT pf.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(pf.tsv, plainto_tsquery('english', p_query), 4) DESC) AS rank
        FROM privacy_filter pf
        WHERE pf.tsv @@ plainto_tsquery('english', p_query)
        LIMIT candidate_limit
    ),
    rrf AS (
        SELECT
            COALESCE(s.id, l.id) AS id,
            COALESCE(1.0 / (60 + s.rank), 0) + COALESCE(1.0 / (60 + l.rank), 0) AS score,
            COALESCE(s.rank::INT, 999) AS s_rank,
            COALESCE(l.rank::INT, 999) AS l_rank
        FROM semantic s
        FULL OUTER JOIN lexical l ON s.id = l.id
    )
    SELECT
        pf.id, pf.user_id, pf.topic_summary, pf.raw_dialogue,
        pf.memory_type, pf.confidence::FLOAT, pf.privacy_level,
        pf.origin_channel_id, pf.origin_guild_id, pf.source_count,
        pf.created_at, pf.updated_at, pf.last_accessed_at,
        pf.agent_id, pf.source_platform,
        r.score::FLOAT AS similarity,
        r.score::FLOAT AS rrf_score,
        r.s_rank AS semantic_rank,
        r.l_rank AS lexical_rank,
        NULL::TEXT AS reaction_summary
    FROM rrf r
    JOIN privacy_filter pf ON pf.id = r.id
    ORDER BY r.score DESC
    LIMIT result_limit;
END;
$$ LANGUAGE plpgsql;
