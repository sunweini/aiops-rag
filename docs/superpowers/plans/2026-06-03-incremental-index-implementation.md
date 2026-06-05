# 增量索引 + 单文件索引 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** `aiops-query index` 增量模式(mtime diff) + `aiops-query index-file <path>` 单文件; 文档迁至 `rag-wiki/`

**Tech Stack:** Python 3, ES 8.x, Neo4j 5.x, docker compose

---

### Task 1: 创建 rag-wiki + 迁移文档 + docker volume 更新

- [ ] **Step 1: Create rag-wiki/ + copy docs**

```bash
mkdir -p ~/.openclaw/workspace-shared/rag-wiki/{services,incidents,topology,hosts}
cp -r /root/.openclaw/workspace-shared/rag/examples/aiops-docs/* ~/.openclaw/workspace-shared/rag-wiki/
ls -la ~/.openclaw/workspace-shared/rag-wiki/
```

Expected: `services/ incidents/ topology/ hosts/` 子目录存在

- [ ] **Step 2: docker-compose.yml 更新 volume**

Modify `docker-compose.yml`:

```yaml
# Before:
      - ./examples/aiops-docs:/app/examples/aiops-docs

# After:
      - ~/.openclaw/workspace-shared/rag-wiki:/app/wiki
```

- [ ] **Step 3: Rebuild + verify mount**

```bash
docker compose build api-server && docker compose up -d api-server
sleep 3
docker exec rag-api ls /app/wiki/services/
```

Expected: service directories visible

---

### Task 2: scripts/index-docs.py 增量逻辑

- [ ] **Step 1: Rewrite scripts/index-docs.py**

```python
#!/usr/bin/env python3
"""Incremental indexer for rag-wiki. Default: mtime diff. --full: full rebuild."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.indexer.doc_indexer import index_directory, index_single_file
from app.retrievers.es_retriever import get_es_client

WIKI_DIR = "/app/wiki"
SNAPSHOT_FILE = os.path.join(WIKI_DIR, ".index_snapshot.json")


def load_snapshot() -> dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE) as f:
            snap = json.load(f)
            if not isinstance(snap, dict):
                raise ValueError("Not a dict")
            return snap
    except Exception as e:
        print(f"Snapshot corrupted ({e}), starting fresh")
        return {}


def save_snapshot(snap: dict):
    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(snap, f, indent=2)
        print(f"Snapshot saved: {len(snap)} entries")
    except Exception as e:
        print(f"FATAL: Cannot write snapshot: {e}")
        sys.exit(1)


def collect_files(dir_path: str) -> dict:
    """Walk dir_path, return {rel_path: mtime} for all *.md files."""
    files = {}
    for fp in Path(dir_path).rglob("*.md"):
        rel = str(fp.relative_to(dir_path))
        files[rel] = int(fp.stat().st_mtime)
    return files


def delete_doc_es(es, doc_id: str):
    """Delete all ES chunks for a doc by doc_id prefix."""
    try:
        resp = es.delete_by_query(
            index="knowledge_base",
            body={"query": {"prefix": {"doc_id": doc_id}}},
            refresh=True,
        )
        return resp.get("deleted", 0)
    except Exception as e:
        print(f"ES delete error for {doc_id}: {e}")
        return 0


def delete_doc_neo4j(doc_id: str):
    """Delete Neo4j Document node via delete_document_node."""
    try:
        from app.retrievers.graph_retriever import get_driver, delete_document_node
        driver = get_driver()
        result = delete_document_node(driver, doc_id)
        if result["status"] == "error":
            print(f"Neo4j delete error [{doc_id}]: {result['detail']}")
    except Exception as e:
        print(f"Neo4j delete driver error [{doc_id}]: {e}")


async def incremental_index(dir_path: str):
    """Mtime-based incremental index + delete removed files."""
    es = get_es_client()
    old_snap = load_snapshot()
    current_files = collect_files(dir_path)

    total_changed = 0
    total_skipped = 0
    total_deleted = 0

    # Deleted files: in snapshot but not on disk
    for rel_path in list(old_snap):
        if rel_path not in current_files:
            stem = Path(rel_path).stem
            parts = rel_path.split("/")
            svc_id = ""
            for p in parts:
                if p.startswith("svc_"):
                    svc_id = p.split("-", 1)[0]
                    break
            doc_id = f"{svc_id}_{stem}" if svc_id else stem
            deleted_es = delete_doc_es(es, doc_id)
            delete_doc_neo4j(doc_id)
            total_deleted += 1
            print(f"Deleted: {rel_path} (ES:{deleted_es} chunks + Neo4j)")
            del old_snap[rel_path]

    # Changed/new files
    for rel_path, mtime in current_files.items():
        old_mtime = old_snap.get(rel_path, 0)
        if mtime == old_mtime:
            total_skipped += 1
            continue

        full_path = os.path.join(dir_path, rel_path)
        print(f"Indexing: {rel_path}")
        s, f = await index_single_file(es, full_path)
        if s > 0:
            total_changed += s
            old_snap[rel_path] = mtime
        else:
            print(f"  Index failed: {rel_path}")

    save_snapshot(old_snap)
    print(f"Done: {total_changed} chunks indexed, {total_skipped} skipped, {total_deleted} deleted")


async def main():
    parser = argparse.ArgumentParser(description="Incremental index rag-wiki")
    parser.add_argument("--full", action="store_true", help="Full rebuild")
    parser.add_argument("--file", type=str, help="Single file (relative to wiki root)")
    args = parser.parse_args()

    if args.full:
        es = get_es_client()
        es.indices.delete(index="knowledge_base", ignore_unavailable=True)
        from app.retrievers.es_retriever import init_index
        init_index(es)
        if os.path.exists(SNAPSHOT_FILE):
            os.remove(SNAPSHOT_FILE)
        print("Full rebuild...")
        s, f = await index_directory(es, WIKI_DIR, clean=False)
        # Save fresh snapshot
        current_files = collect_files(WIKI_DIR)
        save_snapshot(current_files)
        print(f"Full done: {s} success, {f} failed")
    elif args.file:
        # Validate path is inside WIKI_DIR
        full_path = os.path.normpath(os.path.join(WIKI_DIR, args.file))
        if not full_path.startswith(os.path.normpath(WIKI_DIR)):
            print("错误: 路径必须在 rag-wiki/ 内")
            sys.exit(1)
        if not args.file.endswith(".md"):
            print("错误: 只支持 .md 文件")
            sys.exit(1)
        es = get_es_client()
        print(f"Single file index: {args.file}")
        s, f = await index_single_file(es, full_path)
        print(f"Indexed: {s} success, {f} failed")
        if s > 0:
            snap = load_snapshot()
            snap[args.file] = int(os.path.getmtime(full_path))
            save_snapshot(snap)
        if f > 0:
            sys.exit(1)
    else:
        await incremental_index(WIKI_DIR)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Compile check**

```bash
docker exec rag-api python3 -c "import py_compile; py_compile.compile('/app/scripts/index-docs.py', doraise=True); print('OK')"
```

---

### Task 3: app/indexer/doc_indexer.py 加 index_single_file

- [ ] **Step 1: Add index_single_file()**

```python
async def index_single_file(es: Elasticsearch, filepath: str) -> tuple[int, int]:
    """Index a single .md file. Deletes old chunks first, then re-indexes.
    Returns (success, failed)."""
    chunks = parse_markdown(filepath)
    if not chunks:
        return 0, 1

    # Delete old chunks by doc_id
    doc_id = chunks[0].get("doc_id", "")
    if doc_id:
        try:
            es.delete_by_query(
                index=INDEX_NAME,
                body={"query": {"prefix": {"doc_id": doc_id}}},
                refresh=True,
            )
        except Exception as e:
            print(f"Delete old chunks error [{doc_id}]: {e}")

    success = 0
    failed = 0
    for chunk in chunks:
        es_id = await index_chunk(es, chunk)
        if es_id:
            success += 1
        else:
            failed += 1

    # Sync Neo4j
    if success > 0:
        try:
            from app.retrievers.graph_retriever import get_driver, sync_document_node
            driver = get_driver()
            result = sync_document_node(driver, chunks[0])
            if result["status"] == "error":
                print(f"Neo4j sync error: {result['detail']}")
        except Exception as e:
            print(f"Neo4j sync driver error: {e}")

    return success, failed
