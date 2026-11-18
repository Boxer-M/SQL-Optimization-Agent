"""Read a single PostgreSQL table's structure without accessing its rows."""

from collections.abc import Mapping
from typing import TypedDict

from sql_optimization_agent.db import readonly_connection


class ColumnInfo(TypedDict):
    name: str
    data_type: str
    nullable: bool


class IndexInfo(TypedDict):
    name: str
    definition: str


class TableSchema(TypedDict):
    schema: str
    table: str
    columns: list[ColumnInfo]
    primary_key: list[str]
    indexes: list[IndexInfo]


class TableNotFoundError(LookupError):
    """The explicitly named schema/table does not identify a table."""


def inspect_table(
    schema_name: str,
    table_name: str,
    config: Mapping[str, str | None] | None = None,
) -> TableSchema:
    """Return JSON-compatible metadata, preserving column and primary-key order.

    Names are exact, case-sensitive catalog values, not SQL fragments. Ordinary,
    partitioned and foreign tables are supported. Views are not treated as tables.
    Configuration follows db.readonly_connection; no .env discovery occurs here.
    """
    for name, value in (("schema_name", schema_name), ("table_name", table_name)):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError(f"{name} must be a nonempty name without NUL characters")

    with readonly_connection(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.oid
                FROM pg_catalog.pg_class AS c
                JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                    AND c.relkind IN ('r', 'p', 'f')
                """,
                (schema_name, table_name),
            )
            target = cursor.fetchone()
            if target is None:
                raise TableNotFoundError(
                    f"Table not found: schema={schema_name!r}, table={table_name!r}"
                )
            table_oid = target[0]

            cursor.execute(
                """
                SELECT a.attname,
                       pg_catalog.format_type(a.atttypid, a.atttypmod),
                       NOT (a.attnotnull OR t.typnotnull)
                FROM pg_catalog.pg_attribute AS a
                JOIN pg_catalog.pg_type AS t ON t.oid = a.atttypid
                WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                (table_oid,),
            )
            columns: list[ColumnInfo] = [
                {"name": name, "data_type": data_type, "nullable": nullable}
                for name, data_type, nullable in cursor.fetchall()
            ]

            cursor.execute(
                """
                SELECT a.attname
                FROM pg_catalog.pg_constraint AS c
                CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, position)
                JOIN pg_catalog.pg_attribute AS a
                    ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                WHERE c.conrelid = %s AND c.contype = 'p'
                ORDER BY k.position
                """,
                (table_oid,),
            )
            primary_key = [row[0] for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT c.relname, pg_catalog.pg_get_indexdef(i.indexrelid)
                FROM pg_catalog.pg_index AS i
                JOIN pg_catalog.pg_class AS c ON c.oid = i.indexrelid
                WHERE i.indrelid = %s
                ORDER BY c.relname
                """,
                (table_oid,),
            )
            indexes: list[IndexInfo] = [
                {"name": name, "definition": definition}
                for name, definition in cursor.fetchall()
            ]

    return {
        "schema": schema_name,
        "table": table_name,
        "columns": columns,
        "primary_key": primary_key,
        "indexes": indexes,
    }
