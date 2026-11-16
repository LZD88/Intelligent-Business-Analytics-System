"""Additional independently specified metric and temporal golden queries."""


def metric_cases():
    sales = "sales_orders o JOIN dim_products p ON o.product_id=p.product_id JOIN exchange_rates r ON o.order_date=r.rate_date AND o.currency=r.currency"
    period = "o.order_status='completed' AND o.order_date>='2026-07-01' AND o.order_date<'2026-10-01'"
    revenue = "SUM(o.net_amount*r.rate_to_cny)"
    cost = "SUM(o.quantity*(p.material_cost+p.labor_cost))"
    finance = "SUM(f.rd_expense+f.selling_expense+f.admin_expense+f.finance_expense)"
    profit = f"WITH s AS (SELECT {revenue} AS rev,{cost} AS cost FROM {sales} WHERE {period}), f AS (SELECT {finance} AS expense FROM finance_expenses f WHERE f.expense_date>='2026-07-01' AND f.expense_date<'2026-10-01')"
    definitions = [
        ("gross-profit", "毛利", "2026年第三季度已完成订单的人民币毛利合计是多少？仅输出毛利一列。", f"SELECT {revenue}-{cost} FROM {sales} WHERE {period}"),
        ("gross-margin", "毛利率", "2026年第三季度已完成订单整体毛利率是多少？用0到1小数，仅输出毛利率一列。", f"SELECT ({revenue}-{cost})/NULLIF({revenue},0) FROM {sales} WHERE {period}"),
        ("expenses", "期间费用", "2026年第三季度期间费用合计是多少？仅输出期间费用一列。", f"SELECT {finance} FROM finance_expenses f WHERE f.expense_date>='2026-07-01' AND f.expense_date<'2026-10-01'"),
        ("profit", "利润", "2026年第三季度净利润合计是多少？口径为完成订单人民币毛利减同期期间费用，仅输出净利润一列。", profit+" SELECT s.rev-s.cost-f.expense FROM s CROSS JOIN f"),
        ("profit-margin", "利润率", "2026年第三季度净利润率是多少？净利润为完成订单人民币毛利减同期期间费用，比例为0到1小数，仅输出净利润率一列。", profit+" SELECT (s.rev-s.cost-f.expense)/NULLIF(s.rev,0) FROM s CROSS JOIN f"),
        ("purchase-unit", "平均采购单价", "按采购订单币种统计平均采购单价，明细金额除以明细数量，仅输出币种和平均采购单价两列。", "SELECT po.currency,SUM(i.quantity*i.unit_cost)/NULLIF(SUM(i.quantity),0) FROM purchase_order_items i JOIN purchase_orders po ON i.purchase_order_id=po.purchase_order_id GROUP BY po.currency"),
        ("production-rate", "生产完成率", "按产品ID统计整体生产完成率。先按生产单汇总全部实际产出再关联计划，避免重复计划量，仅输出产品ID和生产完成率两列。", "WITH outputs AS (SELECT production_order_id,SUM(good_quantity+defect_quantity) AS qty FROM production_outputs GROUP BY production_order_id) SELECT mo.product_id,SUM(COALESCE(x.qty,0))/NULLIF(SUM(mo.planned_quantity),0) FROM production_orders mo LEFT JOIN outputs x ON x.production_order_id=mo.production_order_id GROUP BY mo.product_id"),
        ("customer-payment-rate", "客户回款率", "按币种统计客户回款率，只统计有已收或部分回款的已完成销售订单；先把同单同币种回款汇总再关联，分母每个对应订单只计一次。仅输出币种和客户回款率两列。", "WITH payments AS (SELECT order_id,currency,SUM(payment_amount) AS paid FROM customer_payments WHERE payment_status IN ('received','partial') GROUP BY order_id,currency) SELECT o.currency,SUM(cp.paid)/NULLIF(SUM(o.net_amount),0) FROM sales_orders o JOIN payments cp ON o.order_id=cp.order_id AND o.currency=cp.currency WHERE o.order_status='completed' GROUP BY o.currency"),
        ("supplier-payment-rate", "采购付款率", "按币种统计采购付款率，只统计有已付或部分付款的采购订单；先把同单同币种付款汇总再关联，分母每个对应采购订单只计一次。仅输出币种和采购付款率两列。", "WITH payments AS (SELECT purchase_order_id,currency,SUM(payment_amount) AS paid FROM supplier_payments WHERE payment_status IN ('paid','partial') GROUP BY purchase_order_id,currency) SELECT po.currency,SUM(sp.paid)/NULLIF(SUM(po.total_amount),0) FROM purchase_orders po JOIN payments sp ON po.purchase_order_id=sp.purchase_order_id AND po.currency=sp.currency GROUP BY po.currency"),
        ("budget-gap", "预算差异", "2026年按成本中心ID统计预算差异，正数为结余，负数为超支。仅输出成本中心ID和预算差异两列。", "SELECT cost_center_id,SUM(budget_amount)-SUM(actual_amount) FROM budgets WHERE budget_year=2026 GROUP BY cost_center_id"),
        ("warehouse-capacity", "仓库容量", "按地区统计启用仓库的仓库容量合计。仅输出地区和仓库容量两列。", "SELECT region,SUM(capacity_mwh) FROM dim_warehouses WHERE warehouse_status='active' GROUP BY region"),
        ("annual-budget", "成本中心年度预算", "按部门ID统计成本中心年度预算合计。仅输出部门ID和成本中心年度预算两列。", "SELECT department_id,SUM(annual_budget) FROM dim_cost_centers GROUP BY department_id"),
        ("channels", "销售渠道数", "销售渠道数总计是多少？只输出一个数值列。", "SELECT COUNT(DISTINCT channel_id) FROM dim_sales_channels"),
        ("workdays", "工作日数", "2026年第三季度工作日数是多少？只输出一个数值列。", "SELECT COUNT(DISTINCT calendar_date) FROM dim_calendar WHERE is_workday=TRUE AND calendar_date>='2026-07-01' AND calendar_date<'2026-10-01'"),
        ("customers", "客户总数", "按国家统计客户总数，包含未成交客户。仅输出国家和客户总数两列。", "SELECT country,COUNT(DISTINCT customer_id) FROM dim_customers GROUP BY country"),
    ]
    return [{"id": "coverage-"+slug, "domain": "完整指标覆盖", "metric": metric, "category": "independent_golden", "difficulty": "medium",
             "question": question, "expected_sql": sql, "business_definition": "独立标准SQL，检查关联粒度、币种与零分母；不从模型输出生成答案。"} for slug,metric,question,sql in definitions]