```

- [ ] **Step 2: Compile check**

```bash
docker exec rag-api python3 -c "import py_compile; py_compile.compile('/app/app/indexer/doc_indexer.py', doraise=True); print('OK')"
```

---

### Task 4: aiops-query CLI 更新

- [ ] **Step 1: Update DOCS_DIR + TOPO_FILE paths**

```python
# Before:
DOCS_DIR = os.path.join(RAG_ROOT, "examples", "aiops-docs")

# After:
WIKI_DIR = os.path.expanduser("~/.openclaw/workspace-shared/rag-wiki")
DOCS_DIR = WIKI_DIR
```

- [ ] **Step 2: Update _index() 为增量 + 加 --full**

```python
def _index(full=False):
    if full:
        os.system(f"docker exec rag-api python3 /app/scripts/index-docs.py --full")
    else:
        os.system(f"docker exec rag-api python3 /app/scripts/index-docs.py")
```

- [ ] **Step 3: Add index-file command + CLI routing**

Add function:
```python
def index_file(filepath):
    os.system(f"docker exec rag-api python3 /app/scripts/index-docs.py --file {filepath}")
```

Add elif branch in main:
```python
elif cmd == "index-file":
    index_file(sys.argv[2] if len(sys.argv) > 2 else "")
```

Add help text under QUERY section.

- [ ] **Step 4: Verify CLI**

```bash
cd ~/.openclaw/skills/aiops-rag && chmod +x aiops-query
./aiops-query index-file --help 2>&1 | head -5
```

---

### Task 5: SKILL.md + 维护指南 + templates 路径更新

- [ ] **Step 1: SKILL.md 加 index-file 命令表**

Add row to 维护命令 table:
```
| `aiops-query index-file <path>` | 单文件索引 | `aiops-query index-file services/svc_nginx/tech-arch.md` |
```

- [ ] **Step 2: 维护指南更新路径**

Replace all `examples/aiops-docs` → `rag-wiki`.
Replace all `index-docs.py /app/examples/aiops-docs/` → `index-docs.py` (now defaults to `/app/wiki`).

---

### Task 6: 部署验证

- [ ] **Step 1: Rebuild + 首次全量索引**

```bash
docker compose build api-server && docker compose up -d api-server
sleep 5
# First run creates snapshot
docker exec rag-api python3 /app/scripts/index-docs.py
curl -s http://localhost:8001/api/v1/health | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], '| sync:', d['sync']['status'])"
```

- [ ] **Step 2: 增量索引验证**

```bash
# Touch a file to change mtime
touch ~/.openclaw/workspace-shared/rag-wiki/services/svc_nginx_company-company-nginx-cluster/tech-arch.md
docker exec rag-api python3 /app/scripts/index-docs.py
# Should show: "1 changed, N skipped"
```

- [ ] **Step 3: 单文件索引验证**

```bash
cd ~/.openclaw/skills/aiops-rag
./aiops-query index-file services/svc_nginx_company-company-nginx-cluster/tech-arch.md
```

- [ ] **Step 4: 全量模式验证**

```bash
docker exec rag-api python3 /app/scripts/index-docs.py --full
```
