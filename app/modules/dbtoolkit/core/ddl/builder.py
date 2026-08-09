"""
Module: toolkit.ddl.builder
Purpose: SQL builders for CREATE TABLE, CREATE VIEW, and list/get FUNCTION
         statements for toolkit-managed PostgreSQL objects.

All column-level SQL is derived from the toolkit field catalog.  No columns
are auto-injected — every column must appear in the fields payload.
The audit trigger (fn_toolkit_set_audit) populates audit columns at runtime.
"""

import json as _json

from app.modules.dbtoolkit.core.constants import (
    DEFAULT_LANG,
    DEFAULT_SCHEMA,
    LANG_SESSION_VAR,
    AUDIT_TRIGGER_FN,
)
from app.modules.dbtoolkit.core.ddl.types import pg_type, safe_name, is_field_multilingual


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _qi(name: str) -> str:
    """Double-quote a SQL identifier so reserved keywords are always safe."""
    return f'"{name}"'


def _jsonb_lang_expr(field_code: str, prefix: str = "t") -> str:
    """Generate a COALESCE expression that extracts the current language from a JSONB column.

    Used for fields where the physical column is JSONB and stores translations
    directly as {"en": "value", "es": "valor", ...}.  This avoids the separate
    translations table entirely and works with UUID primary keys.

    prefix is the SQL alias or table reference to qualify the column (default 't').
    """
    return (
        f"coalesce(\n"
        f"        {prefix}.{_qi(field_code)}->>(nullif(current_setting('{LANG_SESSION_VAR}', true),'')),\n"
        f"        {prefix}.{_qi(field_code)}->>'{DEFAULT_LANG}'\n"
        f"    )"
    )


def _col_default(f: dict) -> str:
    """Return a ' default ...' SQL fragment for a field, or empty string.

    Args:
        f: Toolkit field dict with 'field_type' and optional 'default_value'.

    Returns:
        SQL default fragment (e.g. " default 'active'") or empty string.
    """
    dv = f.get("default_value")
    if dv is None:
        return ""
    # Multilingual fields are jsonb — skip text-style default_value.
    ftype = f["field_type"]
    if f.get("is_multilingual"):
        return ""
    if ftype in ("text", "textarea", "richtext", "slug", "email", "phone", "url",
                 "color", "file", "image", "inline_select", "select"):
        return f" default {dv!r}"
    if ftype == "sequence":
        return ""  # sequence default comes from the PostgreSQL sequence object
    if ftype == "toggle":
        return f" default {str(dv).lower()}"
    if ftype in ("json", "jsonb"):
        if isinstance(dv, (dict, list)):
            return f" default '{_json.dumps(dv)}'::jsonb"
        dv_str = str(dv).strip()
        try:
            _json.loads(dv_str)
        except (ValueError, TypeError):
            dv_str = "{}"
        return f" default '{dv_str}'::jsonb"
    if ftype == "text_array":
        return " default '{}'"
    return f" default {dv}"


def _qual(table: dict) -> tuple[str, str]:
    """Return (tbl, qualified_table_name) for the physical table.

    Args:
        table: Table dict with 'code' and optional 'schema_name'.

    Returns:
        Tuple of (bare_table_name, schema_qualified_table_name).
    """
    tbl    = safe_name(table["code"])
    schema = safe_name(table.get("schema_name") or DEFAULT_SCHEMA)
    qual   = f"{schema}.{tbl}"
    return tbl, qual


def _schema(table: dict) -> str:
    """Return the schema name for a table (always explicit, never empty).

    Args:
        table: Table dict with optional 'schema_name'.

    Returns:
        Schema name string — defaults to DEFAULT_SCHEMA if not set.
    """
    return safe_name(table.get("schema_name") or DEFAULT_SCHEMA)


def _physical_col_exprs(
    fields: list[dict],
    actual_cols: set[str] | None = None,
) -> list[str]:
    """Return t.{col} expressions for every physical column in field order.

    If actual_cols is supplied (column names from information_schema) only
    columns that actually exist in the DB table are emitted, preventing view
    creation from failing when a catalog entry has no matching DB column.

    Args:
        fields: Ordered list of toolkit field dicts.
        actual_cols: Optional set of physically existing column names.

    Returns:
        List of 't.{col}' expression strings.
    """
    exprs: list[str] = []
    for f in fields:
        if not f.get("code"):
            continue
        fc    = safe_name(f["code"])
        ftype = f["field_type"]
        if ftype == "daterange":
            if actual_cols is None or (f"{fc}_from" in actual_cols and f"{fc}_to" in actual_cols):
                exprs.extend([f"t.{_qi(fc+'_from')}", f"t.{_qi(fc+'_to')}"])
        elif pg_type(f) is not None and not f.get("is_multilingual"):
            if actual_cols is None or fc in actual_cols:
                exprs.append(f"t.{_qi(fc)}")
        # is_multilingual fields: rendered as COALESCE in the view body
        # virtual types (multiselect, computed) → skip
    return exprs


