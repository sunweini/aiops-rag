# 增量索引 + 单文件索引 + 目录索引 设计

日期: 2026-06-03 | 状态: 已确认

## 背景

`index-docs.py` 当前是全量重建：删 ES 索引 → 从头建。`aiops-query index` 每次跑 `docker exec load-topology` + `docker exec index-docs.py`，文档多时不可接受。

文档源：`~/workspace-shared/rag-wiki/`（新建独立目录）
快照：`~/workspace-shared/rag-wiki/.index_snapshot.json`

## 设计目标

1. **增量索引（默认）** — 扫描 rag-wiki/，比对 mtime 快照，只重索引变更的 .md 文件
2. **单文件索引** — `aiops-query index-file <path>` 只重建指定文件的 ES chunks + Neo4j 同步
3. **Neo4j 同步自动保持** — 增量/单文件模式均触发 Document 节点 + HAS_DOC 边同步
4. **本地文件保留** — rag-wiki/ 是数据真相源，ES + Neo4j 是检索引擎副本

## 目录结构

```
~/.openclaw/workspace-shared/rag-wiki/
├── .index_snapshot.json          ← 增量快照
├── topology/
│   └── call-graph.yml
├── services/
│   └── {svc_id}-{name}/
│       ├── tech-arch.md
│       └── sop-*.md
├── incidents/
│   └── {YYYY-MM-DD}-{name}.md
└── hosts/
    └── {host_id}.md
```

## 增量索引流程

```
扫描 rag-wiki/ 下所有 *.md
        │
        ├─ 文件在 snapshot 中且 mtime 未变 → 跳过
        ├─ 文件在 snapshot 中且 mtime 变更 → 重新索引（先删旧 chunks 再生产）
        ├─ 文件不在 snapshot 中（新文件）   → 全量索引
        └─ snapshot 中有但文件不存在        → 删除 ES chunks + Neo4j Document 节点

更新 .index_snapshot.json
```

快照格式：

```json
{
  "services/svc_nginx_company-company-nginx-cluster/tech-arch.md": 1719000000,
  "services/svc_nginx_company-company-nginx-cluster/sop-磁盘满-清理日志.md": 1719003600,
  "topology/call-graph.yml": 1719000000
}
```

key = 相对 rag-wiki/ 的文件路径，value = mtime 秒级时间戳。

### 单文件索引

```
aiops-query index-file services/svc_nginx_company-company-nginx-cluster/tech-arch.md
```

流程：
1. 读 rag-wiki/ 下该文件
2. ES：按 `doc_id` 前缀删除该文件的所有旧 chunk → parse → index_chunk
3. Neo4j：sync_document_node（MERGE Document + HAS_DOC 边）
4. 更新 snapshot 中该文件 mtime

不支持 --dry-run（目前），后续可选。

### 首次运行

snapshot 不存在 → 全量索引 rag-wiki/ 所有 .md → 生成 snapshot。

### 强制全量

`aiops-query index --full`：删 ES 索引 + 清空 snapshot + 全量重建。

## 删除检测

增量扫描时：snapshot key 对应的文件在磁盘上不存在 → 按 `doc_id` 前缀删除 ES chunks + 调 `delete_document_node` 清 Neo4j。

ES 删除逻辑：
```python
# doc_id 从快照 key 推导
service_id = key.split('/')[1].split('-')[0]  # 第一个 - 前是 svc_id
filename_stem = Path(key).stem
doc_id = f"{service_id}_{filename_stem}"

es.delete_by_query(index=INDEX_NAME, body={"query": {"prefix": {"doc_id": doc_id}}})
```

Neo4j 删除：`delete_document_node(driver, doc_id)`（已有函数）

## Neo4j 同步

增量/单文件模式均调用已有 `sync_document_node()`：
- MERGE Document 节点
- 对每个 service_ids 检查 Service 存在性 → MERGE HAS_DOC 边
- 对每个 host_ids 检查 Host 存在性 → MERGE HAS_DOC 边

写入时校验（已有逻辑，保持不变）。

## 涉及文件

| 文件 | 改动 |
|------|------|
| `scripts/index-docs.py` | +snapshot 读/写 + mtime diff + 单文件模式 + 删除检测 + --full |
| `app/indexer/doc_indexer.py` | +`index_single_file()`, +`delete_doc_by_id()` |
| `docker-compose.yml` | volume mount `./examples/aiops-docs` → `~/workspace-shared/rag-wiki` |
| `aiops-query` CLI | `DOCS_DIR` → rag-wiki, `_index()` 改为增量, +`index-file` 命令 |
| `SKILL.md` | 更新命令表 + 路径说明 |
| `docs/维护指南.md` | 更新目录路径 |

## 错误处理

| 场景 | 处理 |
|------|------|
| snapshot 文件不存在 | 全量索引 + 生成 snapshot |
| snapshot JSON 损坏 | warn + 全量重建 |
| snapshot 写入失败（权限/磁盘满） | crash early + exit 1，不静默跳过 |
| `index-file` 路径不在 rag-wiki/ 下 | exit 1 + "路径必须在 rag-wiki/ 内"（防路径遍历） |
| `index-file` 路径不存在 | exit 1 + 错误消息 |
| `index-file` 非 .md | exit 1 + 不支持的类型 |
| `index-file` 无 frontmatter | skip + warn（parse_markdown 返回 []） |
| ES 不可达 | error + 不更新 snapshot（下次重试） |
| Neo4j 不可达 | warn + ES 仍写入（partial_success） |
| 并发跑两个 index | 不做文件锁（加注释：不可并发） |

## NOT in scope

- 并发索引锁
- `index-file --dry-run`
- 目录监控/自动触发（inotify）
- hash 对比（mtime 够用，hash 成本高）
