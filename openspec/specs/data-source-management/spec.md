# data-source-management Specification

## Purpose

让业务管理员通过明确的数据源接入流程使用真实 MySQL 数据，保留凭据机密与表字段授权边界，使指标、案例、查询和历史不会混用不同业务数据源的上下文。

## Requirements

### Requirement: 授权数据源接入
系统 SHALL 提供数据源创建、连接测试、元数据发现及授权表字段管理，凭据 MUST 加密存储且不经 API 返回。每个数据源 SHALL 使用独立指标目录和权限快照。

#### Scenario: 发现真实库结构
- **WHEN** 管理员连接授权 MySQL 库并选择业务表
- **THEN** 查询只允许选定表字段，系统表不进入查询上下文

#### Scenario: 无权访问数据源
- **WHEN** 用户提交其他租户或未获授权的数据源 ID
- **THEN** 查询、指标、案例和历史请求均拒绝访问
