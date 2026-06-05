---
title: elasticsearch 技术架构
doc_type: tech
service_ids:
  - svc_es
service_name: elasticsearch
tags:
  - 技术文档
  - ELK
  - Elasticsearch
  - 搜索引擎
  - 中间件
  - 集群
updated_at: 2026-06-01
---

## elasticsearch 技术架构

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

--tags ELK,日志系统 ### 节点信息
| 节点 | IP | 角色 | JVM | 内存 | CPU | 磁盘 |
|------|-----|------|-----|------|-----|------|
| master-1 | 10.33.17.100 | Master+Kibana | 8g | 16GB | 3.3% | 8% |
| master-2 | 10.33.17.101 | Master | 6g | 16GB | 3.1% | 7% |
| master-3 | 10.33.17.102 | Master | - | 16GB | 5.4% | 6% |
| node-1 | 10.33.17.103 | Data | 22g | 47GB | 100% | 11% |
| node-2 | 10.33.17.104 | Data | 22g | 47GB | 105% | 11% |

### 端口
- 9200: HTTP API
- 9300: Transport

### 安全
x-pack security, HTTPS + Basic Auth

### 配置路径
- 安装: /data/elasticsearch-8.3.3/
- 配置: /data/elasticsearch-8.3.3/config/elasticsearch.yml

### 已知问题
Data Node CPU 100%/105% — G1ReservePercent=25% 导致 GC 风暴（93万+次），建议降到 10%
