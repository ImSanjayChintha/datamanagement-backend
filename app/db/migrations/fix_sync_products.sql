-- ════════════════════════════════════════════════════════════════════════════
-- fix_sync_products.sql
--
-- Replaces the toolkit-generated pim.sync_products with a version that
-- covers all writable columns including categories/tags/values.
-- category_codes (text[]) is derived automatically from the categories JSONB
-- — the payload does not need to include category_codes.
--
-- Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════════

DROP FUNCTION IF EXISTS pim.sync_products(jsonb, text);
CREATE OR REPLACE FUNCTION pim.sync_products(
    p_data       jsonb,
    p_audit_user text DEFAULT NULL
)
RETURNS jsonb AS $$
DECLARE
    v_count    int;
    v_deleted  int := 0;
    v_inserted int := 0;
BEGIN

    -- Stage: load payload into a session-scoped temp table.
    -- DROP first so the function is safe when called twice in the same session.
    DROP TABLE IF EXISTS _staging;
    CREATE TEMP TABLE _staging ON COMMIT DROP AS
    SELECT * FROM jsonb_to_recordset(p_data) AS x(
        id                  uuid,
        code                text,
        name                text,
        family_code         text,
        sort_order          integer,
        status              text,
        is_active           boolean,
        superseded_by_code  text,
        launched_at         date,
        discontinued_at     date,
        tags                jsonb,
        categories          jsonb
    );

    -- Check 1: empty payload
    SELECT COUNT(*) INTO v_count FROM _staging;
    IF v_count = 0 THEN
        RAISE EXCEPTION 'No data provided';
    END IF;

    -- Check 2: null sync keys [code]
    IF EXISTS (
        SELECT 1 FROM _staging WHERE code IS NULL
    ) THEN
        RAISE EXCEPTION 'Sync key columns cannot be null: code';
    END IF;

    -- Check 3: duplicate sync keys in payload
    IF EXISTS (
        SELECT code FROM _staging
        GROUP  BY code
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate sync key values in payload: code';
    END IF;

    -- Check 4: row count safety limit
    IF v_count > 5000 THEN
        RAISE EXCEPTION 'Batch too large: % rows (max 5000)', v_count;
    END IF;

    -- Delete existing records whose sync keys appear in staging
    DELETE FROM pim.products
    WHERE code IN (SELECT DISTINCT code FROM _staging);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    -- Insert staging rows, deriving category_codes from categories JSONB
    INSERT INTO pim.products (
        id,
        code,
        name,
        family_code,
        sort_order,
        status,
        is_active,
        superseded_by_code,
        launched_at,
        discontinued_at,
        tags,
        categories,
        category_codes,
        inserted_by,
        inserted_at,
        modified_by,
        modified_at
    )
    SELECT
        COALESCE(id, gen_random_uuid()),
        code,
        name,
        family_code,
        sort_order,
        status,
        is_active,
        superseded_by_code,
        launched_at,
        discontinued_at,
        tags,
        categories,
        -- derive category_codes from categories JSONB: extract each element's "code" field
        ARRAY(
            SELECT elem->>'code'
            FROM jsonb_array_elements(COALESCE(categories, '[]'::jsonb)) AS elem
        ),
        p_audit_user,
        NOW(),
        p_audit_user,
        NOW()
    FROM _staging;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'ok',       true,
        'deleted',  v_deleted,
        'inserted', v_inserted
    );

END;
$$ LANGUAGE plpgsql;
