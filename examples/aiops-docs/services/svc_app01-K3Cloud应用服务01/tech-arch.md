---
title: K3Cloud应用服务01 技术架构
doc_type: tech
service_ids:
  - svc_app01
service_name: K3Cloud应用服务01
tags:
  - ERP
  - K3Cloud
  - 应用服务器
  - Windows Server
updated_at: 2026-06-04
---

## K3Cloud应用服务01 技术架构

### 概述

K3Cloud 应用服务，ERP集团版核心应用层。部署于 Windows Server（10.33.17.120），端口 9999 对外开放，内部 8088 连接文件及中转服务。

### 节点

| 节点 | IP | 端口 |
|------|-----|------|
| 应用服务器01 | 10.33.17.120 | 9999, 8088 |

### 依赖

- 上游: company-nginx-cluster, 契约锁中转, 天枢中转
- 下游: svc_db_alwayson (tcp:1433), svc_file_server (tcp:8088), svc_mgmt_center (tcp:8000), svc_erp_relay_main (tcp:8088), svc_qys_relay_main (tcp:5962), svc_tianqu_relay_main (tcp:5962)

### 运维

- 主节点故障确认VIP是否自动切换到 svc_app02（10.33.17.121）
- 监控: 9999不可达, 内存>85%, 磁盘>85%
