-- Keep evaluation knowledge physically queryable without allowing it to become
-- the production index by accident.  The database repeats the application
-- policy so direct SQL cannot bypass the alias/status separation.

DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT constraint_row.conname
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS relation
          ON relation.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'retrieval'
          AND relation.relname = 'index_versions'
          AND constraint_row.contype = 'c'
          AND pg_get_constraintdef(constraint_row.oid) ILIKE '%origin%'
    LOOP
        EXECUTE format(
            'ALTER TABLE retrieval.index_versions DROP CONSTRAINT %I',
            constraint_name
        );
    END LOOP;
END;
$$;

ALTER TABLE retrieval.index_versions
    ADD CONSTRAINT index_versions_origin_v2_check CHECK (
        origin IN ('publication', 'golden_fixture', 'evaluation_fixture')
    ),
    ADD CONSTRAINT index_versions_origin_build_id_v2_check CHECK (
        (
            origin = 'publication'
            AND published_build_id ~ '^published-knowledge:sha256:[0-9a-f]{64}$'
        )
        OR (
            origin = 'golden_fixture'
            AND published_build_id ~ '^retrieval-fixture:sha256:[0-9a-f]{64}$'
        )
        OR (
            origin = 'evaluation_fixture'
            AND published_build_id ~ '^evaluation-knowledge:sha256:[0-9a-f]{64}$'
        )
    );

DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT constraint_row.conname
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS relation
          ON relation.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'retrieval'
          AND relation.relname = 'index_entries'
          AND constraint_row.contype = 'c'
          AND pg_get_constraintdef(constraint_row.oid) ILIKE '%status%'
    LOOP
        EXECUTE format(
            'ALTER TABLE retrieval.index_entries DROP CONSTRAINT %I',
            constraint_name
        );
    END LOOP;
END;
$$;

ALTER TABLE retrieval.index_entries
    ADD CONSTRAINT index_entries_status_v2_check CHECK (
        status IN ('Draft', 'Baselined')
    );

CREATE FUNCTION retrieval.enforce_index_entry_origin_status()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    index_origin text;
    expected_status text;
BEGIN
    SELECT versions.origin
      INTO index_origin
      FROM retrieval.index_versions AS versions
     WHERE versions.index_version = NEW.index_version;

    IF index_origin IS NULL THEN
        RAISE EXCEPTION 'index version % does not exist', NEW.index_version
            USING ERRCODE = '23503';
    END IF;

    expected_status := CASE index_origin
        WHEN 'publication' THEN 'Baselined'
        WHEN 'golden_fixture' THEN 'Baselined'
        WHEN 'evaluation_fixture' THEN 'Draft'
        ELSE NULL
    END;

    IF expected_status IS NULL OR NEW.status <> expected_status THEN
        RAISE EXCEPTION
            'index origin % requires entry status %, got %',
            index_origin,
            expected_status,
            NEW.status
            USING ERRCODE = '23514';
    END IF;

    IF NEW.clause->>'status' IS DISTINCT FROM NEW.status THEN
        RAISE EXCEPTION
            'entry status % disagrees with clause JSON status %',
            NEW.status,
            NEW.clause->>'status'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER index_entries_enforce_origin_status
BEFORE INSERT OR UPDATE ON retrieval.index_entries
FOR EACH ROW EXECUTE FUNCTION retrieval.enforce_index_entry_origin_status();

CREATE FUNCTION retrieval.enforce_alias_origin_namespace()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    index_origin text;
BEGIN
    SELECT versions.origin
      INTO index_origin
      FROM retrieval.index_versions AS versions
     WHERE versions.index_version = NEW.index_version
       AND versions.state = NEW.index_state;

    IF index_origin IS NULL THEN
        RAISE EXCEPTION 'ready index version % does not exist', NEW.index_version
            USING ERRCODE = '23503';
    END IF;

    IF NEW.alias_name = 'current' AND index_origin <> 'publication' THEN
        RAISE EXCEPTION 'current alias accepts publication indexes only'
            USING ERRCODE = '23514';
    END IF;

    IF index_origin = 'publication' THEN
        IF NEW.alias_name ~ '^(staging|test)-' THEN
            RAISE EXCEPTION
                'publication index cannot use reserved alias %',
                NEW.alias_name
                USING ERRCODE = '23514';
        END IF;
    ELSIF index_origin = 'evaluation_fixture' THEN
        IF NEW.alias_name !~ '^staging-.+$' THEN
            RAISE EXCEPTION
                'evaluation fixture requires a staging-* alias, got %',
                NEW.alias_name
                USING ERRCODE = '23514';
        END IF;
    ELSIF index_origin = 'golden_fixture' THEN
        IF NEW.alias_name !~ '^test-.+$' THEN
            RAISE EXCEPTION
                'golden fixture requires a test-* alias, got %',
                NEW.alias_name
                USING ERRCODE = '23514';
        END IF;
    ELSE
        RAISE EXCEPTION 'unsupported index origin %', index_origin
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER current_index_aliases_enforce_origin_namespace
BEFORE INSERT OR UPDATE ON retrieval.current_index_aliases
FOR EACH ROW EXECUTE FUNCTION retrieval.enforce_alias_origin_namespace();
