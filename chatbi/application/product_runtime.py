"""Request-scoped identity, catalog and data source snapshots."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json

from pydantic import SecretStr

from chatbi.domain.security import DEFAULT_SCHEMA, RowLevelFilter, UserContext
from chatbi.infrastructure.control_store import AccessDenied, ControlStore, Principal, principal_context, source_context
from chatbi.infrastructure.data_sources import CredentialVault
from chatbi.infrastructure.metric_registry import MetricRegistry


class ProductRuntime:
    def __init__(self, settings):
        self.settings = settings
        self.store = ControlStore(settings.control_store_path)
        self.vault = CredentialVault(settings.control_store_path.with_suffix(".key"))

    def catalog(self):
        actor, source = principal_context.get(), source_context.get()
        self.store.source(actor, source)
        if actor.tenant_id == "default" and source == "demo":
            return MetricRegistry(self.settings.metric_registry_path)
        key = hashlib.sha256((actor.tenant_id + ":" + source).encode()).hexdigest()
        return MetricRegistry(self.settings.metric_registry_path.parent / "catalogs" / (key + ".sqlite3"), seed=False)

    def snapshot(self):
        actor, source_id = principal_context.get(), source_context.get()
        source = self.store.source(actor, source_id)
        settings = self.settings
        schema = DEFAULT_SCHEMA
        policy = source.get("policy", {})
        if source_id != "demo":
            config = source["config"]
            schema = {t: frozenset(f) for t, f in source["schema"].items() if t in policy.get("tables", source["schema"])}
            settings = settings.model_copy(update={"db_host": config["host"], "db_port": config["port"],
                "db_name": config["database"], "db_user": config["user"],
                "db_password": SecretStr(self.vault.decrypt(source["secret"])),
                "query_allowed_tables": ",".join(schema), "feature_simple_chroma": False,
                "query_sensitive_fields": ",".join(policy.get("sensitive_fields", []))})
        else:
            schema = {t: fields for t, fields in schema.items() if t in settings.allowed_tables}
        user = UserContext(actor.user_id, frozenset(schema), tuple(RowLevelFilter(**rule) for rule in policy.get("row_filters", [])))
        fingerprint = hashlib.sha256(json.dumps({"actor": asdict(actor), "source": source_id,
            "revision": source["revision"], "policy": policy, "schema": {t: sorted(f) for t, f in schema.items()}}, sort_keys=True).encode()).hexdigest()
        config = None
        if source_id != "demo":
            config = {key: value for key, value in source["config"].items() if key != "ssl_ca"}
            config["password"] = settings.db_password.get_secret_value()
            if source["config"].get("ssl_ca"):
                config["ssl"] = {"ca": source["config"]["ssl_ca"], "check_hostname": True}
        return settings, schema, user, config, fingerprint

    def options(self):
        actor = principal_context.get()
        return {"owner": actor.user_id, "tenant": actor.tenant_id, "source_id": source_context.get(),
                "access_version": self.access_version(actor, source_context.get())}

    def access_version(self, actor, source_id):
        source = self.store.source(actor, source_id)
        value = {"user": asdict(actor), "source": source_id, "revision": source["revision"],
                 "policy": source.get("policy", {}), "allowed": self.settings.query_allowed_tables,
                 "sensitive": self.settings.query_sensitive_fields}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def owns(self, item):
        options = (item or {}).get("request_options") or {}
        actor = principal_context.get()
        # Legacy local records are available only in explicitly disabled auth mode.
        if not options.get("owner"):
            return not self.settings.auth_enabled and actor.tenant_id == "default" and source_context.get() == "demo"
        return all(options.get(k) == v for k, v in self.options().items()
                   if k != "access_version" or options.get(k))

    @contextmanager
    def bind_job(self, options):
        actor: Principal | None
        if not self.settings.auth_enabled:
            actor = Principal()
        else:
            actor = self.store.user(options.get("owner", ""))
            if not actor or actor.tenant_id != options.get("tenant"):
                raise AccessDenied("任务所属账号已停用或权限已变更")
        a = principal_context.set(actor)
        s = source_context.set(options.get("source_id", "demo"))
        try:
            self.store.source(actor, source_context.get())
            if options.get("access_version") and options["access_version"] != self.access_version(actor, source_context.get()):
                raise AccessDenied("数据权限或数据源版本已变化，请重新提交分析")
            yield
        finally:
            source_context.reset(s)
            principal_context.reset(a)