def _safe_drop_fn(schema: str, fn_name: str) -> str:
    """DO block that drops ALL overloads of a function by name.

    Using pg_proc instead of DROP FUNCTION IF EXISTS avoids the 'function name
    is not unique' error that occurs when a function signature has changed since
    the last DDL run.
    """
    return (
        "do $$\n"
        "declare r record;\n"
        "begin\n"
        "    for r in\n"
        f"        select p.oid::regprocedure as sig\n"
        f"        from   pg_proc p\n"
        f"        join   pg_namespace n on n.oid = p.pronamespace\n"
        f"        where  n.nspname = '{schema}' and p.proname = '{fn_name}'\n"
        "    loop\n"
        "        execute format('drop function %s cascade', r.sig);\n"
        "    end loop;\n"
        "end $$;"
    )


# ---------------------------------------------------------------------------
# BUILD CREATE TABLE
# ---------------------------------------------------------------------------

def build_create_table(table: dict, fields: list[dict]) -> str:
    """Generate a CREATE TABLE statement for a toolkit-managed table.

    Follows DBeaver style: lowercase keywords, inline FK constraints.
    All columns come from the fields payload — nothing is auto-injected.
    Junction tables are appended for multiselect fields.
    An audit trigger is attached to fire fn_toolkit_set_audit.

    Args:
        table: Table dict with 'code' and optional 'schema_name', 'has_label'.
        fields: Ordered list of toolkit field dicts.

    Returns:
        Complete SQL string ready for execution.
    """
    tbl, qual = _qual(table)
    schema    = _schema(table)

    col_defs:    list[str] = []
    constraints: list[str] = []
    comments:    list[str] = []

    user_codes: set[str] = set()

    for f in fields:
        fc    = safe_name(f["code"])
        ftype = f["field_type"]
        user_codes.add(fc)

        if ftype == "daterange":
            dv = _col_default(f)
            col_defs.append(f"{_qi(fc+'_from')}  date{dv}")
            col_defs.append(f"{_qi(fc+'_to')}    date")
            if f.get("description"):
                desc = f["description"].replace("'", "''")
                comments.append(f"comment on column {qual}.{_qi(fc+'_from')} is '{desc} (from)';")
                comments.append(f"comment on column {qual}.{_qi(fc+'_to')}   is '{desc} (to)';")
            continue

        pgt = pg_type(f)
        if pgt is None:
            continue

        # The 'id' column is always the UUID primary key — match by code OR type
        # so it works even if the common_fields row has the wrong field_type.
        if ftype == "id" or fc == "id":
            col_defs.append(
                f"{_qi(fc)} uuid not null default gen_random_uuid()\n"
                f"        constraint pk_{tbl} primary key"
            )
            continue

        if ftype == "sequence":
            col_defs.append(f"{_qi(fc)} bigserial")
            if f.get("description"):
                desc = f["description"].replace("'", "''")
                comments.append(f"comment on column {qual}.{_qi(fc)} is '{desc}';")
            continue

        if ftype == "uuid":
            col_defs.append(f"{_qi(fc)} uuid default gen_random_uuid()")
            if f.get("description"):
                desc = f["description"].replace("'", "''")
                comments.append(f"comment on column {qual}.{_qi(fc)} is '{desc}';")
            continue

        parts: list[str] = [f"{_qi(fc)} {pgt}"]

        if f.get("is_required") and ftype != "toggle":
            parts.append(" not null")
        if ftype == "toggle":
            parts.append(" not null")

        dv = _col_default(f)
        if dv:
            parts.append(dv)
        elif ftype == "toggle":
            parts.append(" default false")

        ref_tbl = safe_name(f.get("ref_table_code") or "")
        store   = safe_name(f.get("store_field") or "")
        if ftype == "select" and ref_tbl and store:
            cd_raw = (f.get("cascade_on_delete") or "restrict").lower()
            on_del = cd_raw if cd_raw in ("cascade", "set null", "set default", "no action", "restrict") else "restrict"
            parts.append(
                f"\n        constraint fk_{tbl}_{fc}"
                f"\n            references {ref_tbl} ({_qi(store)}) on delete {on_del}"
            )

        col_defs.append("".join(parts))

        if f.get("is_unique"):
            constraints.append(f"constraint uq_{tbl}_{fc} unique ({_qi(fc)})")

        if f.get("description"):
            desc = f["description"].replace("'", "''")
            comments.append(f"comment on column {qual}.{_qi(fc)} is '{desc}';")

    all_items = col_defs + constraints
    body = ",\n".join(f"    {item}" for item in all_items)
    sql  = f"create table if not exists {qual}\n(\n{body}\n);\n"

    if "is_active" in user_codes:
        sql += f'\ncreate index if not exists idx_{tbl}_is_active on {qual} ({_qi("is_active")});'
    if "sort_order" in user_codes and "code" in user_codes:
        sql += f'\ncreate index if not exists idx_{tbl}_sort on {qual} ({_qi("sort_order")}, {_qi("code")});'

    if comments:
        sql += "\n"
        for c in comments:
            sql += f"\n{c}"

    sql += f"""

drop trigger if exists trg_{tbl}_audit on {qual};
create trigger trg_{tbl}_audit
    before insert or update on {qual}
    for each row execute function {AUDIT_TRIGGER_FN}();"""

    # has_children trigger — only when the table has both parent_code and has_children fields.
    # Maintains the boolean automatically on each INSERT / UPDATE / DELETE without requiring
    # a full-table subquery in the view.
    if "parent_code" in user_codes and "has_children" in user_codes:
        sql += f"""

create or replace function {schema}.fn_{tbl}_has_children()
returns trigger language plpgsql as $$
begin
    if tg_op = 'INSERT' then
        if new.{_qi('parent_code')} is not null and new.{_qi('parent_code')} <> '' then
            update {qual} set {_qi('has_children')} = true
            where {_qi('code')} = new.{_qi('parent_code')};
        end if;
    elsif tg_op = 'DELETE' then
        if old.{_qi('parent_code')} is not null and old.{_qi('parent_code')} <> '' then
            update {qual}
            set {_qi('has_children')} = exists(
                select 1 from {qual} c2 where c2.{_qi('parent_code')} = old.{_qi('parent_code')}
            )
            where {_qi('code')} = old.{_qi('parent_code')};
        end if;
    elsif tg_op = 'UPDATE' and old.{_qi('parent_code')} is distinct from new.{_qi('parent_code')} then
        if old.{_qi('parent_code')} is not null and old.{_qi('parent_code')} <> '' then
            update {qual}
            set {_qi('has_children')} = exists(
                select 1 from {qual} c2 where c2.{_qi('parent_code')} = old.{_qi('parent_code')}
            )
            where {_qi('code')} = old.{_qi('parent_code')};
        end if;
        if new.{_qi('parent_code')} is not null and new.{_qi('parent_code')} <> '' then
            update {qual} set {_qi('has_children')} = true
            where {_qi('code')} = new.{_qi('parent_code')};
        end if;
    end if;
    return coalesce(new, old);
end;
$$;

drop trigger if exists trg_{tbl}_has_children on {qual};
create trigger trg_{tbl}_has_children
    after insert or update or delete on {qual}
    for each row execute function {schema}.fn_{tbl}_has_children();"""

    # Junction tables for multiselect fields
    for f in fields:
        if f["field_type"] != "multiselect":
            continue
        fc      = safe_name(f["code"])
        jt      = f"{tbl}_{fc}_links"
        ref_tbl = safe_name(f.get("ref_table_code") or "")
        store   = safe_name(f.get("store_field") or "")
        cd_raw  = (f.get("cascade_on_delete") or "restrict").lower()
        on_del  = cd_raw if cd_raw in ("cascade", "set null", "set default", "no action", "restrict") else "restrict"
        fk_line = (
            f",\n    constraint fk_{jt}_target"
            f"\n        foreign key (target_value) references {ref_tbl} ({_qi(store)}) on delete {on_del}"
            if ref_tbl and store else ""
        )
        source_col = (
            f"uuid not null\n        constraint fk_{jt}_source references {qual} (id) on delete cascade"
            if "id" in user_codes else
            "uuid not null"
        )
        sql += f"""

-- {f['code']}: multiselect junction table
create table if not exists {jt}
(
    id           bigserial
        constraint pk_{jt} primary key,
    source_id    {source_col},
    target_value text not null{fk_line},
    sort_order   integer not null default 0,
    constraint uq_{jt}_pair unique (source_id, target_value)
);

create index if not exists idx_{jt}_source on {jt} (source_id);"""
        if ref_tbl and store:
            sql += f"\ncreate index if not exists idx_{jt}_target on {jt} (target_value);"

    return sql


