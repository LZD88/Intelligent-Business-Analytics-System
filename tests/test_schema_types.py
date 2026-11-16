from chatbi.infrastructure.schema_metadata import TypedSchema, seed_column_types
from chatbi.infrastructure.simple_semantic import ChromaSimpleSemanticPipeline
from chatbi.infrastructure.source_schema import SourceSchema


def test_seed_context_preserves_id_types_and_covers_all_fields():
    from chatbi.domain.security import DEFAULT_SCHEMA
    types = seed_column_types()
    assert set(types) == set(DEFAULT_SCHEMA)
    for table, fields in DEFAULT_SCHEMA.items():
        assert set(types[table]) == set(fields)
    text = ChromaSimpleSemanticPipeline._build_schema(["production_orders"], ["production_orders.product_id"], [])
    assert "product_id INT" in text


def test_source_types_never_expand_allowlist():
    schema = TypedSchema({"orders": ["id"]}, column_types={"orders": {"id": "bigint", "secret": "text"}, "other": {"id": "int"}})
    class Indicators:
        def detect_indicators(self, _): return []
        def build_knowledge_for_names(self, _): return ""
    resolver = SourceSchema(schema, Indicators())
    text = resolver.resolve("订单").schema.text
    assert "id bigint" in text and "secret" not in text and "other" not in text
