"""Compare isolated control backups without logging users, sessions or credentials."""
import os

import psycopg
from psycopg import sql


def snapshot(dsn):
    with psycopg.connect(dsn, autocommit=True) as connection:
        tables = connection.execute("""SELECT table_schema,table_name FROM information_schema.tables
            WHERE table_schema LIKE 'chatbi\\_%' ESCAPE '\\' AND table_type='BASE TABLE'
            ORDER BY table_schema,table_name""").fetchall()
        result = {}
        for schema, table in tables:
            rows = connection.execute(sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))).fetchall()
            result[(schema, table)] = sorted(rows, key=repr)
        return result


if __name__ == "__main__":
    original = snapshot(os.environ["CHATBI_BACKUP_SOURCE_URL"])
    restored = snapshot(os.environ["CHATBI_BACKUP_RESTORED_URL"])
    assert original and original == restored, "Restored control data differs"
    print(f"PostgreSQL restore verified: {len(original)} tables; contents match, values omitted")
