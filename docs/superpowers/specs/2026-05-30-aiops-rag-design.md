# AIOps 知识库 RAG 系统设计文档

**日期**: 2026-05-30
**状态**: 设计稿

---

## 1. 背景与目标

运维团队需要一个 AIOps 知识库 RAG 系统，用于查询服务信息、主机信息、端口信息、服务间调用关系，以及关联的 SOP 文档和技术文档。系统通过 OpenClaw Agent Skill 或 MCP 协议接入，供运维团队多人使用。

## 2. 技术方案选型

严格依据 doc/ 目录已有技术文档进行方案设计：

| 参考文档 | 应用位置 |
|---------|---------|
| ES 全文检索原理：IK+BM25 | ES 检索引擎 |
| 混合检索 RAG 实战：全文+向量+Rerank | 多路召回+精排 |
| Neo4j 知识图谱：实体建模+Cypher+LangChain | 图检索引擎 |
| 检索策略终极选型 | 方案决策依据 |
| RAG 精排层：Cohere Rerank + Cross-Encoder | Rerank 层 |

### 选型结论：ES + Neo4j + 向量检索 三引擎方案

- **ES (IK+BM25)**：精确关键词匹配（错误码、服务名、型号）
- **向量检索 (Embedding)**：语义相似匹配
- **Neo4j**：服务/主机/端口拓扑 + 调用关系
- **Rerank**：多路召回结果合并重排

## 3. 系统架构

```
                    ┌──────────────────────────────────┐
                    │         OpenClaw 平台             │
                    │  ┌──────────┐  ┌───────────────┐  │
                    │  │ Agent    │  │ MCP Server    │  │
                    │  │ Skill    │  │ (工具/资源)    │  │
                    │  └────┬─────┘  └──────┬────────┘  │
                    └───────┼───────────────┼───────────┘
                            │               │
                    ┌───────▼───────────────▼───────────┐
                    │      RAG API Server (FastAPI)      │
                    │  POST /query — 统一查询入口        │
                    │  POST /index — 文档入库             │
                    │  GET  /topology — 拓扑查询          │
                    │  GET  /health — 健康检查             │
                    └──────┬────────────────┬────────────┘
                           │                │
                    ┌──────┴─────┐   ┌─────┴──────┐
                    │ 检索引擎   │   │   Neo4j    │
                    │ ES + 向量  │   │   Docker   │
                    │ + Rerank   │   │ 图检索      │
                    └────────────┘   └────────────┘
```

### 查询流

```
用户查询 → 意图路由
            │
     ┌──────┼──────────┐
     │      │          │
  ES全文  向量检索   Neo4j
  IK+BM25 Embedding  图查询
  (精确词) (语义)   (拓扑)
     │      │          │
     └──────┴──────────┘
            │
      Rerank 精排
  (Cohere / Cross-Encoder)
            │
       LLM 生成回答
```

## 4. 实体模型

### Neo4j 图模型

参考 doc/Neo4j 知识图谱文档进行实体建模。

```
(Service {id, name, status, description})
    │
    ├─[:DEPLOYS_ON]→(Host {id, name, ip, os})
    │                    │
    │                    └─[:HAS_PORT]→(Port {number, protocol, status})
    │
    └─[:CALLS]→(Service {id, name, protocol, port})
```

**节点属性**：

| 节点 | 属性 |
|------|------|
| Service | id (唯一标识), name, status (running/stopped/unknown), description |
| Host | id, name, ip, os |
| Port | number, protocol (tcp/udp/http/grpc), status (open/closed) |

**关系**：

| 关系 | 源→目标 | 含义 |
|------|---------|------|
| DEPLOYS_ON | Service → Host | 服务部署在该主机 |
| HAS_PORT | Host → Port | 主机开放端口 |
| CALLS | Service → Service | 服务间调用依赖（含 protocol, port 属性） |

### ES 索引映射

参考 doc/ES 文档的 IK+BM25 索引配置：

```json
{
  "settings": {
    "index": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "similarity": {
        "custom_bm25": {
          "type": "BM25",
          "k1": 1.2,
          "b": 0.75
        }
      }
    },
    "analysis": {
      "analyzer": {
        "ik_index_analyzer": {
          "type": "custom",
          "tokenizer": "ik_max_word",
          "filter": ["lowercase"]
        },
        "ik_search_analyzer": {
          "type": "custom",
          "tokenizer": "ik_smart",
          "filter": ["lowercase"]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "title":       { "type": "text", "analyzer": "ik_index_analyzer", "search_analyzer": "ik_search_analyzer", "boost": 2.0 },
      "content":     { "type": "text", "analyzer": "ik_index_analyzer", "search_analyzer": "ik_search_analyzer" },
      "doc_type":    { "type": "keyword" },
      "service_id":  { "type": "keyword" },
      "service_name":{ "type": "keyword" },
      "tags":        { "type": "keyword" },
      "host_ids":    { "type": "keyword" },
      "updated_at":  { "type": "date" }
    }
  }
}
```

