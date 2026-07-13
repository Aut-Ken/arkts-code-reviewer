CREATE SCHEMA IF NOT EXISTS retrieval;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS retrieval.schema_migrations (
    version text PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

CREATE TABLE retrieval.index_versions (
    schema_version text NOT NULL CHECK (schema_version = 'knowledge-index-v1'),
    index_version text PRIMARY KEY CHECK (
        index_version ~ '^knowledge-index:sha256:[0-9a-f]{64}$'
    ),
    origin text NOT NULL CHECK (origin IN ('publication', 'golden_fixture')),
    published_build_id text NOT NULL,
    source_bundle_id text NOT NULL CHECK (
        source_bundle_id ~ '^source-bundle:sha256:[0-9a-f]{64}$'
    ),
    feature_config_version text NOT NULL CHECK (
        feature_config_version ~ '^feature-config:sha256:[0-9a-f]{64}$'
    ),
    annotation_version text NOT NULL CHECK (annotation_version <> ''),
    catalog_version text NOT NULL CHECK (catalog_version <> ''),
    retrieval_version text NOT NULL CHECK (retrieval_version <> ''),
    retrieval_config_fingerprint text NOT NULL CHECK (
        retrieval_config_fingerprint ~ '^retrieval-config:sha256:[0-9a-f]{64}$'
    ),
    embedding_model text,
    embedding_version text,
    embedding_dimensions integer CHECK (embedding_dimensions > 0),
    api_symbols jsonb NOT NULL CHECK (jsonb_typeof(api_symbols) = 'array'),
    record_count integer NOT NULL CHECK (record_count >= 1),
    payload_sha256 text NOT NULL UNIQUE CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL DEFAULT 'ready' CHECK (state = 'ready'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE (index_version, state),
    CHECK (
        (
            origin = 'publication'
            AND published_build_id ~ '^published-knowledge:sha256:[0-9a-f]{64}$'
        )
        OR (
            origin = 'golden_fixture'
            AND published_build_id ~ '^retrieval-fixture:sha256:[0-9a-f]{64}$'
        )
    ),
    CHECK (
        (
            embedding_model IS NULL
            AND embedding_version IS NULL
            AND embedding_dimensions IS NULL
        )
        OR (
            embedding_model IS NOT NULL
            AND embedding_version IS NOT NULL
            AND embedding_dimensions IS NOT NULL
        )
    )
);

CREATE TABLE retrieval.index_entries (
    index_version text NOT NULL REFERENCES retrieval.index_versions(index_version),
    rule_id text NOT NULL CHECK (rule_id <> ''),
    rule_type text NOT NULL CHECK (rule_type <> ''),
    status text NOT NULL CHECK (status = 'Baselined'),
    authority text NOT NULL CHECK (authority <> ''),
    clause_text text NOT NULL CHECK (clause_text <> ''),
    clause jsonb NOT NULL CHECK (jsonb_typeof(clause) = 'object'),
    annotation jsonb NOT NULL CHECK (jsonb_typeof(annotation) = 'object'),
    domains text[] NOT NULL CHECK (cardinality(domains) > 0),
    retrieval_text text NOT NULL CHECK (retrieval_text <> ''),
    token_count integer NOT NULL CHECK (token_count >= 1),
    heading_path text[] NOT NULL DEFAULT ARRAY[]::text[],
    parent_context text,
    neighbor_rule_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    applicability jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
        jsonb_typeof(applicability) = 'object'
    ),
    source_ref jsonb NOT NULL CHECK (jsonb_typeof(source_ref) = 'object'),
    func_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    dimension_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    tags text[] NOT NULL DEFAULT ARRAY[]::text[],
    apis text[] NOT NULL DEFAULT ARRAY[]::text[],
    components text[] NOT NULL DEFAULT ARRAY[]::text[],
    decorators text[] NOT NULL DEFAULT ARRAY[]::text[],
    raw_keywords text[] NOT NULL DEFAULT ARRAY[]::text[],
    llm_keywords text[] NOT NULL DEFAULT ARRAY[]::text[],
    scenario text,
    embedding vector,
    embedding_dimensions integer CHECK (embedding_dimensions > 0),
    embedding_version text,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (index_version, rule_id),
    CHECK (
        (embedding IS NULL AND embedding_dimensions IS NULL AND embedding_version IS NULL)
        OR (
            embedding IS NOT NULL
            AND embedding_dimensions IS NOT NULL
            AND embedding_version IS NOT NULL
            AND vector_dims(embedding) = embedding_dimensions
        )
    )
);

CREATE TABLE retrieval.current_index_aliases (
    alias_name text PRIMARY KEY CHECK (alias_name = btrim(alias_name) AND alias_name <> ''),
    index_version text NOT NULL,
    index_state text NOT NULL DEFAULT 'ready' CHECK (index_state = 'ready'),
    switched_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY (index_version, index_state)
        REFERENCES retrieval.index_versions(index_version, state)
);

CREATE INDEX index_entries_status_idx
    ON retrieval.index_entries (index_version, status);
CREATE INDEX index_entries_func_ids_gin_idx
    ON retrieval.index_entries USING gin (func_ids);
CREATE INDEX index_entries_dimension_ids_gin_idx
    ON retrieval.index_entries USING gin (dimension_ids);
CREATE INDEX index_entries_tags_gin_idx
    ON retrieval.index_entries USING gin (tags);
CREATE INDEX index_entries_apis_gin_idx
    ON retrieval.index_entries USING gin (apis);
CREATE INDEX index_entries_components_gin_idx
    ON retrieval.index_entries USING gin (components);
CREATE INDEX index_entries_decorators_gin_idx
    ON retrieval.index_entries USING gin (decorators);
CREATE INDEX index_entries_domains_gin_idx
    ON retrieval.index_entries USING gin (domains);
CREATE INDEX index_entries_raw_keywords_gin_idx
    ON retrieval.index_entries USING gin (raw_keywords);
CREATE INDEX index_entries_applicability_gin_idx
    ON retrieval.index_entries USING gin (applicability jsonb_path_ops);
CREATE INDEX index_entries_clause_text_trgm_idx
    ON retrieval.index_entries USING gin (clause_text gin_trgm_ops);
CREATE INDEX index_entries_embedding_768_hnsw_idx
    ON retrieval.index_entries USING hnsw
    ((embedding::vector(768)) vector_cosine_ops)
    WHERE embedding_dimensions = 768;

CREATE FUNCTION retrieval.reject_immutable_row_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable; create a new index version instead', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER schema_migrations_are_immutable
BEFORE UPDATE OR DELETE ON retrieval.schema_migrations
FOR EACH ROW EXECUTE FUNCTION retrieval.reject_immutable_row_mutation();

CREATE TRIGGER index_versions_are_immutable
BEFORE UPDATE OR DELETE ON retrieval.index_versions
FOR EACH ROW EXECUTE FUNCTION retrieval.reject_immutable_row_mutation();

CREATE TRIGGER index_entries_are_immutable
BEFORE UPDATE OR DELETE ON retrieval.index_entries
FOR EACH ROW EXECUTE FUNCTION retrieval.reject_immutable_row_mutation();
