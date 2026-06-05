# ES 文档与 Neo4j 知识图谱关联设计

日期: 2026-06-02 | 状态: 已确认

## 背景

当前 AIOps RAG 系统有两套独立存储：
- ES (`knowledge_base`) — 文档全文索引 + 向量检索。通过 markdown frontmatter 的 `service_id` 与图关联
- Neo4j — 服务拓扑图 (Service/Host/Port + DEPLOYS_ON/CALLS 边)

问题：关联仅靠 `service_id` 字符串，单向（文档→服务），无引用完整性，文档不是图的节点，拓扑 YAML 里 `cluster_nodes` 未被加载，检索无关文档污染 LLM 上下文。

## 设计目标

1. 文档与图双向关联：ES 命中 → 图拓扑；图查询 → 相关文档
2. 集群感知：VIP IP 查询自动展开集群所有成员
3. 查询感知富化：拓扑相关文档二次过 ES relevance filter，不无脑全量
4. 引用完整性：健康检查可发现孤儿文档、悬空引用
5. 写入流程不变：markdown 文件 → `_index()` 自动同步 ES + Neo4j

## 数据模型

### ES 文档 (`knowledge_base`)

frontmatter 变化：`service_id`（单值）→ `service_ids`（数组）

```yaml
title: "nginx 502 故障排查 SOP"
doc_type: sop
service_ids:
  - svc_nginx_company
host_ids:
  - host_nginx_01
tags: [故障, 502, 排查]
updated_at: "2026-06-02"
```

### Neo4j 图（完整 schema）

新增节点类型：Document（轻量索引）、Cluster（集群拓扑）

```
(:Service {id, name, status})
(:Host {id, name, ip, os})
(:Port {number, protocol, status})
(:Document {id, title, type, updated_at})          ─ 新增，完整内容在 ES
(:Cluster {service_id, name, vip})                  ─ 新增，从 cluster_nodes YAML 推导

边:
(Service)-[:DEPLOYS_ON]->(Host)
(Service)-[:CALLS {protocol, port}]->(Service)
(Host)-[:HAS_PORT]->(Port)
(Service)-[:HAS_DOC {doc_type, relevance}]->(Document)   ─ 新增
(Host)-[:PART_OF {role}]->(Cluster)                       ─ 新增 (YAML cluster_nodes)
(Service)-[:BELONGS_TO]->(Cluster)                       ─ 新增 (隐式推导)
```

**relevance 枚举**：`primary`（主属）、`secondary`（涉及不主属）、`mentioned`（仅提到）

**Cluster 从 YAML cluster_nodes 推导**：

```yaml
services:
  - id: svc_nginx_company
    deploys_on: host_nginx_01
    cluster_nodes:
      - host: host_nginx_01, role: 主节点, ip: 10.33.16.42
      - host: host_nginx_02, role: 从节点, ip: 10.33.16.43
      - host: host_nginx_vip, role: VIP 负载均衡, ip: 10.33.16.244
```

→ Neo4j: `(host_nginx_01)-[:PART_OF {role:"主节点"}]->(:Cluster {name:"company-nginx-cluster", vip:"10.33.16.244"})`

## 写入流程

**数据校验规则**：
- `service_ids` 必须为非空数组（schema 校验拒绝空数组）
- 每个 service_id 必须以 `svc_` 开头（已有校验）
- 写入 HAS_DOC 前检查 Service 在 Neo4j 是否存在，不存在则拒绝写入 + console.error

**部署前置条件**：
1. 先跑 `init-neo4j.py` 创建 Document 约束 + Cluster 索引
2. 验证约束就位
3. 再跑 `index-docs.py --clean` 全量 reindex
4. `load-topology.py` 加载 cluster_nodes
5. 重启 API

**ES mapping 迁移**：`service_id` 单值 → `service_ids` 数组。全量 reindex（`--clean`），不留旧字段兼容。

流程入口不变：markdown 文件 → `_index()` → 并行更新 ES + Neo4j

```
aiops-query write-* → 写 .md 文件
                    → _index()
                         ├── index-docs.py: parse frontmatter → ES 索引 chunk
                         │                   → upsert Neo4j Document 节点
                         │                   → merge HAS_DOC 边 (per service_ids)
                         │                   → 若 Service 不存在→轻量创建(id/name)，后续 load-topology 补全
                         └── load-topology.py: YAML → Service/Host/Port + cluster_nodes → Cluster 节点 + PART_OF/BELONGS_TO
```

## 检索流程（查询感知 + 集群感知）