# ---------------------------------------------------------------------------
# BUILD CREATE VIEW
# ---------------------------------------------------------------------------

def build_create_view(
    table: dict,
    fields: list[dict],
    actual_cols: set[str] | None = None,
    ref_fields_map: dict[str, list[dict]] | None = None,
    ref_schema_map: dict[str, str] | None = None,
) -> str:
    """Generate a CREATE OR REPLACE VIEW statement for a toolkit table.

    Uses an explicit physical column list (not t.*) to avoid duplicate-column
    errors when virtual fields shadow physical columns or when daterange fields
    add _from/_to variants.

    Multilingual fields (is_multilingual=true) are resolved via COALESCE against
    the jsonb column — coalesce(t.col->>current_lang, t.col->>'en').

    Args:
        table: Table dict with 'code', optional 'schema_name', 'has_label'.
        fields: Ordered toolkit field dicts.
        actual_cols: Optional set of column names confirmed to exist in the DB.
        ref_fields_map: Optional {ref_table_code: [field_dicts]} used to detect
            whether referenced display fields are multilingual.

    Returns:
        SQL string beginning with 'drop view if exists ... cascade;'.
    """
    tbl, qual = _qual(table)
    schema    = _schema(table)
    view      = f"{schema}.v_{tbl}"

    def _exists(col: str) -> bool:
        return actual_cols is None or col in actual_cols

    phys_codes = {
        safe_name(f["code"])
        for f in fields
        if f.get("code") and pg_type(f) is not None
    }

    user_codes = {safe_name(f["code"]) for f in fields if not f.get("is_system")}

    # Detect the primary key column (field_type "id" → UUID PK).
    # Used in multiselect link-table subqueries instead of the hardcoded literal "id".
    _pk_field = next(
        (f for f in fields if f.get("field_type") == "id" or safe_name(f.get("code", "")) == "id"),
        None,
    )
    pk_col: str | None = safe_name(_pk_field["code"]) if _pk_field else None

    phys = _physical_col_exprs(fields, actual_cols)
    select_cols: list[str] = ["    " + ", ".join(phys)] if phys else ["    t.*"]
    joins: list[str] = []
    join_idx = 1

    # label translation — only when the label field is actually multilingual
    label_field = next(
        (f for f in fields if not f.get("is_system") and safe_name(f.get("code", "")) == "label"),
        None,
    )
    label_is_multilingual = label_field is not None and label_field.get("is_multilingual")
    if table.get("has_label") and "id" in user_codes and label_is_multilingual and _exists("id"):
        if _exists("label"):
            # JSONB inline storage: {"en": "…", "es": "…"} — extract current language
            select_cols.append(f"    ,{_jsonb_lang_expr('label')} as {_qi('label')}")

    for f in fields:
        if not f.get("code"):
            continue
        fc    = safe_name(f["code"])
        ftype = f["field_type"]

        # Multilingual (jsonb column, all languages stored inline)
        if f.get("is_multilingual"):
            already_emitted = (
                fc == "label"
                and table.get("has_label")
                and "id" in user_codes
                and label_is_multilingual
            )
            if not already_emitted:
                if _exists(fc):
                    select_cols.append(
                        f"    ,{_jsonb_lang_expr(fc)} as {_qi(fc)}"
                    )
            join_idx += 1
            continue

        # Multiselect — stored values array + optional translated display array
        if ftype == "multiselect":
            jt      = f"{tbl}_{fc}_links"
            ref_tbl = safe_name(f.get("ref_table_code") or "")
            disp    = safe_name(f.get("display_field") or "label")
            store   = safe_name(f.get("store_field") or "code")
            if not pk_col or not _exists(pk_col):
                # No PK column found — emit NULLs so the view is at least queryable
                select_cols.append(f"    ,NULL::text[] as {_qi(fc)}")
                if ref_tbl:
                    select_cols.append(f"    ,NULL::text[] as {_qi(fc+'_display')}")
                join_idx += 1
                continue
            pk_ref = f"t.{_qi(pk_col)}"
            select_cols.append(
                f"    ,(select array_agg(jt.target_value order by jt.sort_order)"
                f" from {jt} jt where jt.source_id={pk_ref}) as {_qi(fc)}"
            )
            if ref_tbl:
                if is_field_multilingual(ref_fields_map, ref_tbl, disp):
                    select_cols.append(
                        f"    ,(select array_agg(\n"
                        f"        {_jsonb_lang_expr(disp, 'ref')}"
                        f" order by jt.sort_order)\n"
                        f"     from {jt} jt left join {ref_tbl} ref on ref.{_qi(store)}=jt.target_value\n"
                        f"     where jt.source_id={pk_ref}) as {_qi(fc+'_display')}"
                    )
                elif ref_fields_map is not None:
                    select_cols.append(
                        f"    ,(select array_agg(ref.{_qi(disp)} order by jt.sort_order)"
                        f" from {jt} jt left join {ref_tbl} ref on ref.{_qi(store)}=jt.target_value"
                        f" where jt.source_id={pk_ref}) as {_qi(fc+'_display')}"
                    )
                else:
                    select_cols.append(
                        f"    ,(select array_agg(ref.{_qi(disp)} order by jt.sort_order)"
                        f" from {jt} jt left join v_{ref_tbl} ref on ref.{_qi(store)}=jt.target_value"
                        f" where jt.source_id={pk_ref}) as {_qi(fc+'_display')}"
                    )
            join_idx += 1
            continue

        # Computed — inline expression
        if ftype == "computed":
            cfg  = f.get("config") or {}
            expr = cfg.get("expression", "null")
            select_cols.append(f"    ,({expr}) as {_qi(fc)}")
            continue

        # Daterange — physical cols already enumerated above; skip
        if ftype == "daterange":
            continue

        # Select with reference — display column with optional translation
        if ftype == "select" and f.get("ref_table_code"):
            # Skip if the FK column doesn't physically exist yet
            if actual_cols is not None and fc not in actual_cols:
                continue
            ref_code  = f["ref_table_code"]
            ref_tbl   = safe_name(ref_code)
            ref_alias = f"ref_{join_idx}"
            disp      = safe_name(f.get("display_field") or "label")
            store     = safe_name(f.get("store_field") or "code")

            # Use schema-qualified name when the ref table lives outside public
            ref_schema = (ref_schema_map or {}).get(ref_code) or DEFAULT_SCHEMA
            ref_qual   = f"{safe_name(ref_schema)}.{ref_tbl}" if ref_schema != DEFAULT_SCHEMA else ref_tbl

            # Guard: if the stored display_field doesn't exist in the referenced table, fall
            # back to 'code'. Prevents UndefinedColumnError when the reference config has a
            # stale/incorrect display_field value (e.g. "label" on a table that only has "name").
            if ref_fields_map is not None and ref_tbl in ref_fields_map:
                ref_field_codes = {safe_name(rf.get("code", "")) for rf in ref_fields_map[ref_tbl]}
                if disp not in ref_field_codes:
                    disp = "code" if "code" in ref_field_codes else disp

            if ref_fields_map is not None and ref_tbl in ref_fields_map:
                # Physical table confirmed present — find the display field definition so we
                # can pick the right multilingual expression (JSONB inline coalesce vs plain text).
                ref_disp_fld = next(
                    (rf for rf in ref_fields_map[ref_tbl]
                     if safe_name(rf.get("code", "")) == disp),
                    None,
                )
                ref_disp_is_jsonb = (
                    ref_disp_fld is not None and ref_disp_fld.get("is_multilingual")
                )
                joins.append(f"left join {ref_qual} {ref_alias} on {ref_alias}.{_qi(store)} = t.{_qi(fc)}")
                if ref_disp_is_jsonb:
                    # JSONB column stores translations inline — extract current language
                    select_cols.append(
                        f"    ,{_jsonb_lang_expr(disp, ref_alias)} as {_qi(fc+'_'+disp)}"
                    )
                else:
                    select_cols.append(f"    ,{ref_alias}.{_qi(disp)} as {_qi(fc+'_'+disp)}")
            else:
                # Referenced table not confirmed to exist in the DB — skip the display join.
                # An unqualified v_{ref_tbl} would break for cross-schema references.
                # The stored FK value (already in phys cols) is still visible.
                # Re-save this table after the referenced table has been created.
                pass

            join_idx += 1
            continue

        # inline_select — resolve option label via LEFT JOIN on toolkit_field_options.
        # Use a subquery to look up field_id by table+field code so the view is
        # portable across environments (no hardcoded primary keys).
        if ftype == "inline_select" and _exists(fc):
            opt_alias  = f"opt_{join_idx}"
            field_id_q = (
                f"(select tf.id from toolkit.toolkit_fields tf"
                f" join toolkit.toolkit_tables tt on tt.id = tf.table_id"
                f" where tt.code = '{tbl}' and tf.code = '{fc}' limit 1)"
            )
            joins.append(
                f"left join toolkit.toolkit_field_options {opt_alias}"
                f" on {opt_alias}.field_id = {field_id_q}"
                f" and {opt_alias}.code = t.{_qi(fc)}"
            )
            select_cols.append(f"    ,{opt_alias}.label as {_qi(fc + '_label')}")
            join_idx += 1

        # toggle — fetch true/false labels from toolkit_fields.config via LATERAL.
        # No outer-table reference in the LATERAL → PostgreSQL memoize node evaluates
        # it once per query regardless of row count. The CASE in the SELECT picks the
        # right label per row from the cached pair — trivial per-row cost.
        elif ftype == "toggle" and _exists(fc):
            lbl_alias = f"lbl_{join_idx}"
            lang_x    = f"nullif(current_setting('{LANG_SESSION_VAR}', true), '')"
            joins.append(
                f"left join lateral (\n"
                f"    select\n"
                f"        coalesce(\n"
                f"            (tf.config->'true_label_i18n')->>{lang_x},\n"
                f"            (tf.config->'true_label_i18n')->>'en',\n"
                f"            tf.config->>'true_label',\n"
                f"            'Yes'\n"
                f"        ) as true_lbl,\n"
                f"        coalesce(\n"
                f"            (tf.config->'false_label_i18n')->>{lang_x},\n"
                f"            (tf.config->'false_label_i18n')->>'en',\n"
                f"            tf.config->>'false_label',\n"
                f"            'No'\n"
                f"        ) as false_lbl\n"
                f"    from toolkit.toolkit_fields tf\n"
                f"    join toolkit.toolkit_tables tt on tt.id = tf.table_id\n"
                f"    where tt.code = '{tbl}' and tf.code = '{fc}'\n"
                f"    limit 1\n"
                f") {lbl_alias} on true"
            )
            select_cols.append(
                f"    ,case when t.{_qi(fc)}"
                f" then {lbl_alias}.true_lbl"
                f" else {lbl_alias}.false_lbl"
                f" end as {_qi(fc + '_label')}"
            )
            join_idx += 1

    # has_children — fallback EXISTS subquery for tables without a physical has_children column.
    # When has_children is a physical boolean (maintained by the trigger emitted by
    # build_create_table), it is already in phys_codes and _physical_col_exprs picks it up,
    # so we skip the subquery to avoid a duplicate column and an unnecessary correlated scan.
    if "parent_code" in phys_codes and _exists("parent_code") and _exists("code") \
            and "has_children" not in phys_codes:
        active_filter = (
            f" and c2.{_qi('is_active')} = true"
            if "is_active" in phys_codes and _exists("is_active") else ""
        )
        select_cols.append(
            f"    ,exists(select 1 from {qual} c2"
            f" where c2.{_qi('parent_code')} = t.{_qi('code')}{active_filter})"
            f" as {_qi('has_children')}"
        )

    cols_str  = "\n".join(select_cols)
    joins_str = ("\n" + "\n".join(joins)) if joins else ""

    return (
        f"drop view if exists {view} cascade;\n"
        f"create or replace view {view} as\n"
        f"select\n{cols_str}\n"
        f"from {qual} t{joins_str};"
    )