### 实体关联方式

- 每个 Service 分配不可变唯一标识 `svc_xxx`
- ES 文档 metadata 通过 `service_id` 关联到 Neo4j Service 节点
- 服务名可变，但 ID 不变，所有关联不受影响
- 支持别名和曾用名

## 5. 维护规范

### 文档目录结构

```
aiops-docs/
├── services/
│   ├── svc_abc123-order-service/
│   │   ├── README.md           # 服务简介
│   │   ├── sop-restart.md      # 重启 SOP
│   │   ├── sop-scale.md        # 扩容 SOP
│   │   └── tech-arch.md        # 技术架构文档
│   └── svc_def456-nginx-gateway/
│       └── ...
├── hosts/
│   └── host_prod_web_01.md     # 主机信息
├── incidents/
│   └── 2026-05-30-nginx-502.md # 故障复盘
└── topology/
    └── call-graph.yml          # 调用关系定义
```

### 文档 Frontmatter 规范

每篇 Markdown 文件头部必须包含 YAML frontmatter：

```yaml
---
title: 订单服务重启 SOP
doc_type: sop                    # sop | tech | incident
service_id: svc_abc123
service_name: order-service
tags:
  - 重启
  - 运维
related_services:
  - svc_def456
related_hosts:
  - host_prod_web_01
author: 伟倪
updated_at: 2026-05-30
---
```

### 调用关系定义

topology/call-graph.yml：

```yaml
services:
  - id: svc_abc123
    name: order-service
    deploys_on: host_prod_web_01
    calls:
      - target: svc_def456
        protocol: grpc
        port: 50051
      - target: svc_ghi789
        protocol: http
        port: 8080
    ports:
      - 8080
      - 50051

hosts:
  - id: host_prod_web_01
    name: prod-web-01
    ip: 192.168.1.10
    os: Ubuntu 22.04
```

### 入库流程

入库脚本执行：
1. 递归扫描 `aiops-docs/` 目录下所有 Markdown 文件
2. 解析 YAML frontmatter 获取 metadata
3. ES 索引：参考 doc/ES 文档的索引配置写入
4. Neo4j：解析 call-graph.yml 创建/更新节点和关系
5. 验证：检测未注册的 service_id、孤立节点，生成告警

## 6. 查询流程

### 问题分类与路由

| 问题类型 | 示例 | 检索策略 |
|---------|------|---------|
| SOP 查询 | "Redis 扩容步骤" | ES 全文 + 向量检索 + Rerank |
| 拓扑查询 | "订单服务部署在哪台机器" | Neo4j Cypher |
| 故障排查 | "nginx 502 怎么排查" | ES + Neo4j + 向量 + Rerank |
| 混合问题 | "订单服务连不上数据库了" | 并行查多方 + Rerank + LLM 综合 |

### API 接口

```
POST /query
{
  "query": "nginx 502",
  "type": "auto"       // auto | sop | topology | incident
}
→ {
    "answer": "根据 SOP...步骤1...步骤2...",
    "sources": [
      { "title": "nginx-502排查", "score": 0.95, "engine": "es" },
      { "title": "nginx依赖服务", "type": "topology", "engine": "neo4j" }
    ]
  }

POST /topology?service_id=svc_abc123
→ { "deploys_on": "prod-web-01", "calls": [...], "ports": [...] }
```

## 7. 部署方案

Docker Compose 单独部署（非 K3s），三容器：

| 容器 | 用途 | 端口 |
|------|------|------|
| elasticsearch | 全文检索引擎 | 9200 |
| neo4j | 图数据库 | 7474 (console), 7687 (bolt) |
| api-server | FastAPI 应用 | 8000 |

部署参考 doc/ES 文档的 Docker Compose 配置方式。

## 8. 向量模型 + Rerank

- **Embedding**: 参考 doc/混合检索文档，使用开源嵌入模型做向量化
- **Rerank**: 参考 doc/RAG 精排层文档，使用 Cohere Rerank 或 Cross-Encoder 做结果重排
- 向量检索仅用于语义相似度匹配，不做主检索

---

## 附：设计依据

本文档所有技术设计严格依据 `/root/.openclaw/workspace-shared/rag/doc/` 目录下的以下文件：

1. `检索策略终极选型：全文检索 vs 向量检索 vs 图检索.md`
2. `混合检索 RAG 实战：ES 全文检索 + 向量检索多路召回 + Rerank 重排.md`
3. `ElasticSearch全文检索原理-IK分词-BM25.md`
4. `Neo4j 知识图谱：实体建模+Cypher查询+LangChain接入.md`
5. `Graph RAG 进阶：用 Neo4j 做多跳推理.md`
6. `RAG 精排层：Cohere Rerank + Cross-Encoder 召回质量提升.md`
