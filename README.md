# AIOps RAG — 运维知识库检索增强生成系统

ES 全文检索 + Neo4j 知识图谱 + 向量检索 + Rerank 的多路召回 RAG 系统，专为 AIOps 运维场景设计。

## 架构

```
用户查询 → query rewrite + entity extract (LLM)
       → ES 全文检索 (IK+BM25) + 向量检索 (Qwen3-Embedding-8B)
       → Neo4j 图拓扑 (Service/Host/Port/Cluster 关系) + 集群感知展开
       → 拓扑→文档二次检索 (query-aware)
       → RRF fusion → Rerank (Qwen3-Reranker-8B) → LLM 合成答案
```

## 技术栈

| 组件 | 技术 |
|------|------|
| API | FastAPI + Uvicorn |
| 全文检索 | Elasticsearch 8.x + IK 分词 + BM25 |
| 向量检索 | Qwen/Qwen3-Embedding-8B (4096-dim) via SiliconFlow |
| 图数据库 | Neo4j 5.x (Service/Host/Port/Cluster/Document) |
| Rerank | Qwen/Qwen3-Reranker-8B via SiliconFlow |
| LLM | deepseek-v4-flash via DeepSeek API |
| 分块策略 | SOP 父子块 + tech/incident 层次切分 |

## 快速开始

```bash
# 启动服务
docker compose up -d

# 初始化 Neo4j schema
docker exec rag-api python3 /app/scripts/init-neo4j.py

# 加载拓扑 (call-graph.yml)
docker exec rag-api python3 /app/scripts/load-topology.py /app/wiki/topology/call-graph.yml

# 索引入库 (增量，mtime diff)
docker exec rag-api python3 /app/scripts/index-docs.py

# 强制全量重建
docker exec rag-api python3 /app/scripts/index-docs.py --full
```

## CLI (aiops-query)

```bash
chmod +x skills/aiops-query

# 查询
./skills/aiops-query query 'nginx 502 排查'
./skills/aiops-query topology svc_nginx_company
./skills/aiops-query cluster svc_nginx_company
./skills/aiops-query service-docs svc_nginx_company

# 运维
./skills/aiops-query index              # 增量索引
./skills/aiops-query index --full       # 强制全量
./skills/aiops-query index-file services/svc_nginx/tech-arch.md
./skills/aiops-query sync-health         # ES ↔ Neo4j 一致性检查
./skills/aiops-query update-node Host host_nginx_01 os 'Rocky Linux 9'
```

## API 端点

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/query` | 多路检索 + 图富化 + LLM 合成 |
| GET | `/api/v1/topology?service_id=xxx` | 服务拓扑 |
| GET | `/api/v1/cluster/{service_id}` | 集群拓扑 |
| GET | `/api/v1/impact?host_id=xxx` | 故障影响分析 |
| GET | `/api/v1/graph/path?service_id=xxx` | 多跳依赖链 |
| GET | `/api/v1/service/{id}/docs` | 服务关联文档 |
| GET | `/api/v1/host/{id}/docs` | 主机关联文档 |
| GET | `/api/v1/document/{id}/services` | 文档关联服务 |
| GET | `/api/v1/document/{id}/hosts` | 文档关联主机 |
| GET | `/api/v1/health` | 健康检查 (含 ES↔Neo4j sync) |
| GET | `/api/v1/health/sync` | 同步一致性检查详情 |
| PATCH | `/api/v1/node/{Label}/{id}` | 更新节点属性 |
| GET | `/api/v1/metrics` | 监控指标 |
| GET | `/api/v1/graph/circular` | 循环依赖检测 |

## 目录结构

```
├── app/               # FastAPI 应用 (routes, retrievers, indexer, reranker, router)
├── skills/            # OpenClaw AIOps RAG Skill (SKILL.md + aiops-query CLI + templates)
├── wiki/              # 知识文档源 (services/ incidents/ topology/ hosts/)
├── scripts/           # 索引/初始化/评估脚本
├── tests/             # pytest
├── docs/              # 维护指南 + superpowers specs/plans
└── docker-compose.yml
```

## 分块策略

| 文档类型 | 策略 | 效果 |
|---------|------|------|
| SOP | 父子块：### section → parent(≤800c) + child(200-300c) | 检索命中 child → 返回完整 section |
| tech/incident | 层次切分：##→### 递归 → 段落合并(3-5段) → 句子级 fallback | chunk 带标题路径，可溯源 |
| 全类型 | 占位符过滤 + 标题继承 + 表格/代码块不切 | 无模板残留 |

## License

MIT