# ---------------------------------------------------------------------------
# BUILD LIST FUNCTION  (thin wrapper — delegates to toolkit.fn_list engine)
# ---------------------------------------------------------------------------

def build_list_function(
    table: dict,
    fields: list[dict] | None = None,
    actual_cols: set[str] | None = None,
) -> str:
    """Generate DROP + CREATE FUNCTION for fn_list_{table}.

    Thin wrapper delegating to toolkit.fn_list with the table name hard-coded.
    """
    tbl    = safe_name(table["code"])
    schema = _schema(table)
    fn     = f"{schema}.fn_list_{tbl}"
    drop   = _safe_drop_fn(schema, f"fn_list_{tbl}")
    return (
        f"{drop}\n"
        f"CREATE OR REPLACE FUNCTION {fn}("
        f"p_filters jsonb DEFAULT '{{}}'::jsonb, "
        f"p_sort jsonb DEFAULT NULL::jsonb, "
        f"p_lang text DEFAULT '{DEFAULT_LANG}'::text, "
        f"p_limit integer DEFAULT 25, "
        f"p_offset integer DEFAULT 0, "
        f"p_with_total boolean DEFAULT true)\n"
        f" RETURNS jsonb\n"
        f" LANGUAGE sql\n"
        f" SET search_path TO 'pg_catalog', '{schema}', 'toolkit'\n"
        f"AS $function$\n"
        f"    select toolkit.fn_list('{tbl}', p_filters, p_sort, p_lang, p_limit, p_offset, p_with_total);\n"
        f"$function$\n"
        f";"
    )


