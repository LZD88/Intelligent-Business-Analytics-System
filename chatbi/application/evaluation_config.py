"""Credential-free identity of the configuration actually used for evaluation."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

from chatbi.settings import Settings

ROLES = ("sql", "planner", "executor", "validator", "summary")


def public_endpoint(value: str) -> str:
    parts = urlsplit(value)
    # Never include URL credentials, fragments, or query-string tokens.
    return urlunsplit((parts.scheme.lower(), parts.netloc.rsplit("@", 1)[-1].lower(),
                       parts.path.rstrip("/"), "", ""))


def evaluation_config(settings: Settings, catalog_revision: str) -> dict:
    values = settings.model_dump(mode="json")
    names = [name for name in values if name.startswith(("feature_", "complex_", "simple_", "query_"))
             and not name.endswith(("_path", "_dir"))]
    return {
        "chat_endpoint": public_endpoint(settings.openai_base_url),
        "models": {role: settings.model_for(role) for role in ROLES},
        "temperature": settings.llm_temperature, "max_tokens": settings.llm_max_tokens,
        "thinking": settings.llm_thinking, "timeout_seconds": settings.llm_timeout_seconds,
        "intent_endpoint": public_endpoint(settings.intent_base_url), "intent_model": settings.intent_model,
        "intent_timeout_seconds": settings.intent_timeout_seconds,
        "embedding_provider": settings.chinese_embedding_provider,
        "schema_index_version": settings.schema_index_version,
        "catalog_revision": catalog_revision,
        "data_source": {"driver": settings.db_driver, "host": settings.db_host,
                        "port": settings.db_port, "database": settings.db_name},
        "execution": {name: values[name] for name in sorted(names)},
    }


def config_fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()
