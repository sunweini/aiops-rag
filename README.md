# AIOps RAG

ES 全文检索 + Neo4j 图拓扑 + 向量检索 + Rerank，AIOps 多路召回 RAG。

## 架构

```
Query → LLM rewrite + entity extract
      → ES BM25 (IK) + ES Vector (Qwen3-Embedding-8B)
      → Neo4j topology (Service/Host/Port/Cluster/Document)
      → cluster-aware expansion → secondary retrieval
      → RRF → Rerank (Qwen3-Reranker-8B) → LLM answer
```

## 技术栈

| 组件 | 技术 |
|------|------|
| API | FastAPI + Uvicorn |
| 全文检索 | ES 8.x, IK 分词, BM25 |
| 向量检索 | Qwen/Qwen3-Embedding-8B, 4096-dim |
| 图数据库 | Neo4j 5.x |
| Rerank | Qwen/Qwen3-Reranker-8B |
| LLM | deepseek-v4-flash |

## 使用方式

### 方式 1：服务端部署

```bash
git clone https://github.com/sunweini/aiops-rag.git
cd aiops-rag

# 启动 ES + Neo4j + API
docker compose up -d

# 初始化
docker exec rag-api python3 /app/scripts/init-neo4j.py

# 加载拓扑
docker exec rag-api python3 /app/scripts/load-topology.py /app/wiki/topology/call-graph.yml

# 索引文档
docker exec rag-api python3 /app/scripts/index-docs.py
# 或全量: --full, 单文件: --file <path>

# 验证
curl http://localhost:8001/api/v1/health
```

**示例**：

```bash
# 查询
curl -s -X POST http://localhost:8001/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "nginx 502 怎么排查"}' | python3 -m json.tool

# 服务拓扑
curl 'http://localhost:8001/api/v1/topology?service_id=svc_nginx_company' | python3 -m json.tool

# 集群拓扑
curl 'http://localhost:8001/api/v1/cluster/svc_nginx_company' | python3 -m json.tool

# 故障影响
curl 'http://localhost:8001/api/v1/impact?host_id=host_nginx_01' | python3 -m json.tool

# 多跳依赖链
curl 'http://localhost:8001/api/v1/graph/path?service_id=svc_erp_relay_main&depth=3' | python3 -m json.tool

# 服务关联文档
curl http://localhost:8001/api/v1/service/svc_nginx_company/docs | python3 -m json.tool

# 主机关联文档
curl http://localhost:8001/api/v1/host/host_nginx_01/docs | python3 -m json.tool

# 同步健康检查
curl http://localhost:8001/api/v1/health/sync | python3 -m json.tool

# 更新节点
curl -X PATCH http://localhost:8001/api/v1/node/Host/host_nginx_01 \
  -H 'Content-Type: application/json' -d '{"os":"Rocky Linux 9"}'
```

**增删文档**：

```bash
# 添加 SOP — 编辑 wiki/services/ 下 .md → 增量索引
docker exec rag-api python3 /app/scripts/index-docs.py

# 单文件索引
mkdir -p wiki/services/svc_myapp-my-service
cp /tmp/my-sop.md wiki/services/svc_myapp-my-service/
docker exec rag-api python3 /app/scripts/index-docs.py --file services/svc_myapp-my-service/my-sop.md

# 删除 — rm .md + 增量索引
rm wiki/services/svc_myapp-my-service/my-sop.md
docker exec rag-api python3 /app/scripts/index-docs.py
```

### 方式 2：OpenClaw Skill 接入

前提：服务端已部署。配置 OpenClaw 使用 `/root/.openclaw/skills/aiops-rag/aiops-query` CLI。

```bash
# 安装 Skill
chmod +x ~/.openclaw/skills/aiops-rag/aiops-query

# 查询
./aiops-query query 'nginx 502 怎么排查'
./aiops-query topology svc_nginx_company
./aiops-query cluster svc_nginx_company

# 主机故障影响
./aiops-query impact host_nginx_01

# 文档查反向
./aiops-query service-docs svc_nginx_company
./aiops-query host-docs host_nginx_01
./aiops-query doc-services svc_nginx_company_tech-arch

# 索引维护
./aiops-query index              # 增量
./aiops-query index --full       # 全量
./aiops-query index-file services/svc_nginx_company-company-nginx-cluster/tech-arch.md

# 更新节点属性
./aiops-query update-node Host host_nginx_01 os 'Rocky Linux 9'
./aiops-query update-node Service svc_nginx_company status healthy

# 一致性检查
./aiops-query sync-health
./aiops-query health
```

## API

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/query` | 多路检索 + 图富化 + LLM |
| GET | `/api/v1/topology?service_id=` | 服务拓扑 |
| GET | `/api/v1/cluster/{id}` | 集群拓扑 |
| GET | `/api/v1/impact?host_id=` | 主机影响分析 |
| GET | `/api/v1/graph/path?service_id=` | 依赖链 (depth=1-4) |
| GET | `/api/v1/graph/circular` | 循环依赖检测 |
| GET | `/api/v1/service/{id}/docs` | 服务关联文档 |
| GET | `/api/v1/host/{id}/docs` | 主机关联文档 |
| GET | `/api/v1/document/{id}/services` | 文档关联服务 |
| GET | `/api/v1/document/{id}/hosts` | 文档关联主机 |
| PATCH | `/api/v1/node/{Label}/{id}` | 更新节点属性 |
| GET | `/api/v1/health` | 健康检查 (含 sync) |
| GET | `/api/v1/health/sync` | ES↔Neo4j 一致性报告 |
| GET | `/api/v1/metrics` | 监控指标 |

## 分块策略

| 文档类型 | 策略 |
|---------|------|
| SOP | 父子块 (### section → parent ≤800c + child 200-300c) |
| tech/incident | 层次切分 (##→### 递归 → 段落合并 3-5 → 句子 fallback) |
| 全类型 | 占位符过滤 + 标题继承 + 表格/代码块不切 |

## License

MIT