# ---------------------------------------------------------------------------
# BUILD GET FUNCTION  (thin wrapper — delegates to toolkit.fn_get engine)
# ---------------------------------------------------------------------------

def build_get_function(
    table: dict,
    fields: list[dict] | None = None,
    actual_cols: set[str] | None = None,
) -> str:
    """Generate DROP + CREATE FUNCTION for fn_get_{table}.

    Thin wrapper delegating to toolkit.fn_get with the table name hard-coded.
    Accepts a jsonb identity ({"id": "uuid"} or {"code": "str"}) and a lang.

    `fields` and `actual_cols` are accepted for backward compatibility but
    are not used.
    """
    tbl    = safe_name(table["code"])
    schema = _schema(table)
    fn     = f"{schema}.fn_get_{tbl}"
    drop   = _safe_drop_fn(schema, f"fn_get_{tbl}")
    list_fn = f"{schema}.fn_list_{tbl}"
    return (
        f"{drop}\n"
        f"create or replace function {fn}(\n"
        f"    p_id   jsonb,\n"
        f"    p_lang text  default '{DEFAULT_LANG}'\n"
        f") returns jsonb language sql volatile\n"
        f"set search_path = pg_catalog, {schema}, toolkit as $$\n"
        f"    select {list_fn}(p_id, NULL::jsonb, p_lang, 1, 0, false) -> 'rows' -> 0;\n"
        f"$$;"
    )


