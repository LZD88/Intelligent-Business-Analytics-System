# feedback-learning Specification

## Purpose

把查询反馈转为可追踪、可审核的纠错知识，保留原始问题、错误与修正结果，使后续检索和回归能够利用经验证的案例，同时隔离不同租户及数据源的知识。

## Requirements

### Requirement: 经审核反馈案例
系统 SHALL 保存纠错草稿、审核记录和标准 SQL，只有有权限的审核者在安全执行验证后才能发布。未发布、跨租户、跨数据源或口径过期的案例 MUST NOT 注入查询。

#### Scenario: 发布错误 SQL
- **WHEN** 审核者发布危险 SQL 或结果不符合标准的案例
- **THEN** 系统拒绝发布，记录保持未生效

#### Scenario: 合法案例生效
- **WHEN** 已校验案例被发布
- **THEN** 同租户同数据源查询可召回该案例，并可导出独立回归集
