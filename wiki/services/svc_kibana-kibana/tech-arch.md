---
title: kibana 技术架构
doc_type: tech
service_ids:
  - svc_kibana
service_name: kibana
tags:
  - 技术文档
  - ELK
  - Kibana
  - 可视化
  - 日志平台
updated_at: 2026-06-01
---

## kibana 技术架构

### 功能概述

（服务负责什么，在系统中的角色）

### 技术栈

- 语言:
- 框架:
- 数据库:
- 缓存:
- 协议:

### 依赖关系

```
（上游谁调用我 -> 我 -> 我调用哪些下游）
```

### 部署信息

- 主机:
- 端口:
- 健康检查:
- 日志路径:

### 关键配置

```yaml
# 核心配置参数
```

--tags ELK,日志系统 ### 部署信息
- IP: 10.33.17.100 (master-1)
- 端口: 5601
- 进程: Node.js, PID 25339, 启动于 2026-01-23

### 依赖
User → Kibana(5601) → ES(9200)

### 配置路径
- /etc/kibana/kibana.yml
- kibana.service
