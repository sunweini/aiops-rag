---
title: logstash 技术架构
doc_type: tech
service_ids:
  - svc_logstash
service_name: logstash
tags:
  - 技术文档
  - ELK
  - Logstash
  - 日志采集
  - 管道
  - 集群
updated_at: 2026-06-01
---

## logstash 技术架构

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

--tags ELK,日志系统 ### 功能概述
Logstash 日志处理服务，接收 Syslog/Beats 数据，处理后输出到 ES 集群。

### 节点信息
| 节点 | IP | CPU | 内存 | 磁盘 |
|------|-----|-----|------|------|
| logstash-1 | 10.33.17.105 | 12.5% | 62.7% | 11% |
| logstash-2 | 10.33.17.106 | 23.5% | 62.3% | 24% |

### VIP
10.33.17.107 — 负载均衡入口

### 输入端口
- UDP-5514: Syslog GBK
- UDP-514: Syslog UTF-8
- TCP-5555: Beats

### 输出
- ES HTTP API: 9200

### 依赖关系
Syslog/Beats → Logstash → Elasticsearch(9200)

### Pipeline 配置
- 目录: /etc/logstash/conf.d/
- 包含防火墙(10-firewalld)、应用(20-30)、安全(40-dbaudit)等分类管道