# ---------------------------------------------------------------------------
# BUILD WRITE FUNCTIONS (insert / upsert / sync / delete)
# ---------------------------------------------------------------------------

def build_write_functions(table: dict, fields: list[dict]) -> str:
    """Generate four PL/pgSQL write functions for a toolkit-managed table.

    Functions generated:
        insert_{tbl}(p_data jsonb, p_audit_user text)  → jsonb  {ok, inserted}
        upsert_{tbl}(p_data jsonb, p_audit_user text)  → jsonb  {ok, inserted, updated, upserted}
        sync_{tbl}(p_data jsonb, p_audit_user text)    → jsonb  {ok, inserted, updated, deactivated, upserted}
        delete_{tbl}(p_filter jsonb, p_audit_user text)→ jsonb  {ok, deleted}

    All accept a single-object or array payload in p_data.
    The audit trigger (fn_toolkit_set_audit) handles inserted_at/modified_at automatically.

    Args:
        table: Table dict with 'code' and optional 'schema_name'.
        fields: Ordered toolkit field dicts including system fields.

    Returns:
        SQL string with DROP + CREATE for all four functions.
    """
    tbl, qual = _qual(table)
    schema    = _schema(table)

    _SKIP = frozenset({
        "id", "inserted_at", "modified_at", "inserted_by", "modified_by",
        "created_at", "updated_at",
    })

    writable = [
        f for f in fields
        if f.get("code")
        and safe_name(f["code"]) not in _SKIP
        and pg_type(f) is not None
        and f.get("field_type") not in ("multiselect", "computed", "daterange")
        # is_multilingual fields have a physical jsonb column — include them so the
        # upsert function can write the full {"en": ..., "es": ...} object.
        # pg_type() already returns 'jsonb' for them regardless of base field_type.
    ]

    user_codes    = {safe_name(f["code"]) for f in fields if not f.get("is_system") and f.get("code")}
    has_code      = "code"      in user_codes
    has_is_active = "is_active" in user_codes

    col_names     = [safe_name(f["code"]) for f in writable]
    pgt_map       = {safe_name(f["code"]): (pg_type(f) or "text") for f in writable}

    col_list      = ", ".join(_qi(c) for c in col_names)
    jtr_type_list = ", ".join(f"{_qi(c)} {pgt_map[c]}" for c in col_names)

    # When a JSON key is absent, jsonb_to_recordset returns NULL — which would
    # violate NOT NULL constraints on boolean/numeric columns that have DB defaults.
    # Use COALESCE so missing keys fall back to the column's natural default.
    _BOOL_TYPES = frozenset({"boolean"})
    _INT_TYPES  = frozenset({"integer", "bigint", "smallint", "numeric", "real", "double precision"})

    def _select_expr(col: str, pgt: str) -> str:
        if pgt in _BOOL_TYPES:
            return f"COALESCE(x.{_qi(col)}, false)"
        if pgt in _INT_TYPES:
            return f"COALESCE(x.{_qi(col)}, 0)"
        return f"x.{_qi(col)}"

    select_from_x = ", ".join(_select_expr(c, pgt_map[c]) for c in col_names)

    # Conflict target: 'code' (unique natural key) preferred, else 'id'
    conflict_col  = "code" if has_code else "id"
    update_cols   = [c for c in col_names if c != conflict_col]
    # For the UPDATE path: COALESCE(EXCLUDED.col, existing.col) means a missing
    # JSON key keeps the current DB value instead of nullifying it (PATCH semantics).
    update_set    = ",\n            ".join(
        f"{_qi(c)} = COALESCE(EXCLUDED.{_qi(c)}, {tbl}.{_qi(c)})" for c in update_cols
    ) if update_cols else "modified_at = NOW()"

    jsonb_arr_expr = (
        f"CASE jsonb_typeof(p_data) WHEN 'array' THEN p_data "
        f"ELSE jsonb_build_array(p_data) END"
    )

    # ── INSERT ───────────────────────────────────────────────────────────────
    fn_insert  = f"{schema}.insert_{tbl}"
    sql_insert = f"""\
drop function if exists {fn_insert}(jsonb, text) cascade;
create or replace function {fn_insert}(
    p_data       jsonb,
    p_audit_user text default null
) returns jsonb language plpgsql as $$
declare
    v_inserted integer := 0;
begin
    insert into {qual} ({col_list})
    select {select_from_x}
    from jsonb_to_recordset({jsonb_arr_expr}) as x({jtr_type_list})
    on conflict do nothing;

    get diagnostics v_inserted = row_count;
    return jsonb_build_object('ok', true, 'inserted', v_inserted);
end;
$$;"""

    # ── UPSERT ───────────────────────────────────────────────────────────────
    fn_upsert  = f"{schema}.upsert_{tbl}"
    sql_upsert = f"""\
drop function if exists {fn_upsert}(jsonb, text) cascade;
create or replace function {fn_upsert}(
    p_data       jsonb,
    p_audit_user text default null
) returns jsonb language plpgsql as $$
declare
    v_inserted integer := 0;
    v_updated  integer := 0;
    v_new_id   text;
begin
    with upserted as (
        insert into {qual} ({col_list})
        select {select_from_x}
        from jsonb_to_recordset({jsonb_arr_expr}) as x({jtr_type_list})
        on conflict ({_qi(conflict_col)}) do update set
            {update_set}
        returning id::text as row_id, (xmax = 0) as is_insert
    )
    select
        count(*) filter (where is_insert),
        count(*) filter (where not is_insert),
        (array_agg(row_id))[1]
    into v_inserted, v_updated, v_new_id
    from upserted;

    return jsonb_build_object(
        'ok',       true,
        'inserted', v_inserted,
        'updated',  v_updated,
        'upserted', v_inserted + v_updated,
        'id',       v_new_id
    );
end;
$$;"""

    # ── SYNC (upsert + soft-delete rows absent from incoming set) ────────────
    fn_sync = f"{schema}.sync_{tbl}"
    if has_is_active and has_code:
        deactivate_block = f"""
    update {qual}
    set {_qi("is_active")} = false
    where {_qi("is_active")} = true
      and {_qi("code")} not in (
          select x.{_qi("code")}
          from jsonb_to_recordset({jsonb_arr_expr}) as x({_qi("code")} text)
      );
    get diagnostics v_deactivated = row_count;"""
    else:
        deactivate_block = ""

    sql_sync = f"""\
drop function if exists {fn_sync}(jsonb, text) cascade;
create or replace function {fn_sync}(
    p_data       jsonb,
    p_audit_user text default null
) returns jsonb language plpgsql as $$
declare
    v_inserted    integer := 0;
    v_updated     integer := 0;
    v_deactivated integer := 0;
begin
    with upserted as (
        insert into {qual} ({col_list})
        select {select_from_x}
        from jsonb_to_recordset({jsonb_arr_expr}) as x({jtr_type_list})
        on conflict ({_qi(conflict_col)}) do update set
            {update_set}
        returning (xmax = 0) as is_insert
    )
    select
        count(*) filter (where is_insert),
        count(*) filter (where not is_insert)
    into v_inserted, v_updated
    from upserted;
{deactivate_block}
    return jsonb_build_object(
        'ok',          true,
        'inserted',    v_inserted,
        'updated',     v_updated,
        'deactivated', v_deactivated,
        'upserted',    v_inserted + v_updated
    );
end;
$$;"""

    # ── DELETE ───────────────────────────────────────────────────────────────
    fn_delete = f"{schema}.delete_{tbl}"
    if has_is_active:
        id_check = f" or ({_qi('id')}::text = p_filter->>'id')" if "id" in user_codes else ""
        delete_body = (
            f"    update {qual}\n"
            f"    set {_qi('is_active')} = false\n"
            f"    where {_qi(conflict_col)} = p_filter->>{_qi(conflict_col)}{id_check};\n"
            f"    get diagnostics v_deleted = row_count;"
        )
    elif has_code:
        delete_body = (
            f"    delete from {qual} where {_qi('code')} = p_filter->>'code';\n"
            f"    get diagnostics v_deleted = row_count;"
        )
    else:
        delete_body = (
            f"    delete from {qual} where {_qi('id')}::text = p_filter->>'id';\n"
            f"    get diagnostics v_deleted = row_count;"
        )

    sql_delete = f"""\
drop function if exists {fn_delete}(jsonb, text) cascade;
create or replace function {fn_delete}(
    p_filter     jsonb,
    p_audit_user text default null
) returns jsonb language plpgsql as $$
declare
    v_deleted integer := 0;
begin
{delete_body}
    return jsonb_build_object('ok', true, 'deleted', v_deleted);
end;
$$;"""

    return "\n\n".join([sql_insert, sql_upsert, sql_sync, sql_delete])


