---
title: SQLServer AlwaysOn 集群 技术架构
doc_type: tech
service_ids:
  - svc_db_alwayson
service_name: SQLServer AlwaysOn 集群
tags:
  - ERP
  - 数据库
  - SQLServer
  - AlwaysOn
  - 集群
updated_at: 2026-06-04
---

## SQLServer AlwaysOn 集群 技术架构

### 概述

ERP 集团版数据库层，采用 SQLServer AlwaysOn 三节点集群提供高可用。主节点 10.33.17.124，侦听器 VIP 为 10.33.17.127，端口 1433。所有 ERP 应用服务、中转服务均通过侦听器访问。

### 集群节点

| 节点 | IP | 角色 | 端口 |
|------|-----|------|------|
| MSSQL-Master | 10.33.17.124 | 主节点 | 1433 |
| MSSQL-slave | 10.33.17.125 | 从节点 | 1433 |
| MSSQL-Backup | 10.33.17.126 | 备份节点 | 1433 |

侦听器 VIP: 10.33.17.127:1433

### 依赖

- 上游调用: svc_app01, svc_app02, svc_erp_relay_main, svc_qys_relay_main, svc_tianqu_relay_main
- 下游: 无

### 运维

- AlwaysOn 故障切换由集群自动处理，一般无需人工干预
- 不要同时重启两个以上节点，避免集群仲裁失效
- 监控告警: 端口1433不可达, 数据库连接数>80%