def temporal_cases():
    source="sales_orders o JOIN exchange_rates r ON o.order_date=r.rate_date AND o.currency=r.currency"
    current=f"SELECT SUM(o.net_amount*r.rate_to_cny) AS rev FROM {source} WHERE o.order_status='completed' AND o.order_date>='2026-09-01' AND o.order_date<'2026-10-01'"
    previous=f"SELECT SUM(o.net_amount*r.rate_to_cny) AS rev FROM {source} WHERE o.order_status='completed' AND o.order_date>='2026-08-01' AND o.order_date<'2026-09-01'"
    yearago=f"SELECT SUM(o.net_amount*r.rate_to_cny) AS rev FROM {source} WHERE o.order_status='completed' AND o.order_date>='2025-09-01' AND o.order_date<'2025-10-01'"
    return [
        {"id":"revenue-month-comparison", "question":"分步骤计算2026年9月完成订单人民币收入及8月同口径收入，再基于上游结果计算9月相对8月的环比增长率，零分母返回NULL。最终表格只输出9月收入、8月收入、环比增长率三列，比例用0到1小数；不据此编造增长原因。", "min_steps":2,"requires_binding":True,
         "expected_sql":f"WITH c AS ({current}), p AS ({previous}) SELECT c.rev,p.rev,(c.rev-p.rev)/NULLIF(p.rev,0) FROM c CROSS JOIN p"},
        {"id":"revenue-year-empty-baseline", "question":"分步骤核算2026年9月与2025年9月完成订单人民币收入，再基于上游结果计算同比增长率。没有去年数据或分母为零时保留NULL，不填0且明确无法判断同比。最终只输出本期收入、同期收入、同比增长率三列，比例用0到1小数。", "min_steps":2,"requires_binding":True,
         "expected_sql":f"WITH c AS ({current}), p AS ({yearago}) SELECT c.rev,p.rev,(c.rev-p.rev)/NULLIF(p.rev,0) FROM c CROSS JOIN p"},
        {"id":"supplier-payment-grain", "question":"分步骤按采购订单和币种汇总已付及部分付款的供应商付款额，再关联对应采购订单按币种计算采购付款率，订单金额不能因多笔付款重复累加。最后从上游汇总结果中仅保留付款率低于1的币种。最终表格只输出币种和采购付款率两列，并说明还需核对到期日才能判断逾期。", "min_steps":2,"requires_binding":True,
         "expected_sql":"WITH payments AS (SELECT purchase_order_id,currency,SUM(payment_amount) AS paid FROM supplier_payments WHERE payment_status IN ('paid','partial') GROUP BY purchase_order_id,currency) SELECT po.currency,SUM(sp.paid)/NULLIF(SUM(po.total_amount),0) FROM purchase_orders po JOIN payments sp ON po.purchase_order_id=sp.purchase_order_id AND po.currency=sp.currency GROUP BY po.currency HAVING SUM(sp.paid)/NULLIF(SUM(po.total_amount),0)<1"},
    ]
