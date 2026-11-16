# 产品化真实模型验收（2026-11-16）

完整真实模型验收已完成 **139/139** 题，发布门禁 **通过**。SQL 结果题 117/117、复杂结果 6/6、行为题 16/16。复杂模型报告通过 5/6；可信模板回退 1 份，已明确披露。本机源指纹 `ddf150b8ba6a109a`，配置、目录与题库在整轮中一致；没有调低门槛或跨版本续跑。

| 项目 | 结果 |
| --- | --- |
| SQL 执行等价 | 117/117 |
| 复杂结果等价 | 6/6 |
| 问候、澄清、追问 | 16/16 |
| 执行不匹配的题目 | 无 |
| 模型报告回退题目 | supplier-payment-grain |
| SQL 题 P95 | 5535.99 ms |
| 复杂题 P95 | 32457.7 ms |

| 复杂分析门禁 | 实测 |
| --- | --- |
| planning_success_rate | 1.0000 |
| sql_step_success_rate | 1.0000 |
| full_success_rate | 1.0000 |
| partial_success_rate | 0.0000 |
| evidence_reference_accuracy | 1.0000 |
| recommendation_grounding_rate | 1.0000 |
| result_accuracy | 1.0000 |
| dependency_success_rate | 1.0000 |
| summary_model_success_rate | 0.8333 |

模型角色使用 DeepSeek Flash，意图为千问 qwen-plus。真实调用、用量、逐题 SQL 及结果存在本机 `reports/product-release-20261116.json`，默认看板读取 `reports/release-quality.json`。

修复前完整轮的 SQL 为 115/117，复杂结果为 6/6，但模型报告成功率只有 2/6，整体未通过。该轮完整保留；另一次在发现并发初始化与报告修复问题后中止，未合并到最终成绩。失败推动修复真实字段类型、证据定位、中文措辞误判及并发 WAL 初始化；标准 SQL 与门槛未改变。

模型报告失败会保留可核验结果并回退确定性模板；此状态不会伪装成模型总结成功。正文数字及显式比较校验不等于证明因果、隐含语义、业务建议效果或真实源数据正确，仍需人工复核。

源指纹：`ddf150b8ba6a109a`；目录：`9f0a7d66a78fd9149d42a30a024638019922aae432f88bb4e2661cbb7614a50c`；题库：`0b874aca74b6474721553114d8adbb0cc5beac0f5b8a816f9a71ebcd0ec2dda1`；配置：`700479bd607710f1c2f63f5969c9305e05a32bf8d5e0b7c45c660048c9bbe8a6`。
