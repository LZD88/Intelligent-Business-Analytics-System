"""Control-store connection boundary: local SQLite or central PostgreSQL.

Only fixed application SQL reaches this adapter. Business/model SQL never does.
PostgreSQL uses one schema per stable store filename, shared across hosts.
"""
import hashlib
import os
import re
import sqlite3


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return Row(row) if row is not None else None

    def fetchall(self):
        return [Row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class PostgreSQL:
    def __init__(self, path, dsn):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg import sql
        self.connection = psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)
        schema = "chatbi_" + hashlib.sha256(path.name.encode()).hexdigest()[:16]
        self.connection.execute("BEGIN")
        self.connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (schema,))
        self.connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        self.connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))

    def execute(self, query, values=()):
        if query.startswith("PRAGMA table_info("):
            table = query.split("(",1)[1].rstrip(")")
            return Cursor(self.connection.execute("SELECT ordinal_position-1 AS cid,column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (table,)))
        if query.startswith(("PRAGMA ", "BEGIN")):
            return Cursor(self.connection.execute("SELECT 1"))
        query = query.replace("name=? COLLATE NOCASE", "lower(name)=lower(?)")
        query = query.replace(" COLLATE NOCASE", "")
        query = re.sub(r"\bREAL\b", "DOUBLE PRECISION", query)
        query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        query = query.replace("id INTEGER PRIMARY KEY,", "id BIGSERIAL PRIMARY KEY,")
        query = query.replace("LIMIT -1 OFFSET", "OFFSET")
        query = re.sub(r"json_extract\(request_options,'\$\.(\w+)'\)", r"(request_options::jsonb->>'\1')", query)
        if query.startswith("INSERT OR REPLACE INTO"):
            table = query.split()[4]
            columns, key = {"login_attempts": (["identity", "count", "until"], ["identity"]),
                            "source_grants": (["source_id", "user_id", "policy"], ["source_id", "user_id"])}[table]
            query = query.replace("INSERT OR REPLACE INTO", "INSERT INTO")
            query += " ON CONFLICT(" + ",".join(key) + ") DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in columns if c not in key)
        # All application placeholders are positional; no literal '?' is used in control SQL.
        query = query.replace("?", "%s")
        try:
            return Cursor(self.connection.execute(query, values))
        except Exception as exc:
            import psycopg
            if isinstance(exc, psycopg.IntegrityError):
                raise sqlite3.IntegrityError("control store constraint violation") from None
            raise

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement.strip())
        if "CREATE TABLE IF NOT EXISTS metrics (" in script:
            self.execute("CREATE UNIQUE INDEX IF NOT EXISTS metrics_name_folded ON metrics(lower(name))")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.connection.execute("ROLLBACK" if exc_type else "COMMIT")

    def close(self):
        self.connection.close()


def connect(path, timeout=15):
    dsn = os.getenv("CONTROL_DATABASE_URL")
    return PostgreSQL(path, dsn) if dsn else sqlite3.connect(path, timeout=timeout)
