"""Build reviewed golden SQL cases; never derive answers from generated SQL."""
import json
from pathlib import Path


def build() -> dict:
    # Explicit reference definitions, independent of the mutable metric catalog.
    # Each family has a grouped case and a dated/status-filtered case.
    sales = "sales_orders o"
    revenue = sales + " JOIN exchange_rates r ON o.order_date=r.rate_date AND o.currency=r.currency"
    products = sales + " JOIN dim_products p ON o.product_id=p.product_id"
    complete = "o.order_status='completed'"
    specs = [
        ("sales-count", "销售", "完成订单数", "COUNT(DISTINCT o.order_id)", sales, complete, "o.region", "地区", "o.order_date", "2026年第三季度"),
        ("sales-revenue", "销售", "收入", "SUM(o.net_amount*r.rate_to_cny)", revenue, complete, "o.region", "地区", "o.order_date", "2026年第三季度"),
        ("sales-cost", "盈利", "销售成本", "SUM((p.material_cost+p.labor_cost)*o.quantity)", products, complete, "p.product_line", "产品线", "o.order_date", "2026年第三季度"),
        ("sales-quantity", "销售", "销售数量", "SUM(o.quantity)", sales, complete, "o.region", "地区", "o.order_date", "2026年第三季度"),
        ("sales-customers", "客户", "成交客户数", "COUNT(DISTINCT o.customer_id)", sales, complete, "o.region", "地区", "o.order_date", "2026年第三季度"),
        ("sales-average", "销售", "平均订单金额", "SUM(o.net_amount*r.rate_to_cny)/NULLIF(COUNT(DISTINCT o.order_id),0)", revenue, complete, "o.region", "地区", "o.order_date", "2026年第三季度"),
        ("sales-unit", "销售", "平均销售单价", "SUM(o.net_amount*r.rate_to_cny)/NULLIF(SUM(o.quantity),0)", revenue, complete, "o.region", "地区", "o.order_date", "2026年第三季度"),
        ("sales-discount", "销售", "销售折扣金额", "SUM(o.discount_amount)", sales, complete, "o.currency", "币种", "o.order_date", "2026年第三季度"),
        ("purchase-count", "采购", "采购订单数", "COUNT(DISTINCT po.purchase_order_id)", "purchase_orders po", "1=1", "po.order_status", "订单状态", "po.order_date", "2026年第三季度"),
        ("purchase-amount", "采购", "采购金额", "SUM(po.total_amount)", "purchase_orders po", "1=1", "po.currency", "币种", "po.order_date", "2026年第三季度"),
        ("purchase-quantity", "采购", "采购数量", "SUM(poi.quantity)", "purchase_order_items poi JOIN purchase_orders po ON poi.purchase_order_id=po.purchase_order_id", "1=1", "po.supplier_id", "供应商ID", "po.order_date", "2026年第三季度"),
        ("purchase-received", "采购", "已收货数量", "SUM(poi.received_quantity)", "purchase_order_items poi JOIN purchase_orders po ON poi.purchase_order_id=po.purchase_order_id", "1=1", "po.supplier_id", "供应商ID", "po.order_date", "2026年第三季度"),
        ("purchase-rate", "采购", "采购收货率", "SUM(poi.received_quantity)/NULLIF(SUM(poi.quantity),0)", "purchase_order_items poi JOIN purchase_orders po ON poi.purchase_order_id=po.purchase_order_id", "1=1", "po.supplier_id", "供应商ID", "po.order_date", "2026年第三季度"),
        ("supplier-count", "供应商", "活跃供应商数", "COUNT(DISTINCT s.supplier_id)", "dim_suppliers s", "s.supplier_status='active'", "s.country", "国家", None, None),
        ("supplier-rating", "供应商", "供应商平均评分", "AVG(s.rating)", "dim_suppliers s", "1=1", "s.supplier_category", "供应商类别", None, None),
        ("inventory-onhand", "库存", "库存数量", "SUM(ib.quantity_on_hand)", "inventory_balances ib", "ib.snapshot_date=(SELECT MAX(snapshot_date) FROM inventory_balances)", "ib.warehouse_id", "仓库ID", None, None),
        ("inventory-reserved", "库存", "预留库存数量", "SUM(ib.quantity_reserved)", "inventory_balances ib", "ib.snapshot_date=(SELECT MAX(snapshot_date) FROM inventory_balances)", "ib.product_id", "产品ID", None, None),
        ("inventory-available", "库存", "可用库存", "SUM(ib.quantity_on_hand-ib.quantity_reserved)", "inventory_balances ib", "ib.snapshot_date=(SELECT MAX(snapshot_date) FROM inventory_balances)", "ib.warehouse_id", "仓库ID", None, None),
        ("inventory-value", "库存", "库存金额", "SUM(ib.inventory_value)", "inventory_balances ib", "ib.snapshot_date=(SELECT MAX(snapshot_date) FROM inventory_balances)", "ib.warehouse_id", "仓库ID", None, None),
        ("inventory-rate", "库存", "库存预留率", "SUM(ib.quantity_reserved)/NULLIF(SUM(ib.quantity_on_hand),0)", "inventory_balances ib", "ib.snapshot_date=(SELECT MAX(snapshot_date) FROM inventory_balances)", "ib.warehouse_id", "仓库ID", None, None),
        ("inventory-inbound", "库存", "入库数量", "SUM(it.quantity)", "inventory_transactions it", "it.transaction_type='inbound'", "it.product_id", "产品ID", "it.transaction_date", "2026年第三季度"),
        ("inventory-outbound", "库存", "出库数量", "SUM(ABS(it.quantity))", "inventory_transactions it", "it.transaction_type='outbound'", "it.product_id", "产品ID", "it.transaction_date", "2026年第三季度"),
        ("production-planned", "生产", "计划生产数量", "SUM(mo.planned_quantity)", "production_orders mo", "1=1", "mo.product_id", "产品ID", "mo.start_date", "2026年第三季度"),
        ("production-actual", "生产", "实际产出数量", "SUM(prod_out.good_quantity+prod_out.defect_quantity)", "production_outputs prod_out JOIN production_orders mo ON prod_out.production_order_id=mo.production_order_id", "1=1", "mo.product_id", "产品ID", "prod_out.output_date", "2026年第三季度"),
        ("production-good", "生产", "良品数量", "SUM(prod_out.good_quantity)", "production_outputs prod_out JOIN production_orders mo ON prod_out.production_order_id=mo.production_order_id", "1=1", "mo.product_id", "产品ID", "prod_out.output_date", "2026年第三季度"),
        ("production-defect", "生产", "不良品数量", "SUM(prod_out.defect_quantity)", "production_outputs prod_out JOIN production_orders mo ON prod_out.production_order_id=mo.production_order_id", "1=1", "mo.product_id", "产品ID", "prod_out.output_date", "2026年第三季度"),
        ("production-yield", "生产", "生产良率", "SUM(prod_out.good_quantity)/NULLIF(SUM(prod_out.good_quantity+prod_out.defect_quantity),0)", "production_outputs prod_out JOIN production_orders mo ON prod_out.production_order_id=mo.production_order_id", "1=1", "mo.product_id", "产品ID", "prod_out.output_date", "2026年第三季度"),
        ("quality-inspected", "质量", "检验数量", "SUM(qi.inspected_quantity)", "quality_inspections qi", "1=1", "qi.production_order_id", "生产订单ID", "qi.inspection_date", "2026年第三季度"),
        ("quality-passed", "质量", "检验合格数量", "SUM(qi.passed_quantity)", "quality_inspections qi", "1=1", "qi.production_order_id", "生产订单ID", "qi.inspection_date", "2026年第三季度"),
        ("quality-rate", "质量", "检验合格率", "SUM(qi.passed_quantity)/NULLIF(SUM(qi.inspected_quantity),0)", "quality_inspections qi", "1=1", "qi.production_order_id", "生产订单ID", "qi.inspection_date", "2026年第三季度"),
        ("quality-defect-rate", "质量", "检验缺陷率", "SUM(qi.inspected_quantity-qi.passed_quantity)/NULLIF(SUM(qi.inspected_quantity),0)", "quality_inspections qi", "1=1", "qi.production_order_id", "生产订单ID", "qi.inspection_date", "2026年第三季度"),
        ("equipment-count", "设备", "设备数量", "COUNT(DISTINCT e.equipment_id)", "equipment e", "1=1", "e.equipment_type", "设备类型", None, None),
        ("equipment-running", "设备", "在运设备数量", "COUNT(DISTINCT e.equipment_id)", "equipment e", "e.equipment_status='running'", "e.department_id", "部门ID", None, None),
        ("maintenance-count", "设备", "设备维护次数", "COUNT(DISTINCT em.maintenance_id)", "equipment_maintenance em", "1=1", "em.equipment_id", "设备ID", "em.maintenance_date", "2026年第三季度"),
        ("maintenance-cost", "设备", "设备维护成本", "SUM(em.maintenance_cost)", "equipment_maintenance em", "1=1", "em.equipment_id", "设备ID", "em.maintenance_date", "2026年第三季度"),
        ("maintenance-down", "设备", "设备停机时长", "SUM(em.downtime_hours)", "equipment_maintenance em", "1=1", "em.equipment_id", "设备ID", "em.maintenance_date", "2026年第三季度"),
        ("shipment-count", "物流", "发运单数", "COUNT(DISTINCT sh.shipment_id)", "shipments sh", "1=1", "sh.warehouse_id", "仓库ID", "sh.shipment_date", "2026年第三季度"),
        ("shipment-freight", "物流", "运费", "SUM(sh.freight_cost)", "shipments sh", "1=1", "sh.carrier_name", "承运商名称", "sh.shipment_date", "2026年第三季度"),
        ("shipment-quantity", "物流", "发运数量", "SUM(si.shipped_quantity)", "shipment_items si JOIN shipments sh ON si.shipment_id=sh.shipment_id JOIN sales_order_items soi ON si.sales_order_item_id=soi.sales_order_item_id", "1=1", "soi.product_id", "产品ID", "sh.shipment_date", "2026年第三季度"),
        ("payment-customer", "回款", "客户回款金额", "SUM(cp.payment_amount)", "customer_payments cp", "cp.payment_status IN ('received','partial')", "cp.currency", "币种", "cp.payment_date", "2026年第三季度"),
        ("payment-supplier", "付款", "供应商付款金额", "SUM(sp.payment_amount)", "supplier_payments sp", "sp.payment_status IN ('paid','partial')", "sp.currency", "币种", "sp.payment_date", "2026年第三季度"),
        ("budget-amount", "预算", "预算金额", "SUM(b.budget_amount)", "budgets b", "b.budget_year=2026", "b.cost_center_id", "成本中心ID", None, None),
        ("budget-actual", "预算", "实际发生金额", "SUM(b.actual_amount)", "budgets b", "b.budget_year=2026", "b.cost_center_id", "成本中心ID", None, None),
        ("budget-rate", "预算", "预算执行率", "SUM(b.actual_amount)/NULLIF(SUM(b.budget_amount),0)", "budgets b", "b.budget_year=2026", "b.cost_center_id", "成本中心ID", None, None),
        ("employee-count", "人力", "员工人数", "COUNT(DISTINCT e.employee_id)", "dim_employees e", "1=1", "e.department_id", "部门ID", None, None),
        ("employee-active", "人力", "在职员工数", "COUNT(DISTINCT e.employee_id)", "dim_employees e", "e.employee_status='active'", "e.department_id", "部门ID", None, None),
    ]
    cases = []
    for slug, domain, metric, formula, source, where, dimension, label, date, period in specs:
        for variant in ("all", "filtered"):
            condition, prefix = where, ""
            if date and variant == "filtered":
                condition += f" AND {date}>='2026-07-01' AND {date}<'2026-10-01'"
                prefix = period
            elif variant == "filtered":
                condition += f" AND {dimension} IS NOT NULL"
                prefix = f"仅统计{label}非空的记录，"
            if "inventory-" in slug and "inbound" not in slug and "outbound" not in slug:
                prefix += "最新快照的"
            if slug.startswith("budget-"):
                prefix += "2026年"
            # Snapshot/dimension families use grouped vs total queries rather
            # than duplicate NULL predicates with identical expected results.
            grouped = bool(date or variant == "all" or label == "币种")
            question = f"{prefix}按{label}统计{metric}，仅输出{label}和{metric}两列。" if grouped else f"{prefix}{metric}合计是多少？仅输出一个数值列。"
            sql = f"SELECT {dimension + ',' if grouped else ''}{formula} FROM {source} WHERE {condition}"
            if grouped:
                sql += f" GROUP BY {dimension}"
            cases.append({"id": f"extended-{slug}-{variant}", "domain": domain, "metric": metric,
                          "category": "grouped_metric" if grouped else "aggregate_metric", "difficulty": "medium",
                          "question": question, "business_definition": f"按治理口径计算{metric}；比例使用0到1小数；禁止扩大粒度。",
                          "expected_sql": sql})
    cases += [
        {"id": "empty-future-sales", "domain": "边界", "category": "empty_result", "difficulty": "simple", "question": "2099年按地区统计完成订单数，只输出地区和订单数两列。", "expected_sql": "SELECT region,COUNT(DISTINCT order_id) FROM sales_orders WHERE order_status='completed' AND order_date>='2099-01-01' AND order_date<'2100-01-01' GROUP BY region"},
        {"id": "zero-future-count", "domain": "边界", "category": "zero_result", "difficulty": "simple", "question": "2099年完成订单数合计是多少？", "expected_sql": "SELECT COUNT(DISTINCT order_id) FROM sales_orders WHERE order_status='completed' AND order_date>='2099-01-01' AND order_date<'2100-01-01'"},
    ]
    from scripts.product_benchmark import metric_cases
    cases += metric_cases()
    behaviors = []
    for index, question in enumerate(("你好", "谢谢你", "你是谁", "怎么用", "再见", "早上好", "hello", "在吗")):
        behaviors.append({"id": f"conversation-{index+1}", "domain": "意图路由", "kind": "non_query", "question": question})
    for index, (question, ids) in enumerate((("最近利润怎么样？", ["metric", "time"]), ("最近收入是多少？", ["time"]), ("利润是多少？", ["metric"]), ("哪些产品表现最好？", ["ranking"]))):
        behaviors.append({"id": f"clarification-{index+1}", "domain": "主动澄清", "kind": "clarification", "question": question, "question_ids": ids})
    for index, (question, predicate) in enumerate((("只看欧洲", "region='欧洲'"), ("只看亚太", "region='亚太'"), ("改成2025年", "order_date>='2025-01-01' AND order_date<'2026-01-01'"), ("改成2026年第三季度", "order_date>='2026-07-01' AND order_date<'2026-10-01'"))):
        condition = f"order_date>='2026-01-01' AND order_date<'2027-01-01' AND {predicate}" if index < 2 else predicate
        behaviors.append({"id": f"followup-{index+1}", "domain": "多轮追问", "kind": "followup", "question": question,
                          "context": ["2026年按地区统计完成订单数，只输出地区和订单数两列。"],
                          "expected_sql": f"SELECT region,COUNT(DISTINCT order_id) FROM sales_orders WHERE order_status='completed' AND {condition} GROUP BY region"})
    return {"version": "3.0.0", "database_snapshot": "chatbi-mvp-seed-2026-11-09", "cases": cases, "behaviors": behaviors}


if __name__ == "__main__":
    output = Path("benchmarks/extended_cases.json")
    document = build()
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(document['cases'])} SQL cases and {len(document['behaviors'])} behavior cases.")
