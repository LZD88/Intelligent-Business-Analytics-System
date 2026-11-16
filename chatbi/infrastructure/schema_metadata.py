"""Column types supplement the existing table/field allowlist contract."""
from functools import lru_cache
from pathlib import Path

import sqlglot
from sqlglot import exp


class TypedSchema(dict):
    def __init__(self, schema=(), *, column_types=None):
        super().__init__(schema)
        self.column_types = {t: {f: kind for f, kind in fields.items() if f in self.get(t, ())}
                             for t, fields in (column_types or {}).items() if t in self}


@lru_cache(maxsize=1)
def seed_column_types():
    result = {}
    root = Path(__file__).resolve().parents[2] / "db"
    for name in ("01_schema.sql", "04_extended_schema.sql"):
        for statement in (root / name).read_text(encoding="utf-8").split(";"):
            if "CREATE TABLE" not in statement.upper():
                continue
            tree = sqlglot.parse_one(statement, read="mysql")
            if isinstance(tree, exp.Create) and isinstance(tree.this, exp.Schema):
                result[tree.this.this.name] = {
                    column.name: column.kind.sql(dialect="mysql")
                    for column in tree.this.expressions if isinstance(column, exp.ColumnDef) and column.kind is not None
                }
    return result


def typed_fields(table, fields, column_types):
    return ", ".join(f"{field} {column_types.get(table, {}).get(field, '类型未知')}" for field in sorted(fields))
