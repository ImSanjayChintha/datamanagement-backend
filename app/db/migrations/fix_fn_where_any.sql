-- ════════════════════════════════════════════════════════════════════════════
-- fix_fn_where_any.sql
--
-- Adds the 'any' operator to toolkit.fn_where so array columns (text[])
-- can be filtered with: { "op": "any", "value": "some_code" }
-- which generates: 'some_code' = ANY(column)
--
-- Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION toolkit.fn_where(p_rel regclass, p_table_code text, p_alias text, p_filters jsonb, p_lang text DEFAULT 'en'::text, OUT sql text, OUT params jsonb)
 RETURNS record
 LANGUAGE plpgsql
 STABLE
 SET search_path TO 'pg_catalog', 'toolkit'
AS $function$
declare
    r_key text; r_val jsonb;
    v_base text; v_path text; v_op text; v_value jsonb;
    v_type text; v_lhs text; v_ph text; v_pkey text; v_cond text;
    v_slot int := 0;
    v_conds text[] := '{}';
    m record;
begin
    params := '{}'::jsonb;

    if p_filters is null
       or jsonb_typeof(p_filters) <> 'object'
       or p_filters = '{}'::jsonb then
        sql := '($1::jsonb is not null)';
        return;
    end if;

    for r_key, r_val in select key, value from jsonb_each(p_filters)
    loop
        v_ph := null; v_cond := null;

        v_base := split_part(r_key, '.', 1);
        v_path := nullif(split_part(r_key, '.', 2), '');

        select * into m
        from   toolkit.fn_field_meta(p_rel, p_table_code) f
        where  f.field_code = v_base;

        if not found then
            raise exception 'unknown filter field: %', r_key using errcode = '42703';
        elsif not m.filterable then
            raise exception 'field is not filterable: %', r_key using errcode = '42501';
        end if;

        if    jsonb_typeof(r_val) = 'object' and r_val ? 'op' then
            v_op := lower(r_val ->> 'op');  v_value := r_val -> 'value';
        elsif jsonb_typeof(r_val) = 'array' then
            v_op := 'in';                   v_value := r_val;
        elsif jsonb_typeof(r_val) = 'null'  then
            v_op := 'isnull';               v_value := null;
        else
            v_op := 'eq';                   v_value := r_val;
        end if;

        if v_op in ('in','nin')
           and coalesce(jsonb_typeof(v_value), 'null') <> 'array' then
            raise exception '"%" on field % requires an array value', v_op, r_key
                using errcode = '22023';
        end if;
        if v_op = 'between'
           and (coalesce(jsonb_typeof(v_value), 'null') <> 'array'
                or jsonb_array_length(v_value) <> 2) then
            raise exception 'between on field % requires a 2-element array', r_key
                using errcode = '22023';
        end if;

        v_type := m.col_type;
        v_lhs  := format('%I.%I', p_alias, v_base);

        if m.is_jsonb then
            v_lhs  := format('coalesce(%s ->> %L, %s ->> %L)',
                             v_lhs, coalesce(v_path, p_lang), v_lhs, 'en');
            v_type := 'text';
        end if;

        if v_op in ('like','ilike','contains','startswith','endswith') then
            v_lhs  := format('(%s)::text', v_lhs);
            v_type := 'text';
        end if;

        -- for 'any': compare a scalar value against each element of a text[] column
        -- cast param as text (element type), leave v_lhs as-is (the text[] column)
        if v_op = 'any' then
            v_type := 'text';
        end if;

        if v_op not in ('isnull','notnull') then
            v_slot := v_slot + 1;
            v_pkey := 'p' || v_slot;
            params := params || jsonb_build_object(v_pkey, v_value);
            v_ph   := format('($1::jsonb ->> %L)::%s', v_pkey, v_type);

            -- probe the cast now, so a bad value names its field
            begin
                if v_op in ('in','nin') then
                    execute format('select (jsonb_array_elements_text($1))::%s', v_type)
                        using v_value;
                elsif v_op = 'between' then
                    execute format('select ($1 ->> 0)::%s, ($1 ->> 1)::%s', v_type, v_type)
                        using v_value;
                else
                    execute format('select ($1 #>> ''{}'')::%s', v_type)
                        using v_value;
                end if;
            exception when others then
                raise exception 'invalid value for field "%": expected %', r_key, v_type
                    using errcode = '22P02';
            end;
        end if;

        v_cond := case v_op
            when 'eq'    then format('%s = %s',  v_lhs, v_ph)
            when 'ne'    then format('%s is distinct from %s', v_lhs, v_ph)
            when 'gt'    then format('%s > %s',  v_lhs, v_ph)
            when 'gte'   then format('%s >= %s', v_lhs, v_ph)
            when 'lt'    then format('%s < %s',  v_lhs, v_ph)
            when 'lte'   then format('%s <= %s', v_lhs, v_ph)
            when 'like'  then format('%s like %s',  v_lhs, v_ph)
            when 'ilike' then format('%s ilike %s', v_lhs, v_ph)
            when 'contains'   then format($f$%s ilike '%%' || toolkit.fn_like_escape(%s) || '%%' escape '\'$f$, v_lhs, v_ph)
            when 'startswith' then format($f$%s ilike toolkit.fn_like_escape(%s) || '%%' escape '\'$f$, v_lhs, v_ph)
            when 'endswith'   then format($f$%s ilike '%%' || toolkit.fn_like_escape(%s) escape '\'$f$, v_lhs, v_ph)
            when 'in'    then format('%s = any (array(select el::%s from jsonb_array_elements_text($1::jsonb -> %L) el))',
                                     v_lhs, v_type, v_pkey)
            when 'nin'   then format('not (%s = any (array(select el::%s from jsonb_array_elements_text($1::jsonb -> %L) el)))',
                                     v_lhs, v_type, v_pkey)
            when 'between' then format('%s between ($1::jsonb #>> array[%L,''0''])::%s and ($1::jsonb #>> array[%L,''1''])::%s',
                                       v_lhs, v_pkey, v_type, v_pkey, v_type)
            when 'isnull'  then format('%s is null',     v_lhs)
            when 'notnull' then format('%s is not null', v_lhs)
            when 'any'     then format('%s = any(%s::text[])', v_ph, v_lhs)
            else null
        end;

        if v_cond is null then
            raise exception 'unsupported operator "%" on field %', v_op, r_key
                using errcode = '22023';
        end if;

        v_conds := v_conds || v_cond;
    end loop;

    sql := array_to_string(v_conds, ' and ');
end $function$
;

ALTER FUNCTION toolkit.fn_where(in regclass, in text, in text, in jsonb, in text, out text, out jsonb) OWNER TO gmc_db_sa;
GRANT ALL ON FUNCTION toolkit.fn_where(in regclass, in text, in text, in jsonb, in text, out text, out jsonb) TO gmc_db_sa;
