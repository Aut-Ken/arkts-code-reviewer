CREATE SCHEMA IF NOT EXISTS document_projection;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS document_projection.schema_migrations (
    version text PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    checksum_sha256 text NOT NULL CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

CREATE TABLE document_projection.projection_versions (
    schema_version text NOT NULL CHECK (
        schema_version = 'document-projection-storage-v1'
    ),
    projection_version text PRIMARY KEY CHECK (
        projection_version ~ '^document-projection:sha256:[0-9a-f]{64}$'
    ),
    document_id text NOT NULL CHECK (
        document_id = btrim(document_id) AND document_id <> ''
    ),
    record_payload jsonb NOT NULL CHECK (jsonb_typeof(record_payload) = 'object'),
    l2_markdown text NOT NULL CHECK (l2_markdown <> ''),
    atom_count integer NOT NULL CHECK (atom_count >= 1),
    binding_count integer NOT NULL CHECK (binding_count >= 0),
    payload_sha256 text NOT NULL UNIQUE CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    use_scope text NOT NULL DEFAULT 'retrieval_projection_only_not_evidence' CHECK (
        use_scope = 'retrieval_projection_only_not_evidence'
    ),
    evidence_eligible boolean NOT NULL DEFAULT false CHECK (evidence_eligible = false),
    production_qualified boolean NOT NULL DEFAULT false CHECK (
        production_qualified = false
    ),
    qualification text NOT NULL DEFAULT
        'mechanically_verified_projection_not_semantically_reviewed' CHECK (
        qualification = 'mechanically_verified_projection_not_semantically_reviewed'
    ),
    state text NOT NULL DEFAULT 'building' CHECK (
        state IN ('building', 'mechanically_verified')
    ),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);

CREATE TABLE document_projection.projection_atoms (
    projection_version text NOT NULL REFERENCES
        document_projection.projection_versions(projection_version),
    atom_id text NOT NULL CHECK (
        atom_id ~ '^source-atom:sha256:[0-9a-f]{64}$'
    ),
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    kind text NOT NULL CHECK (kind = btrim(kind) AND kind <> ''),
    heading_path text[] NOT NULL DEFAULT ARRAY[]::text[],
    start_line integer NOT NULL CHECK (start_line >= 1),
    end_line integer NOT NULL CHECK (end_line >= start_line),
    body_text text NOT NULL CHECK (body_text <> ''),
    text_hash text NOT NULL CHECK (text_hash ~ '^sha256:[0-9a-f]{64}$'),
    required_context_atom_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    is_unclassified boolean NOT NULL,
    atom_payload jsonb NOT NULL CHECK (jsonb_typeof(atom_payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (projection_version, atom_id),
    UNIQUE (projection_version, ordinal),
    UNIQUE (projection_version, atom_id, ordinal)
);

CREATE TABLE document_projection.projection_bindings (
    projection_version text NOT NULL REFERENCES
        document_projection.projection_versions(projection_version),
    binding_id text NOT NULL CHECK (
        binding_id ~ '^projection-binding:sha256:[0-9a-f]{64}$'
    ),
    category_kind text NOT NULL CHECK (
        category_kind IN (
            'overview',
            'applicability',
            'api_and_symbols',
            'component_behavior',
            'constraint',
            'prohibition',
            'exception',
            'numeric_limit',
            'failure_behavior',
            'lifecycle_and_resource',
            'performance',
            'security_and_permission',
            'alternative_and_recommendation',
            'example',
            'diagnostic_and_observability'
        )
    ),
    display_title text NOT NULL CHECK (
        display_title = btrim(display_title) AND display_title <> ''
    ),
    subject_terms text[] NOT NULL DEFAULT ARRAY[]::text[],
    retrieval_aliases text[] NOT NULL DEFAULT ARRAY[]::text[],
    required_context_atom_ids text[] NOT NULL DEFAULT ARRAY[]::text[],
    binding_payload jsonb NOT NULL CHECK (jsonb_typeof(binding_payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (projection_version, binding_id)
);

CREATE TABLE document_projection.projection_binding_atoms (
    projection_version text NOT NULL,
    binding_id text NOT NULL,
    atom_id text NOT NULL,
    atom_ordinal integer NOT NULL CHECK (atom_ordinal >= 0),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (projection_version, binding_id, atom_id),
    UNIQUE (projection_version, binding_id, atom_ordinal),
    FOREIGN KEY (projection_version, binding_id)
        REFERENCES document_projection.projection_bindings(
            projection_version,
            binding_id
        ),
    FOREIGN KEY (projection_version, atom_id, atom_ordinal)
        REFERENCES document_projection.projection_atoms(
            projection_version,
            atom_id,
            ordinal
        )
);

CREATE INDEX projection_versions_document_id_idx
    ON document_projection.projection_versions (document_id);
CREATE INDEX projection_atoms_ordinal_idx
    ON document_projection.projection_atoms (projection_version, ordinal);
CREATE INDEX projection_atoms_heading_path_gin_idx
    ON document_projection.projection_atoms USING gin (heading_path);
CREATE INDEX projection_atoms_body_text_trgm_idx
    ON document_projection.projection_atoms USING gin (body_text gin_trgm_ops);
CREATE INDEX projection_bindings_category_kind_idx
    ON document_projection.projection_bindings (projection_version, category_kind);
CREATE INDEX projection_bindings_subject_terms_gin_idx
    ON document_projection.projection_bindings USING gin (subject_terms);
CREATE INDEX projection_bindings_retrieval_aliases_gin_idx
    ON document_projection.projection_bindings USING gin (retrieval_aliases);
CREATE INDEX projection_bindings_display_title_trgm_idx
    ON document_projection.projection_bindings USING gin (display_title gin_trgm_ops);
CREATE INDEX projection_binding_atoms_atom_idx
    ON document_projection.projection_binding_atoms (projection_version, atom_id);

CREATE FUNCTION document_projection.reject_immutable_row_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% rows are immutable; create a new projection version instead',
        TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

CREATE FUNCTION document_projection.reject_unclassified_binding_atom()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    atom_is_unclassified boolean;
BEGIN
    SELECT atoms.is_unclassified
      INTO atom_is_unclassified
      FROM document_projection.projection_atoms AS atoms
     WHERE atoms.projection_version = NEW.projection_version
       AND atoms.atom_id = NEW.atom_id
       AND atoms.ordinal = NEW.atom_ordinal;

    IF atom_is_unclassified IS NULL THEN
        RAISE EXCEPTION 'projection Atom % does not exist', NEW.atom_id
            USING ERRCODE = '23503';
    END IF;
    IF atom_is_unclassified THEN
        RAISE EXCEPTION 'unclassified Atom % cannot have a category binding', NEW.atom_id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION document_projection.require_building_projection()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    projection_state text;
BEGIN
    SELECT versions.state
      INTO projection_state
      FROM document_projection.projection_versions AS versions
     WHERE versions.projection_version = NEW.projection_version
       FOR UPDATE;

    IF projection_state IS NULL THEN
        RAISE EXCEPTION 'projection version % does not exist', NEW.projection_version
            USING ERRCODE = '23503';
    END IF;
    IF projection_state <> 'building' THEN
        RAISE EXCEPTION 'projection version % is sealed and cannot accept new rows',
            NEW.projection_version
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION document_projection.guard_projection_version_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    actual_atom_count bigint;
    actual_binding_count bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'projection_versions rows are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.state <> 'building' OR NEW.state <> 'mechanically_verified' THEN
        RAISE EXCEPTION 'projection version state may only transition building -> mechanically_verified'
            USING ERRCODE = '55000';
    END IF;
    IF (to_jsonb(NEW) - 'state') IS DISTINCT FROM (to_jsonb(OLD) - 'state') THEN
        RAISE EXCEPTION 'projection version sealing cannot change immutable metadata'
            USING ERRCODE = '55000';
    END IF;

    SELECT count(*) INTO actual_atom_count
      FROM document_projection.projection_atoms AS atoms
     WHERE atoms.projection_version = NEW.projection_version;
    SELECT count(*) INTO actual_binding_count
      FROM document_projection.projection_bindings AS bindings
     WHERE bindings.projection_version = NEW.projection_version;
    IF actual_atom_count <> NEW.atom_count OR actual_binding_count <> NEW.binding_count THEN
        RAISE EXCEPTION 'projection version row counts do not match its immutable metadata'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM document_projection.projection_atoms AS atoms
         WHERE atoms.projection_version = NEW.projection_version
           AND (
               (
                   atoms.is_unclassified
                   AND EXISTS (
                       SELECT 1
                         FROM document_projection.projection_binding_atoms AS links
                        WHERE links.projection_version = atoms.projection_version
                          AND links.atom_id = atoms.atom_id
                   )
               )
               OR (
                   NOT atoms.is_unclassified
                   AND NOT EXISTS (
                       SELECT 1
                         FROM document_projection.projection_binding_atoms AS links
                        WHERE links.projection_version = atoms.projection_version
                          AND links.atom_id = atoms.atom_id
                   )
               )
           )
    ) THEN
        RAISE EXCEPTION 'every projection Atom must be classified or explicitly unclassified'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM document_projection.projection_bindings AS bindings
         WHERE bindings.projection_version = NEW.projection_version
           AND NOT EXISTS (
               SELECT 1
                 FROM document_projection.projection_binding_atoms AS links
                WHERE links.projection_version = bindings.projection_version
                  AND links.binding_id = bindings.binding_id
           )
    ) THEN
        RAISE EXCEPTION 'every projection binding must reference at least one Atom'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE FUNCTION document_projection.require_projection_version_building_on_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.state <> 'building' THEN
        RAISE EXCEPTION 'new projection versions must start in building state'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER projection_binding_atoms_reject_unclassified
BEFORE INSERT OR UPDATE ON document_projection.projection_binding_atoms
FOR EACH ROW EXECUTE FUNCTION document_projection.reject_unclassified_binding_atom();

CREATE TRIGGER projection_versions_start_building
BEFORE INSERT ON document_projection.projection_versions
FOR EACH ROW EXECUTE FUNCTION
    document_projection.require_projection_version_building_on_insert();

CREATE TRIGGER projection_atoms_require_building
BEFORE INSERT ON document_projection.projection_atoms
FOR EACH ROW EXECUTE FUNCTION document_projection.require_building_projection();

CREATE TRIGGER projection_bindings_require_building
BEFORE INSERT ON document_projection.projection_bindings
FOR EACH ROW EXECUTE FUNCTION document_projection.require_building_projection();

CREATE TRIGGER projection_binding_atoms_require_building
BEFORE INSERT ON document_projection.projection_binding_atoms
FOR EACH ROW EXECUTE FUNCTION document_projection.require_building_projection();

CREATE TRIGGER schema_migrations_are_immutable
BEFORE UPDATE OR DELETE ON document_projection.schema_migrations
FOR EACH ROW EXECUTE FUNCTION document_projection.reject_immutable_row_mutation();

CREATE TRIGGER projection_versions_are_immutable
BEFORE UPDATE OR DELETE ON document_projection.projection_versions
FOR EACH ROW EXECUTE FUNCTION document_projection.guard_projection_version_mutation();

CREATE TRIGGER projection_atoms_are_immutable
BEFORE UPDATE OR DELETE ON document_projection.projection_atoms
FOR EACH ROW EXECUTE FUNCTION document_projection.reject_immutable_row_mutation();

CREATE TRIGGER projection_bindings_are_immutable
BEFORE UPDATE OR DELETE ON document_projection.projection_bindings
FOR EACH ROW EXECUTE FUNCTION document_projection.reject_immutable_row_mutation();

CREATE TRIGGER projection_binding_atoms_are_immutable
BEFORE UPDATE OR DELETE ON document_projection.projection_binding_atoms
FOR EACH ROW EXECUTE FUNCTION document_projection.reject_immutable_row_mutation();
