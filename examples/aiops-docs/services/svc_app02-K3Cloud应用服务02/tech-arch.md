---
title: K3Cloud应用服务02 技术架构
doc_type: tech
service_ids:
  - svc_app02
service_name: K3Cloud应用服务02
tags:
  - ERP
  - K3Cloud
  - 应用服务器
  - Windows Server
updated_at: 2026-06-04
---

## K3Cloud应用服务02 技术架构

### 概述

K3Cloud 应用服务02，ERP集团版备用应用节点。部署于 Windows Server（10.33.17.121），提供与 svc_app01 同等的 ERP 业务逻辑处理能力。对外通过 Nginx VIP 暴露 9599 端口，内部通过 8088 连接文件、管理中心及中转服务。

### 节点

| 节点 | IP | 端口 |
|------|-----|------|
| 应用服务器02 | 10.33.17.121 | 9599, 8088 |

### 依赖

- 上游: company-nginx-cluster
- 下游: svc_db_alwayson (tcp:1433), svc_file_server (tcp:8088), svc_mgmt_center (tcp:8000), svc_erp_relay_main (tcp:8088), svc_qys_relay_main (tcp:5962), svc_tianqu_relay_main (tcp:5962)

### 运维

- 主节点 svc_app01 故障时可切换至此节点
- 重启需在业务低峰期执行