关键原则：拓扑→文档二次检索通过 ES relevance filter 防污染；集群查询通过 entity extraction 防无关注入。

```
用户问 "10.33.16.42 上 nginx 502 排查"
  │
  ├─ 1) query rewrite + entity extract + intent classify
  │       rewritten: "nginx 502 故障排查"
  │       entities: {host_ip: "10.33.16.42", service: "nginx", symptom: "502"}
  │       intent: ["incident", "topology"]
  │
  ├─ 2) ES 多路检索 (BM25 + vector) → 3篇
  │
  ├─ 3) 取命中文档 service_ids → {svc_nginx_company}
  │
  ├─ 4) Neo4j 集群感知拓扑:
  │     MATCH (h:Host {ip:"10.33.16.42"})-[:PART_OF]->(c:Cluster)
  │     → 发现是集群成员，展开所有 PART_OF host
  │     → 集群内 Service 部署、调用链
  │     若 h IP = 某 Cluster 的 vip → 同样展开
  │
  ├─ 5) 拓扑相关文档二次检索:
  │     MATCH (s:Service {id:"svc_nginx_company"})-[:HAS_DOC]->(d)
  │     → 20个 doc_id
  │     → ES: doc_ids filter + "nginx 502 排查" query → 仅 4/20 相关
  │     → Rerank → 补 1篇进结果集
  │
  └─ 6) RRF fusion → Rerank → top-5 → LLM 合成
       → 每个来源标注 confidence (★★★/★★/★) + source_path (direct/topology_expand/cluster_expand)
       → 最高 rerank score < 0.3 → gap_warning 追加
       → Neo4j 不可用 → degraded=true + answer 末尾拼接降级警告
```

**性能硬约束**：
- 二次检索 doc_ids 上限 50（按 updated_at 降序），超过截断 + log warn
- Neo4j driver `max_transaction_retry_time=15`（指数退避）
- health/sync 扫描 timeout 30s，超时返回 partial

**降级行为**：
- Neo4j 完全不可用 → QueryResponse.degraded=true + missing_components=["neo4j"]
- answer 末尾拼接系统警告文本
- ES 降级 → 同 degraded=true + missing_components=["es"]
- Neo4j 部分查询超时 → skip 该步骤，其余正常

**文档删除同步**：
- 删除 ES 文档时同步调用 `delete_document_from_neo4j()`
- 删除 HAS_DOC 边 + 若 Document 节点无其他 HAS_DOC 引用则删除节点
- 定期 cron 跑 health/sync → 输出报告 → 人工确认清理残留

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `POST /api/v1/query` | POST | 改造：ES+图双向富化+集群感知 |
| `GET /api/v1/service/{id}/docs` | GET | 新增：服务关联文档列表 |
| `GET /api/v1/document/{id}/services` | GET | 新增：文档关联服务列表 |
| `GET /api/v1/cluster/{service_id}` | GET | 新增：集群拓扑详情 |
| `GET /api/v1/health` | GET | 改造：新增 sync 字段 |
| `GET /api/v1/health/sync` | GET | 新增：ES ↔ Neo4j 一致性详情 |

## 健康检查

`GET /api/v1/health` 新增 sync 段：

```json
{
  "status": "ok",
  "es": "ok",
  "neo4j": "ok",
  "sync": {
    "status": "ok",
    "orphan_docs": 0,
    "dangling_service_refs": 0,
    "missing_doc_edges": 0,
    "cluster_sync_ok": true,
    "total_es_docs": 42,
    "total_neo4j_docs": 42,
    "partial": false
  }
}
```

`GET /api/v1/health/sync` 返回四项检查详情：

1. **orphan_docs** — Neo4j 有 Document 节点但 ES 查不到对应 doc_id（删除不完全）
2. **dangling_service_refs** — ES 文档 service_ids 指向不存在的 Neo4j Service
3. **missing_doc_edges** — HAS_DOC 边指向不存在的 Document 节点
4. **cluster_issues** — cluster_nodes 引用的 host 在 hosts 列表中不存在

**性能**: health/sync 扫描 timeout 30s，超时返回 partial=true + 已收集的部分结果。

## Metrics

`GET /api/v1/metrics` 新增：

| metric | 说明 |
|--------|------|
| `sync_errors_total` | Neo4j 文档同步失败次数 |
| `orphan_docs_total` | health/sync 发现的孤儿文档数（最近一次扫描） |
| `degraded_queries_total` | Neo4j 不可用时降级查询次数 |
| `entity_extract_fallback_total` | 正则兜底被触发的次数 |
| `llm_tokens_total` | LLM token 总消耗（prompt + completion） |

