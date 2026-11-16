# OpenSpec 实现核对（2026-11-16）

## 初版：deliver-chatbi-mvp

41/41 任务完成，27 个 ADDED Requirement 的实现均已对应。安全、口径、API 和错误契约通过当前自动化回归；原五表、单用户及会话历史是初版范围，后续按用户已授权需求扩展为 30 表、61 指标、账号隔离和持久历史。本次不把初版 Non-Goals 当作限制后续授权的约束。

| 能力 | 要求数 | 实现与场景证据 |
| --- | ---: | --- |
| chatbi-query | 5 | `query_service.py`、Prompt/Schema/指标适配；`test_query_service.py`、`test_api.py`、`test_prompt_builder.py` |
| local-mvp-runtime | 6 | 锁文件、`.env.example`、`db/`、初始化/索引/启动器；数据库初始化集成 CI、`test_settings.py`、`test_launcher.ps1` |
| sql-query-safety | 6 | `domain/security.py`、连接池和只读账号；`test_sql_security.py`、P0/故障控制真实 MySQL 测试 |
| text2sql-quality-gates | 5 | 独立标准 SQL、安全执行评估器、真实模型门禁；基准结构、执行等价和全部 SQL 的真实 MySQL 检查 |
| web-query-experience | 5 | 对话输入、执行状态、SQL/数据视图、错误反馈、历史；41 项 Node 测试和真实浏览器回车查询 |

初版固定模型验收已有 121/121 历史证据，当前版本再执行扩展 139 题评测。既有需求未删除或重命名；主规格保留原 Purpose 与全部场景。当前产品提供更多历史与身份能力，新增规格明确隔离规则。无未实现要求、关键偏差或跳过的规格核对。

## 产品化：complete-chatbi-product-capabilities

本变更有 7 个 ADDED Requirement、12 个 Scenario，按下表逐项对应；最终完整评测和交付状态见 `delivery-20261116.md`。

| 要求 | 实现 | 验证 |
| --- | --- | --- |
| 身份与角色授权 | `control_store.py`、`api/control.py`、登录/管理页 | 账号初始化、PBKDF2、会话撤销、角色和跨租户负例；真实浏览器登录/退出 |
| 租户与历史隔离 | `product_runtime.py`、历史和任务 API | 历史、父查询、事件、证据、权限版本的越权测试；分用户/源保留额度 |
| 授权数据源接入 | `data_sources.py`、SourceSchema、管理页 | 密钥不回传、只读权限、表字段白名单；真实 MySQL 30 表发现、授权源只读执行、前端源切换 |
| 有界并发与共享任务协调 | `query_jobs.py`、`shared_queue.py`、执行 Agent、中心控制适配器 | 双 OS 进程只领取一次、租约 fencing、恢复冲突、独立 DAG 重叠执行、核心失败级联取消；真实 PostgreSQL CI |
| 经审核反馈案例 | `api/feedback.py`、管理页 | 独立结果比对、自审拒绝、审核发布、过期/跨源隔离和召回；真实模型查询及另一审核账号发布；浏览器导出独立结果回归集 |
| 正文可验证陈述 | `narrative_verification.py`、总结修复/模板、报告 UI | 伪数字、错误比较、无据因果、金额显示精度；保留人工语义/因果判断边界 |
| 全指标及复杂场景基准 | `product_benchmark.py`、三份版本题库、评测指纹 | 61 指标覆盖断言、全部标准 SQL AST/真实 MySQL 执行、完整题库模型与配置来源检查 |

已完成的恢复演练包括：本地 SQLite 控制/历史/指标/自定义源目录 4 个数据库通过 integrity_check、表计数和原凭据密钥核对；GitHub 独立 PostgreSQL 备份恢复后 25 张表的完整行内容一致。测试输出不包含口令或业务值。

真实浏览器使用 `.runtime/ui-check/` 隔离验收库，初始化页、账号登录、问候路由、回车执行、数据源切换、独立资产计数、结果、反馈发布显示、标准 SQL、回归导出和退出均核对；未替用户设置生产管理员密码。截图保存在 `reports/product-workspace-20261116.jpg`、`reports/product-management-20261116.jpg`。

## 核验结论边界

SQL 和证据检查可重复验证固定数据与题库；报告数值和显式比较检查不等于证明所有自然语言语义、因果关系或实际业务收益。不同真实库接入后须建立自己的指标和独立答案再验收。当前多主机控制事务按 store 串行化，未声称无限水平吞吐或高并发 SLA。

主规格与 delta 已逐块比对，所有要求和场景一致，11 份主规格和 2 项变更的 `openspec validate --strict` 通过。没有移除/重命名要求，不涉及此类基线检查。
