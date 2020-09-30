
{% macro duckdb__get_catalog(information_schema, schemas) -%}
  {%- call statement('catalog', fetch_result=True) -%}
    select
        'main' as table_database,
        t.table_schema,
        t.table_name,
        t.table_type,
        '' as table_comment,
        c.column_name,
        c.ordinal_position as column_index,
        c.data_type column_type,
        '' as column_comment,
        '' as table_owner
    FROM information_schema_tables() t JOIN information_schema_columns() c ON t.table_schema = c.table_schema AND t.table_name = c.table_name
    order by
        t.table_schema,
        t.table_name,
        c.ordinal_position
  {%- endcall -%}
  {{ return(load_result('catalog').table) }}
{%- endmacro %}
