"""Schema selection bounded by the authenticated source allowlist."""
from chatbi.domain.schema import SchemaSelection, SimpleSemanticSelection


class SourceSchema:
    def __init__(self, schema, indicators):
        self.schema, self.indicators = schema, indicators

    def select(self, question):
        del question
        text = "授权数据源 Schema：\n" + "\n".join(
            f"{table}({', '.join(sorted(fields))})" for table, fields in self.schema.items())
        return SchemaSelection(text, "source_allowlist", tuple(self.schema))

    def resolve(self, question, *, required_tables=(), indicator_names=()):
        names = list(indicator_names) or self.indicators.detect_indicators(question)
        selected = {table: fields for table, fields in self.schema.items() if not required_tables or table in required_tables}
        schema = SourceSchema(selected, self.indicators).select(question)
        return SimpleSemanticSelection(schema=schema, indicator_text=self.indicators.build_knowledge_for_names(names),
            indicators=tuple(names), fields=tuple(f"{t}.{f}" for t, fs in selected.items() for f in fs),
            joins=(), mode="source_allowlist")