# ---------------------------------------------------------------------------
# BUILD UPSERT FUNCTION  (thin wrapper — delegates to toolkit.fn_upsert engine)
# ---------------------------------------------------------------------------

def build_upsert_function(table: dict) -> str:
    """Generate DROP + CREATE FUNCTION for fn_upsert_{table}.

    Thin wrapper calling toolkit.fn_upsert. Accepts a jsonb payload, optional
    conflict-target column list, optional actor name, and language.
    """
    tbl    = safe_name(table["code"])
    schema = _schema(table)
    fn     = f"{schema}.fn_upsert_{tbl}"
    drop   = _safe_drop_fn(schema, f"fn_upsert_{tbl}")
    return (
        f"{drop}\n"
        f"create or replace function {fn}(\n"
        f"    p_payload  jsonb,\n"
        f"    p_conflict text[]  default null,\n"
        f"    p_actor    text    default null,\n"
        f"    p_lang     text    default '{DEFAULT_LANG}'\n"
        f") returns jsonb language sql volatile\n"
        f"set search_path = pg_catalog, {schema}, toolkit as $$\n"
        f"    select toolkit.fn_upsert('{tbl}', p_payload, p_conflict, p_actor, p_lang);\n"
        f"$$;"
    )


# ---------------------------------------------------------------------------
# BUILD DELETE FUNCTION  (thin wrapper — delegates to toolkit.fn_delete engine)
# ---------------------------------------------------------------------------

