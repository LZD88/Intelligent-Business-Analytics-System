"""Encrypted connection credentials and read-only MySQL metadata discovery."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import threading

from cryptography.fernet import Fernet
import pymysql  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, SecretStr

_vault_lock = threading.Lock()


class CredentialVault:
    def __init__(self, path: Path):
        with _vault_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
                if os.name == "nt":
                    account = subprocess.check_output(["whoami"], text=True).strip()
                    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)"],
                                   check=True, capture_output=True)
            self.cipher = Fernet(path.read_bytes())

    def encrypt(self, value: str) -> str:
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self.cipher.decrypt(value.encode()).decode()


class SourceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=3306, ge=1, le=65535)
    database: str = Field(pattern=r"^[a-zA-Z0-9_]{1,64}$")
    user: str = Field(min_length=1, max_length=80)
    password: SecretStr
    # Discovery returns all metadata; saving requires an explicit subset.
    tables: dict[str, list[str]] = Field(default_factory=dict, max_length=100)
    ssl_ca: str | None = None

    def config(self) -> dict:
        return self.model_dump(exclude={"name", "password", "tables"})


def discover_mysql(config: dict, password: str) -> dict:
    kwargs = {key: config[key] for key in ("host", "port", "database", "user")}
    if config.get("ssl_ca"):
        kwargs["ssl"] = {"ca": config["ssl_ca"], "check_hostname": True}
    connection = pymysql.connect(**kwargs, password=password, connect_timeout=5,
                                 read_timeout=10, write_timeout=10, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = [row[0] for row in cursor.fetchall()]
            # Fail closed on write/admin grants, roles, partial revokes or unknown grammar.
            for grant in grants:
                match = re.match(r"GRANT (.+?) ON (.+?) TO ", grant, re.I)
                if not match or "WITH GRANT OPTION" in grant.upper():
                    raise ValueError("请使用仅授予 SELECT/SHOW VIEW 的独立只读账号")
                privileges = {item.strip().upper() for item in match[1].split(",")}
                if privileges - {"SELECT", "SHOW VIEW", "USAGE"}:
                    raise ValueError("账号包含写入或管理权限，请改用只读账号")
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("""SELECT TABLE_NAME,COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
              WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION""", (config["database"],))
            schema: dict[str, list[str]] = {}
            for table, column in cursor.fetchall():
                if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table + "") or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", column):
                    continue
                schema.setdefault(table.lower(), []).append(column.lower())
            return schema
    finally:
        connection.rollback()
        connection.close()


def validate_allowlist(selected: dict[str, list[str]], discovered: dict) -> dict:
    if not selected:
        raise ValueError("请选择至少一张表和一个字段")
    for table, fields in selected.items():
        if table not in discovered or not fields or set(fields) - set(discovered[table]):
            raise ValueError("白名单包含未知表或字段")
    return {table: sorted(set(fields)) for table, fields in selected.items()}
