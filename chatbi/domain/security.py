"""Deterministic SQL safety policy for model-generated MySQL queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope, Scope

from chatbi.domain.errors import ChatBIError, ErrorCode


DEFAULT_SCHEMA: dict[str, frozenset[str]] = {
    "dim_customers": frozenset(
        {"customer_id", "customer_name", "customer_type", "industry", "country", "region"}
    ),
    "dim_products": frozenset(
        {
            "product_id", "product_name", "product_line", "category", "tech_route",
            "standard_cost", "material_cost", "labor_cost",
        }
    ),
    "sales_orders": frozenset(
        {
            "order_id", "order_no", "customer_id", "product_id", "region", "order_date",
            "order_status", "quantity", "unit_price", "discount_amount", "gross_amount",
            "net_amount", "currency",
        }
    ),
    "exchange_rates": frozenset({"rate_date", "currency", "rate_to_cny"}),
    "finance_expenses": frozenset(
        {
            "expense_id", "expense_date", "department", "rd_expense", "selling_expense",
            "admin_expense", "finance_expense", "marketing_expense", "logistics_expense",
            "warranty_expense",
        }
    ),
    "dim_regions": frozenset({"region_id", "region_name", "region_manager"}),
    "dim_countries": frozenset({"country_id", "country_name", "region_id", "iso_code"}),
    "dim_departments": frozenset({"department_id", "department_name", "department_type", "parent_department_id"}),
    "dim_employees": frozenset({"employee_id", "employee_name", "department_id", "job_title", "hire_date", "employee_status"}),
    "dim_suppliers": frozenset({"supplier_id", "supplier_name", "supplier_category", "country", "rating", "supplier_status"}),
    "dim_warehouses": frozenset({"warehouse_id", "warehouse_name", "region", "capacity_mwh", "warehouse_status"}),
    "dim_sales_channels": frozenset({"channel_id", "channel_name", "channel_type", "commission_rate"}),
    "dim_currencies": frozenset({"currency_code", "currency_name", "currency_symbol", "decimal_places"}),
    "dim_calendar": frozenset({"calendar_date", "calendar_year", "calendar_quarter", "calendar_month", "month_name", "is_workday"}),
    "dim_cost_centers": frozenset({"cost_center_id", "cost_center_code", "cost_center_name", "department_id", "annual_budget"}),
    "purchase_orders": frozenset({"purchase_order_id", "purchase_order_no", "supplier_id", "order_date", "expected_date", "order_status", "currency", "total_amount"}),
    "purchase_order_items": frozenset({"purchase_order_item_id", "purchase_order_id", "product_id", "quantity", "unit_cost", "received_quantity"}),
    "sales_order_items": frozenset({"sales_order_item_id", "order_id", "product_id", "quantity", "unit_price", "discount_amount", "line_net_amount"}),
    "inventory_balances": frozenset({"inventory_balance_id", "warehouse_id", "product_id", "snapshot_date", "quantity_on_hand", "quantity_reserved", "inventory_value"}),
    "inventory_transactions": frozenset({"inventory_transaction_id", "warehouse_id", "product_id", "transaction_date", "transaction_type", "quantity", "reference_type", "reference_id"}),
    "production_orders": frozenset({"production_order_id", "production_order_no", "product_id", "planned_quantity", "start_date", "due_date", "production_status"}),
    "production_outputs": frozenset({"production_output_id", "production_order_id", "output_date", "good_quantity", "defect_quantity", "shift_name"}),
    "quality_inspections": frozenset({"inspection_id", "production_order_id", "inspection_date", "inspected_quantity", "passed_quantity", "defect_type", "inspection_result"}),
    "equipment": frozenset({"equipment_id", "equipment_code", "equipment_name", "equipment_type", "department_id", "purchase_date", "equipment_status"}),
    "equipment_maintenance": frozenset({"maintenance_id", "equipment_id", "maintenance_date", "maintenance_type", "downtime_hours", "maintenance_cost", "maintenance_result"}),
    "shipments": frozenset({"shipment_id", "shipment_no", "order_id", "warehouse_id", "shipment_date", "delivery_date", "carrier_name", "shipment_status", "freight_cost"}),
    "shipment_items": frozenset({"shipment_item_id", "shipment_id", "sales_order_item_id", "shipped_quantity"}),
    "customer_payments": frozenset({"customer_payment_id", "customer_id", "order_id", "payment_date", "payment_amount", "currency", "payment_method", "payment_status"}),
    "supplier_payments": frozenset({"supplier_payment_id", "supplier_id", "purchase_order_id", "payment_date", "payment_amount", "currency", "payment_status"}),
    "budgets": frozenset({"budget_id", "cost_center_id", "budget_year", "budget_month", "budget_category", "budget_amount", "actual_amount"}),
}

SYSTEM_SCHEMAS = frozenset({"information_schema", "mysql", "performance_schema", "sys"})
DANGEROUS_FUNCTIONS = frozenset({"sleep", "benchmark", "load_file"})


@dataclass(frozen=True, slots=True)
class SecuredQuery:
    sql: str
    tables: tuple[str, ...]
    limited: bool
    max_rows: int
    row_level_applied: bool = False


@dataclass(frozen=True, slots=True)
class RowLevelFilter:
    """A parameter-like equality policy rendered through the SQL AST."""

    table: str
    column: str
    value: Any


@dataclass(frozen=True, slots=True)
class UserContext:
    """MVP user context retained for compatibility with the teaching database client."""

    user_id: str
    allowed_tables: frozenset[str]
    row_filters: tuple[RowLevelFilter, ...] = ()

    @classmethod
    def demo_admin(cls) -> "UserContext":
        return cls("local-demo", frozenset(DEFAULT_SCHEMA))


class QuerySecurityManager:
    def __init__(
        self,
        *,
        schema: dict[str, Iterable[str]] | None = None,
        max_rows: int = 200,
        sensitive_fields: Iterable[str] = (),
    ) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        source = DEFAULT_SCHEMA if schema is None else schema
        self.schema = {
            table.lower(): frozenset(field.lower() for field in fields)
            for table, fields in source.items()
        }
        self.max_rows = max_rows
        self.sensitive_fields = frozenset(field.lower().split(".")[-1] for field in sensitive_fields)

    def secure(self, sql: str, user: UserContext | None = None) -> SecuredQuery:
        if not sql or not sql.strip():
            self._reject("SQL 为空")
        try:
            statements = sqlglot.parse(sql, read="mysql")
        except ParseError as exc:
            self._reject("SQL 无法解析", parse_error=str(exc))
        if len(statements) != 1:
            self._reject("仅允许一条 SQL 语句")

        statement = statements[0]
        if not isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            self._reject("仅允许 SELECT 或 WITH ... SELECT 查询", root=type(statement).__name__)
        statement = cast(exp.Query, statement)

        self._reject_dangerous_nodes(statement)
        tables, aliases = self._validate_tables(statement, user)
        self._validate_columns(statement, tables, aliases)
        self._validate_functions(statement)
        policy_secured, row_level_applied = self._apply_row_level_filters(statement, tables, user)
        secured, limited = self._apply_limit(policy_secured)
        return SecuredQuery(
            sql=secured.sql(dialect="mysql"),
            tables=tuple(sorted(tables)),
            limited=limited,
            max_rows=self.max_rows,
            row_level_applied=row_level_applied,
        )

    def secure_sql(self, sql: str, user: UserContext | None = None) -> str:
        return self.secure(sql, user).sql

    def mask_result(
        self,
        columns: list[str],
        rows: list[tuple],
        user: UserContext | None = None,
    ) -> tuple[list[str], list[tuple]]:
        """Mask configured sensitive result columns as a final defensive layer."""
        indexes = {i for i, name in enumerate(columns) if name.lower() in self.sensitive_fields}
        if not indexes:
            return columns, rows
        masked = [tuple("***" if i in indexes else value for i, value in enumerate(row)) for row in rows]
        return columns, masked

    def _reject_dangerous_nodes(self, statement: exp.Expression) -> None:
        forbidden_names = {
            "Insert", "Update", "Delete", "Create", "Drop", "Alter", "TruncateTable",
            "Grant", "Revoke", "Commit", "Rollback", "Transaction", "Command", "Merge",
            "Copy", "LoadData",
        }
        for node in statement.walk():
            if type(node).__name__ in forbidden_names:
                self._reject("查询包含禁止的 SQL 操作", node=type(node).__name__)
        rendered = statement.sql(dialect="mysql").lower()
        if " into outfile" in rendered or " into dumpfile" in rendered:
            self._reject("禁止将查询结果写入文件")

    def _validate_tables(
        self,
        statement: exp.Expression,
        user: UserContext | None,
    ) -> tuple[set[str], dict[str, str]]:
        tables: set[str] = set()
        aliases: dict[str, str] = {}
        allowed = user.allowed_tables if user else frozenset(self.schema)
        for scope in traverse_scope(statement):
            for alias, (_, source) in scope.selected_sources.items():
                if not isinstance(source, exp.Table):
                    continue
                name = source.name.lower()
                if source.db or source.catalog:
                    self._reject("禁止跨库访问", table=name)
                if name not in self.schema or name not in allowed:
                    self._reject("查询引用了未授权的表", table=name)
                tables.add(name)
                aliases[alias.lower()] = name
        return tables, aliases

    def _validate_columns(self, statement, tables, aliases) -> None:
        del aliases
        if self.sensitive_fields & {field for table in tables for field in self.schema[table]}:
            for star in statement.find_all(exp.Star):
                # COUNT(*) reveals no field value. SELECT * and table.* do.
                if not isinstance(star.parent, exp.Count):
                    self._reject("敏感数据源禁止星号展开，请明确选择非敏感字段")
        for scope in traverse_scope(statement):
            sources = {name.lower(): source for name, source in scope.sources.items()}
            fields: set[str] = set()
            for source in sources.values():
                if isinstance(source, exp.Table):
                    fields.update(self.schema.get(source.name.lower(), ()))
                else:
                    fields.update(name.lower() for name in source.expression.named_selects)
            select_aliases = {item.alias.lower() for item in getattr(scope.expression, "expressions", ()) if item.alias}
            for column in scope.columns:
                name, qualifier = column.name.lower(), column.table.lower()
                if name in self.sensitive_fields:
                    self._reject("查询引用了敏感字段", field=name)
                if qualifier:
                    source = sources.get(qualifier)
                    parent = scope.parent
                    while source is None and parent is not None:
                        source = next((v for k,v in parent.sources.items() if k.lower() == qualifier), None)
                        parent = parent.parent
                    if source is None:
                        self._reject("查询引用了未知表别名", field=qualifier)
                    if isinstance(source, exp.Table) and name not in self.schema.get(source.name.lower(), ()):
                        self._reject("查询引用了未知字段", field=f"{qualifier}.{name}")
                elif name not in fields and name not in select_aliases and "*" not in fields:
                    self._reject("查询引用了未知字段", field=name)

    def _validate_functions(self, statement: exp.Expression) -> None:
        for function in statement.find_all(exp.Func):
            name = (
                function.name.lower()
                if isinstance(function, exp.Anonymous)
                else function.sql_name().lower()
            )
            if name in DANGEROUS_FUNCTIONS:
                self._reject("查询调用了禁止的函数", function=name)

    def _apply_row_level_filters(
        self,
        statement: exp.Query,
        tables: set[str],
        user: UserContext | None,
    ) -> tuple[exp.Query, bool]:
        if user is None or not user.row_filters:
            return statement, False

        secured = statement.copy()
        applied = False
        for policy in user.row_filters:
            table_name = policy.table.lower()
            column_name = policy.column.lower()
            if table_name not in self.schema:
                self._reject("行级权限引用了未知表", table=table_name)
            if column_name not in self.schema[table_name]:
                self._reject("行级权限引用了未知字段", field=f"{table_name}.{column_name}")
            if table_name not in tables:
                continue

            matched = False
            for scope in traverse_scope(secured):
                select = scope.expression
                if not isinstance(select, exp.Select):
                    continue
                for _, (_, table) in scope.selected_sources.items():
                    if not isinstance(table, exp.Table) or table.name.lower() != table_name:
                        continue
                    qualifier = (table.alias_or_name or table.name).lower()
                    predicate = exp.EQ(
                        this=exp.column(column_name, table=qualifier),
                        expression=exp.convert(policy.value),
                    )
                    current_where = select.args.get("where")
                    condition = (
                        exp.and_(current_where.this, predicate)
                        if isinstance(current_where, exp.Where)
                        else predicate
                    )
                    select.set("where", exp.Where(this=condition))
                    matched = True
                    applied = True
            if not matched:
                self._reject("无法在查询作用域中注入行级权限", table=table_name)
        return secured, applied

    def _apply_limit(self, statement: exp.Query) -> tuple[exp.Query, bool]:
        secured = statement.copy()
        # A scalar aggregate is guaranteed to return one row. Grouped
        # aggregates, however, can still return an unbounded result set.
        if isinstance(secured, exp.Select) and secured.args.get("group") is None and any(
            aggregate.find_ancestor(exp.Select) is secured
            and aggregate.find_ancestor(exp.Window) is None
            for aggregate in secured.find_all(exp.AggFunc)
        ):
            return secured, False
        current_limit = secured.args.get("limit")
        if current_limit is None:
            return secured.limit(self.max_rows), True
        expression = current_limit.expression
        try:
            requested = int(expression.this) if isinstance(expression, exp.Literal) else self.max_rows + 1
        except (TypeError, ValueError):
            requested = self.max_rows + 1
        if requested > self.max_rows:
            secured.set("limit", exp.Limit(expression=exp.Literal.number(self.max_rows)))
            return secured, True
        return secured, False

    @staticmethod
    def _reject(message: str, **details: object) -> None:
        raise ChatBIError(ErrorCode.SQL_REJECTED, message, details=dict(details))
