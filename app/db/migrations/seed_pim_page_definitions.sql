-- ════════════════════════════════════════════════════════════════════════════
-- seed_pim_page_definitions.sql  —  PIM page definitions
--
-- Safe to re-run: ON CONFLICT (code) DO UPDATE
-- Run manually in psql / pgAdmin after schema.sql has been applied.
--
-- list_endpoint / upsert_endpoint / delete_endpoint are resolved live from
-- api_gateway.endpoints so the page definitions always reference real
-- registered endpoints. If an endpoint doesn't exist yet the field stays ''.
-- ════════════════════════════════════════════════════════════════════════════

-- Helper: look up a url_path from api_gateway.endpoints
-- Usage: ep_url('/gateway/attribute_groups/list')
CREATE OR REPLACE FUNCTION pg_temp.ep_url(p_path text)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        (SELECT url_path FROM api_gateway.endpoints
         WHERE  url_path = p_path AND status != 'deprecated'
         LIMIT  1),
        ''
    );
$$;


-- ── Attribute Groups ─────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'attribute_groups', 'Attribute Groups', 'Groupings of product attributes', 'Columns3',
    'attribute_groups', 'attribute_groups', 'string',
    'pim', 'Attr Groups', 2, 2, TRUE,
    '{"columns":[
        {"code":"code",       "visible":true,  "order":1},
        {"code":"name",       "visible":true,  "order":2},
        {"code":"sort_order", "visible":false, "order":3},
        {"code":"is_active",  "visible":true,  "order":4}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",       "order":1, "visible":true},
        {"code":"name",       "order":2, "visible":true, "multilingual":true},
        {"code":"sort_order", "order":3, "visible":true},
        {"code":"is_active",  "order":4, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/attribute_groups/list'),
    pg_temp.ep_url('/gateway/attribute_groups/upsert'),
    pg_temp.ep_url('/gateway/attribute_groups/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Brands ───────────────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'brands', 'Brands', 'Product brands and their classifications', 'Tag',
    'brands', 'brands', 'string',
    'pim', 'Brands', 10, 10, TRUE,
    '{"columns":[
        {"code":"code",        "visible":true,  "order":1},
        {"code":"name",        "visible":true,  "order":2},
        {"code":"brand_type",  "visible":true,  "order":3},
        {"code":"quality_tier","visible":true,  "order":4},
        {"code":"website_url", "visible":false, "order":5},
        {"code":"sort_order",  "visible":false, "order":6},
        {"code":"is_active",   "visible":true,  "order":7}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",        "order":1, "visible":true},
        {"code":"name",        "order":2, "visible":true, "multilingual":true},
        {"code":"brand_type",  "order":3, "visible":true},
        {"code":"description", "order":4, "visible":true, "multilingual":true},
        {"code":"quality_tier","order":5, "visible":true},
        {"code":"website_url", "order":6, "visible":true},
        {"code":"sort_order",  "order":7, "visible":true},
        {"code":"is_active",   "order":8, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/brands/list'),
    pg_temp.ep_url('/gateway/brands/upsert'),
    pg_temp.ep_url('/gateway/brands/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Families ─────────────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'families', 'Families', 'Product families grouping related attribute sets', 'FolderTree',
    'families', 'families', 'string',
    'pim', 'Families', 20, 20, TRUE,
    '{"columns":[
        {"code":"code",       "visible":true,  "order":1},
        {"code":"name",       "visible":true,  "order":2},
        {"code":"sort_order", "visible":false, "order":3},
        {"code":"is_active",  "visible":true,  "order":4}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",        "order":1, "visible":true},
        {"code":"name",        "order":2, "visible":true, "multilingual":true},
        {"code":"description", "order":3, "visible":true, "multilingual":true},
        {"code":"sort_order",  "order":4, "visible":true},
        {"code":"is_active",   "order":5, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/families/list'),
    pg_temp.ep_url('/gateway/families/upsert'),
    pg_temp.ep_url('/gateway/families/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Family Attributes ─────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'family_attributes', 'Family Attributes', 'Attribute assignments within product families', 'Table2',
    'family_attributes', 'family_attributes', 'string',
    'pim', 'Fam Attrs', 21, 21, TRUE,
    '{"columns":[
        {"code":"family_code",    "visible":true,  "order":1},
        {"code":"attribute_code", "visible":true,  "order":2},
        {"code":"sort_order",     "visible":false, "order":3},
        {"code":"is_required",    "visible":true,  "order":4}
    ]}'::jsonb,
    '{"fields":[
        {"code":"family_code",    "order":1, "visible":true, "ref_endpoint":"/gateway/families/list"},
        {"code":"attribute_code", "order":2, "visible":true, "ref_endpoint":"/gateway/attributes/list"},
        {"code":"sort_order",     "order":3, "visible":true},
        {"code":"is_required",    "order":4, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/family_attributes/list'),
    pg_temp.ep_url('/gateway/family_attributes/upsert'),
    pg_temp.ep_url('/gateway/family_attributes/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Locales ───────────────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'locales', 'Locales', 'Language and locale configurations', 'Globe',
    'locales', 'locales', 'string',
    'pim', 'Locales', 30, 30, TRUE,
    '{"columns":[
        {"code":"code",          "visible":true,  "order":1},
        {"code":"name",          "visible":true,  "order":2},
        {"code":"is_default",    "visible":true,  "order":3},
        {"code":"fallback_code", "visible":false, "order":4},
        {"code":"sort_order",    "visible":false, "order":5},
        {"code":"is_active",     "visible":true,  "order":6}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",          "order":1, "visible":true},
        {"code":"name",          "order":2, "visible":true, "multilingual":true},
        {"code":"is_default",    "order":3, "visible":true},
        {"code":"fallback_code", "order":4, "visible":true},
        {"code":"sort_order",    "order":5, "visible":true},
        {"code":"is_active",     "order":6, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/locales/list'),
    pg_temp.ep_url('/gateway/locales/upsert'),
    pg_temp.ep_url('/gateway/locales/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Regions ───────────────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'regions', 'Regions', 'Geographic regions and sub-regions', 'Globe',
    'regions', 'regions', 'string',
    'pim', 'Regions', 31, 31, TRUE,
    '{"columns":[
        {"code":"code",        "visible":true,  "order":1},
        {"code":"name",        "visible":true,  "order":2},
        {"code":"iso_code",    "visible":true,  "order":3},
        {"code":"locale_code", "visible":false, "order":4},
        {"code":"region_code", "visible":false, "order":5},
        {"code":"sort_order",  "visible":false, "order":6},
        {"code":"is_active",   "visible":true,  "order":7}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",        "order":1, "visible":true},
        {"code":"name",        "order":2, "visible":true,  "multilingual":true},
        {"code":"iso_code",    "order":3, "visible":true},
        {"code":"locale_code", "order":4, "visible":true,  "ref_endpoint":"/gateway/locales/list"},
        {"code":"region_code", "order":5, "visible":true,  "ref_endpoint":"/gateway/regions/list"},
        {"code":"sort_order",  "order":6, "visible":true},
        {"code":"is_active",   "order":7, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/regions/list'),
    pg_temp.ep_url('/gateway/regions/upsert'),
    pg_temp.ep_url('/gateway/regions/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Countries ─────────────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'countries', 'Countries', 'Country definitions with locale and region mappings', 'Globe',
    'countries', 'countries', 'string',
    'pim', 'Countries', 32, 32, TRUE,
    '{"columns":[
        {"code":"code",        "visible":true,  "order":1},
        {"code":"iso_code",    "visible":true,  "order":2},
        {"code":"name",        "visible":true,  "order":3},
        {"code":"locale_code", "visible":true,  "order":4},
        {"code":"region_code", "visible":true,  "order":5},
        {"code":"sort_order",  "visible":false, "order":6},
        {"code":"is_active",   "visible":true,  "order":7}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",        "order":1, "visible":true},
        {"code":"iso_code",    "order":2, "visible":true},
        {"code":"name",        "order":3, "visible":true, "multilingual":true},
        {"code":"locale_code", "order":4, "visible":true, "ref_endpoint":"/gateway/locales/list"},
        {"code":"region_code", "order":5, "visible":true, "ref_endpoint":"/gateway/regions/list"},
        {"code":"sort_order",  "order":6, "visible":true},
        {"code":"is_active",   "order":7, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/countries/list'),
    pg_temp.ep_url('/gateway/countries/upsert'),
    pg_temp.ep_url('/gateway/countries/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Unit Families ─────────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'unit_families', 'Unit Families', 'Groupings of measurement units (e.g. weight, length)', 'Package',
    'unit_families', 'unit_families', 'string',
    'pim', 'Unit Families', 40, 40, TRUE,
    '{"columns":[
        {"code":"code",       "visible":true,  "order":1},
        {"code":"name",       "visible":true,  "order":2},
        {"code":"sort_order", "visible":false, "order":3},
        {"code":"is_active",  "visible":true,  "order":4}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",       "order":1, "visible":true},
        {"code":"name",       "order":2, "visible":true, "multilingual":true},
        {"code":"sort_order", "order":3, "visible":true},
        {"code":"is_active",  "order":4, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/unit_families/list'),
    pg_temp.ep_url('/gateway/unit_families/upsert'),
    pg_temp.ep_url('/gateway/unit_families/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Units ─────────────────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'units', 'Units', 'Measurement units within unit families', 'SlidersHorizontal',
    'units', 'units', 'string',
    'pim', 'Units', 41, 41, TRUE,
    '{"columns":[
        {"code":"code",             "visible":true,  "order":1},
        {"code":"name",             "visible":true,  "order":2},
        {"code":"symbol",           "visible":true,  "order":3},
        {"code":"unit_family_code", "visible":true,  "order":4},
        {"code":"sort_order",       "visible":false, "order":5},
        {"code":"is_active",        "visible":true,  "order":6}
    ]}'::jsonb,
    '{"fields":[
        {"code":"code",             "order":1, "visible":true},
        {"code":"name",             "order":2, "visible":true, "multilingual":true},
        {"code":"symbol",           "order":3, "visible":true},
        {"code":"unit_family_code", "order":4, "visible":true, "ref_endpoint":"/gateway/unit_families/list"},
        {"code":"sort_order",       "order":5, "visible":true},
        {"code":"is_active",        "order":6, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/units/list'),
    pg_temp.ep_url('/gateway/units/upsert'),
    pg_temp.ep_url('/gateway/units/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();


-- ── Unit Conversion ───────────────────────────────────────────────────────────

INSERT INTO toolkit.page_definitions (
    code, title, description, icon,
    table_code, gateway_object, id_type,
    nav_section, nav_label, nav_order, sort_order, is_active,
    list_config, form_config,
    list_endpoint, upsert_endpoint, delete_endpoint
) VALUES (
    'unit_conversion', 'Unit Conversions', 'Conversion factors between measurement units', 'Database',
    'unit_conversion', 'unit_conversion', 'string',
    'pim', 'Unit Conversions', 42, 42, TRUE,
    '{"columns":[
        {"code":"from_unit_code", "visible":true, "order":1},
        {"code":"to_unit_code",   "visible":true, "order":2},
        {"code":"factor",         "visible":true, "order":3}
    ]}'::jsonb,
    '{"fields":[
        {"code":"from_unit_code", "order":1, "visible":true, "ref_endpoint":"/gateway/units/list"},
        {"code":"to_unit_code",   "order":2, "visible":true, "ref_endpoint":"/gateway/units/list"},
        {"code":"factor",         "order":3, "visible":true}
    ]}'::jsonb,
    pg_temp.ep_url('/gateway/unit_conversion/list'),
    pg_temp.ep_url('/gateway/unit_conversion/upsert'),
    pg_temp.ep_url('/gateway/unit_conversion/delete')
)
ON CONFLICT (code) DO UPDATE SET
    title           = EXCLUDED.title,
    description     = EXCLUDED.description,
    icon            = EXCLUDED.icon,
    table_code      = EXCLUDED.table_code,
    gateway_object  = EXCLUDED.gateway_object,
    id_type         = EXCLUDED.id_type,
    nav_section     = EXCLUDED.nav_section,
    nav_label       = EXCLUDED.nav_label,
    nav_order       = EXCLUDED.nav_order,
    sort_order      = EXCLUDED.sort_order,
    is_active       = EXCLUDED.is_active,
    list_config     = EXCLUDED.list_config,
    form_config     = EXCLUDED.form_config,
    list_endpoint   = EXCLUDED.list_endpoint,
    upsert_endpoint = EXCLUDED.upsert_endpoint,
    delete_endpoint = EXCLUDED.delete_endpoint,
    modified_at     = NOW();