def build_delete_function(table: dict) -> str:
    """Generate DROP + CREATE FUNCTION for fn_delete_{table}.

    Thin wrapper calling toolkit.fn_delete. Accepts a jsonb filter (one or more
    id/code values), optional soft-delete flag, and actor name.
    """
    tbl    = safe_name(table["code"])
    schema = _schema(table)
    fn     = f"{schema}.fn_delete_{tbl}"
    drop   = _safe_drop_fn(schema, f"fn_delete_{tbl}")
    return (
        f"{drop}\n"
        f"create or replace function {fn}(\n"
        f"    p_ids   jsonb,\n"
        f"    p_soft  boolean default null,\n"
        f"    p_actor text    default null\n"
        f") returns jsonb language sql volatile\n"
        f"set search_path = pg_catalog, {schema}, toolkit as $$\n"
        f"    select toolkit.fn_delete('{tbl}', p_ids, p_soft, p_actor);\n"
        f"$$;"
    )


# ---------------------------------------------------------------------------
# BUILD BULK INSERT FUNCTION  (thin wrapper — delegates to toolkit.fn_bulk_load)
# ---------------------------------------------------------------------------

def build_bulk_insert_function(table: dict) -> str:
    """Generate DROP + CREATE FUNCTION for fn_bulk_insert_{table}.

    Thin wrapper calling toolkit.fn_bulk_load. Accepts a jsonb array of rows,
    optional key columns for conflict resolution, a mode ('merge' or 'replace'),
    and an actor name.
    """
    tbl    = safe_name(table["code"])
    schema = _schema(table)
    fn     = f"{schema}.fn_bulk_insert_{tbl}"
    drop   = _safe_drop_fn(schema, f"fn_bulk_insert_{tbl}")
    return (
        f"{drop}\n"
        f"create or replace function {fn}(\n"
        f"    p_rows  jsonb,\n"
        f"    p_keys  text[]  default null,\n"
        f"    p_mode  text    default 'merge',\n"
        f"    p_actor text    default null\n"
        f") returns jsonb language sql volatile\n"
        f"set search_path = pg_catalog, {schema}, toolkit as $$\n"
        f"    select toolkit.fn_bulk_load('{tbl}', p_rows, p_keys, p_mode, p_actor);\n"
        f"$$;"
    )


# ---------------------------------------------------------------------------
# FULL DDL PACKAGE
# ---------------------------------------------------------------------------

def build_full_ddl(
    table: dict,
    fields: list[dict],
    ref_fields_map: dict[str, list[dict]] | None = None,
    ref_schema_map: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Return an ordered list of (operation, sql) tuples for a new table.

    Covers: CREATE TABLE, CREATE VIEW, and five wrapper FUNCTION statements
    (fn_list, fn_get, fn_upsert, fn_delete, fn_bulk_insert).

    Args:
        table: Table dict.
        fields: Ordered toolkit field dicts.
        ref_fields_map: Optional {ref_table_code: [field_dicts]} for multilingual
            display field resolution in the view.
        ref_schema_map: Optional {ref_table_code: schema_name} for schema-qualified
            JOIN names when the referenced table is not in the public schema.

    Returns:
        List of (operation_label, sql_string) tuples ready for exec_ddl.
    """
    return [
        ("create_table",    build_create_table(table, fields)),
        ("create_view",     build_create_view(table, fields, ref_fields_map=ref_fields_map, ref_schema_map=ref_schema_map)),
        ("create_function", build_list_function(table)),
    ]