## 测试策略

| 测试 | 类型 | Happy Path | Failure Path | Edge Case |
|------|------|------------|--------------|-----------|
| test_full_query_pipeline | Integration | query → ES → Neo4j 富化 → source_path 标注 | Neo4j down → degraded=true | 零命中 → gap_warning |
| test_doc_sync_roundtrip | Integration | 写文档 → ES+Neo4j → 删 → 双方无残留 | Neo4j 同步失败 → partial_success | 多 service 文档部分 service 不存在 |
| test_entity_extract | Unit | LLM 返回 entity_dict | LLM 超时 → 正则兜底 → 并集合并 | LLM 空 + 正则空 = {} |
| test_cluster_query | Integration | 集群成员 IP → 展开所有 PART_OF | 非集群 IP → 仅本机拓扑 | 多 cluster 匹配 → warn |
| test_health_sync | Integration | ES+Neo4j 一致 → clean | 一侧不可达 → partial | 大索引超时 → partial+30s |
| test_gap_detection | Unit | rerank 最高分 > 0.3 → 无警告 | 最高分 < 0.3 → gap_warning | 空结果 → gap_warning |

## 改造文件清单

| 文件 | 改动 |
|------|------|
| `app/schema.py` | service_id→service_ids；新增 Cluster 属性白名单；relevance 枚举 |
| `app/models/query.py` | 新增 DocRef, SyncStatus, ClusterInfo 模型 |
| `app/indexer/doc_indexer.py` | 索引后同步 Neo4j Document + HAS_DOC |
| `app/retrievers/graph_retriever.py` | 新增 get_service_docs(), get_doc_services(), get_service_cluster(), sync_document_node(), check_sync_health() |
| `app/retrievers/es_retriever.py` | 新增 get_docs_by_ids() 支持二次检索 |
| `app/api/routes.py` | 改造 /query；新增 /service/{id}/docs, /document/{id}/services, /cluster/{service_id}, /health/sync |
| `app/monitor.py` | 新增 check_sync() |
| `app/router/query_rewriter.py` | 增强 entity extraction (IP, host名, service名, 端口) |
| `scripts/load-topology.py` | 解析 cluster_nodes → Cluster 节点 + PART_OF + BELONGS_TO |
| `aiops-query` CLI | service_id→service_ids；新增 cluster 子命令 |
| `templates/*.md` | frontmatter service_id → service_ids |
| `docs/维护指南.md` | 补充 ES ↔ Neo4j 同步维护说明 |
| `app/monitor.py` | 新增 5 个 metrics（sync_errors_total, orphan_docs_total, degraded_queries_total, entity_extract_fallback_total, llm_tokens_total） |
| `cron/` | 新增 sync-health 定时检查 cron 任务 |

## 决策记录（/plan-ceo-review 审阅结果）

审阅日期: 2026-06-02 | 模式: SCOPE EXPANSION | 5/5 扩展提案接受

### GAP 决议

| # | 发现 | 决议 |
|---|------|------|
| GAP 1 | 空 service_ids 数组行为未定义 | schema 校验拒绝空数组，要求至少 1 个 service_id |
| GAP 2 | ES 写成功 + Neo4j 同步失败无补偿 | console.error + 返回 partial_success 状态 |
| GAP 3 | Neo4j 完全不可用时降级未告知用户 | QueryResponse 新增 `degraded: true` + `missing_components: ["neo4j"]` |
| GAP 4 | Neo4j TransientError 未处理 | driver 配置 `max_transaction_retry_time=15`，利用内置指数退避 |
| GAP 5 | 多 Cluster 匹配数据一致性错误 | load-topology 预防重复 BELONGS_TO + 查询时检测多行 warn |
| GAP 6 | degraded=true 时降级说明文本谁负责 | routes.py 在 answer 末尾拼接降级警告文本，不依赖 LLM |
| GAP 7 | 文档删除未同步 Neo4j | 新增 delete_document_from_neo4j()：删 HAS_DOC 边 + 无其他引用时删 Document 节点 |
| GAP 8 | 新功能无 metrics 暴露 | 新增 5 个 metrics：sync_errors_total, orphan_docs_total, degraded_queries_total, entity_extract_fallback_total, llm_tokens_total |
| GAP 9 | Neo4j Document 节点无 GC | cron 定期跑 check_sync_health → 输出报告 → 人工确认清理 |

