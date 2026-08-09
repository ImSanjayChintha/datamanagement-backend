-- ════════════════════════════════════════════════════════════════════════════
-- fix_upsert_products.sql
--
-- Replaces the toolkit-generated pim.upsert_products with a version that
-- covers all writable columns including categories/tags/values.
-- category_codes (text[]) is derived automatically from the categories JSONB
-- — the application only passes categories, not category_codes.
--
-- Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════════

DROP FUNCTION IF EXISTS pim.upsert_products(jsonb, text);
CREATE OR REPLACE FUNCTION pim.upsert_products(p_data jsonb, p_audit_user text DEFAULT NULL)
RETURNS jsonb AS $$
DECLARE
    v_upserted int := 0;
    v_new_id   text;
BEGIN
    WITH upserted AS (
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
            COALESCE(x.id, gen_random_uuid()),
            x.code,
            x.name,
            x.family_code,
            x.sort_order,
            x.status,
            x.is_active,
            x.superseded_by_code,
            x.launched_at,
            x.discontinued_at,
            x.tags,
            x.categories,
            -- derive category_codes from categories JSONB: extract each element's "code" field
            ARRAY(
                SELECT elem->>'code'
                FROM jsonb_array_elements(COALESCE(x.categories, '[]'::jsonb)) AS elem
            ),
            p_audit_user,
            NOW(),
            p_audit_user,
            NOW()
        FROM jsonb_to_recordset(p_data) AS x(
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
        )
        ON CONFLICT (id) DO UPDATE SET
            code               = EXCLUDED.code,
            name               = EXCLUDED.name,
            family_code        = EXCLUDED.family_code,
            sort_order         = EXCLUDED.sort_order,
            status             = EXCLUDED.status,
            is_active          = EXCLUDED.is_active,
            superseded_by_code = EXCLUDED.superseded_by_code,
            launched_at        = EXCLUDED.launched_at,
            discontinued_at    = EXCLUDED.discontinued_at,
            tags               = EXCLUDED.tags,
            categories         = EXCLUDED.categories,
            category_codes     = EXCLUDED.category_codes,
            modified_at        = NOW(),
            modified_by        = p_audit_user
        RETURNING id::text AS row_id
    )
    SELECT COUNT(*), (ARRAY_AGG(row_id))[1]
    INTO v_upserted, v_new_id
    FROM upserted;

    RETURN jsonb_build_object('ok', true, 'upserted', v_upserted, 'id', v_new_id);
END;
$$ LANGUAGE plpgsql;