### 扩展提案（全接受）

| # | 提案 | Effort |
|---|------|--------|
| 1 | 来源可信度显式评分（★★★/★★/★） | S |
| 2 | 实体提取失败降级策略（正则兜底 IP/host/service 名） | S |
| 3 | 图一致性写时校验（HAS_DOC 前检查 Service 存在） | S |
| 4 | 二次检索可解释性追踪（source_path: direct/topology_expand/cluster_expand） | S |
| 5 | 知识缺口检测（Rerank 最高分 < 0.3 → 缺口提示） | S |

### 安全要求

- 所有新增 Cypher 方法 100% 使用 `$param` 参数化查询，禁用 f-string 拼接
- 同步修复已有两处 f-string：`get_service_downstream` depth, `get_full_path` depth
- `service_ids` 数组值必须前缀 `svc_`（schema 已有校验）

### 部署步骤

1. 先跑 `init-neo4j.py` 创建 Document 约束 + Cluster 索引
2. 验证约束就位
3. 全量 reindex：`index-docs.py --clean` 重建 ES 索引（`service_ids` 数组）
4. load-topology.py 加载 cluster_nodes
5. 重启 API

### 性能硬约束

- 二次检索 doc_ids 上限 50（按 updated_at 降序），超过截断 + log warn
- health/sync 扫描 timeout 30s，超时返回 partial 结果

### 测试策略

| 测试 | 类型 | Happy Path | Failure Path | Edge Case |
|------|------|------------|--------------|-----------|
| test_full_query_pipeline | Integration | query → ES hit → Neo4j 富化 → source_path 标注 | Neo4j 不可用 → degraded=true | 零命中 → gap_warning |
| test_doc_sync_roundtrip | Integration | 写文档 → ES+Neo4j → 删文档 → 双方无残留 | Neo4j 同步失败 → partial_success | 多 service 文档部分 service 不存在 |
| test_entity_extract | Unit | LLM 返回 entity_dict | LLM 超时 → 正则兜底 → 并集合并 | LLM 空 + 正则空 = 空 entities |
| test_cluster_query | Integration | 集群成员 IP → 展开所有 PART_OF | 非集群 IP → 返回本机拓扑 | 多 cluster 匹配 → warn |
| test_health_sync | Integration | ES+Neo4j 一致 → clean | 一侧不可达 → partial | 大索引超时 → partial+30s |
| test_gap_detection | Unit | rerank 最高分 > 0.3 → 无警告 | 最高分 < 0.3 → gap_warning | 空结果 → gap_warning |

### NOT in scope

- Document 节点 GC 自动清理（归 cron 定期检查，不归代码自动）
- LLM 自动从文档抽取实体关系写入 Neo4j（准确性风险大）
- Cypher QA Chain（LangChain GraphCypherQAChain）替代手写 Cypher
- `service_id` 旧字段兼容（全量 reindex 后不留旧数据）
- 实时 sync 事务性写入（ES 不支持两阶段提交）

### 已存在的复用

| 已有组件 | 如何复用 |
|----------|---------|
| `doc_indexer.parse_markdown()` | 已有 frontmatter 解析 + schema 校验，只改 service_ids 验证 |
| `graph_retriever.get_driver()` | 新增方法共用同一 driver，`max_transaction_retry_time` 加启动配置 |
| `es_retriever.search_fulltext()` | get_docs_by_ids() 基于已有 ES query 模式 |
| `reranker.merge_and_rerank()` | 不变，仍做 RRF + Rerank，新增结果多一个 source_path/confidence 字段 |
| `query_rewriter.rewrite_query()` | 已有 LLM 调用模式，extract_entities() 沿用 + 正则兜底 |
| `load-topology.py` YAML 解析 | 已有 services/hosts 解析，cluster_nodes 在同一个 YAML 文件里 |
| `aiops-query _index()` | 不变，仍调用 index-docs.py + load-topology.py |

### Dreams State Delta

```
当前状态                       本次改造后                     12个月理想
ES/Neo4j 单 string 关联  →  双向关联+Cluster+查询感知   →  全自动运维知识图谱
无 cluster 感知             集群感知拓扑展开              实体抽取自动化
检索无防污染                二次检索+RRF+评分透明        多模态（日志/监控/拓扑/文档）
无降级通信                  degraded + 降级警告          自愈系统自动匹配 SOP
```

## Implementation Tasks

- [ ] **T1 (P1, human: ~3h / CC: ~20min)** — schema + mapping — `service_id` → `service_ids` 全量迁移
  - Surfaced by: Eng Review Architecture Issue 1 — schema.py:67, doc_indexer.py:56/70, es_retriever.py:69/100/138
  - Files: `app/schema.py`, `app/indexer/doc_indexer.py`, `app/retrievers/es_retriever.py`
  - Verify: `test_doc_sync_roundtrip` 通过
- [ ] **T2 (P1, human: ~2h / CC: ~15min)** — graph_retriever — 新增 5 个方法 + Cypher $param 化
  - Surfaced by: CEO Review GAP (Cypher injection) + design — get_service_docs/get_doc_services/get_service_cluster/sync_document_node/check_sync_health
  - Files: `app/retrievers/graph_retriever.py`
  - Verify: `test_cluster_query`, `test_health_sync` 通过
- [ ] **T3 (P1, human: ~2h / CC: ~15min)** — doc_indexer — Neo4j 同步 + 写时校验 + 删除同步
  - Surfaced by: CEO Review GAP 2/3/7 — partial_success, write-time validation, delete sync
  - Files: `app/indexer/doc_indexer.py`
  - Verify: `test_doc_sync_roundtrip` 写入+删除 通过
- [ ] **T4 (P1, human: ~2h / CC: ~15min)** — routes.py — /query 改造 + 新 API + degraded
  - Surfaced by: design + CEO Review GAP 3/6 — 双向富化, degraded=true, answer 降级警告
  - Files: `app/api/routes.py`, `app/models/query.py`
  - Verify: `test_full_query_pipeline`, `test_gap_detection` 通过
- [ ] **T5 (P1, human: ~1.5h / CC: ~10min)** — query_rewriter — 合并 rewrite+entity_extract + 正则兜底
  - Surfaced by: Eng Review Code Quality Issue 1 — 双 LLM call 合并为单 call
  - Files: `app/router/query_rewriter.py`
  - Verify: `test_entity_extract` 通过
- [ ] **T6 (P1, human: ~1h / CC: ~8min)** — load-topology — cluster_nodes 解析
  - Surfaced by: design — YAML cluster_nodes 未被加载
  - Files: `scripts/load-topology.py`
  - Verify: `test_cluster_query` 通过
- [ ] **T7 (P2, human: ~30min / CC: ~5min)** — es_retriever — get_docs_by_ids()
  - Surfaced by: design — 二次检索 doc_ids filter
  - Files: `app/retrievers/es_retriever.py`
  - Verify: `test_full_query_pipeline` 通过
- [ ] **T8 (P2, human: ~45min / CC: ~5min)** — monitor — 5 metrics + check_sync()
  - Surfaced by: CEO Review GAP 8 — sync_errors/degraded/orphan/fallback/token metrics
  - Files: `app/monitor.py`
  - Verify: `GET /api/v1/metrics` 含新字段
- [ ] **T9 (P2, human: ~30min / CC: ~5min)** — templates + CLI — service_id→service_ids 适配
  - Surfaced by: design — frontmatter 模板 + aiops-query 参数
  - Files: `templates/*.md`, `aiops-query`
  - Verify: `write-sop` 后检查 .md 文件 frontmatter
- [ ] **T10 (P2, human: ~1.5h / CC: ~12min)** — 测试基础设施 — pytest + 6 核心测试 + 并发 + eval
  - Surfaced by: Eng Review Section 3 — 零测试框架, 34 gaps
  - Files: `tests/conftest.py`, `tests/test_query.py`, `tests/test_sync.py`, `tests/test_cluster.py`, `tests/test_health.py`, `tests/test_rewriter.py`, `tests/test_eval.py`
  - Verify: `pytest -v` 全通过, 并发 `pytest -n 4` 无错误
- [ ] **T11 (P3, human: ~30min / CC: ~5min)** — cron — sync-health 定时检查
  - Surfaced by: CEO Review GAP 9 — Document GC via cron
  - Files: `cron/sync-health-check.sh`
  - Verify: crontab 注册 + 手动跑一次输出报告

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | CLEAN | 9 gaps fixed, 5/5 expansions accepted |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAN | 4 issues found: A1(service_ids迁移), A2(懒加载TTL), CQ1(合并LLM call), T1(pytest+eval+concurrent) |
| Codex Review | — | Independent 2nd opinion | 0 | — | — |
| Design Review | — | UI/UX gaps | 0 | — | — |
| DX Review | — | Developer experience gaps | 0 | — | — |

- **UNRESOLVED:** 0
- **VERDICT:** CEO + ENG CLEARED — ready to implement
