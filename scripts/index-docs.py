#!/usr/bin/env python3
"""Incremental indexer for wiki.

Default: scan wiki/, mtime diff, only re-index changed .md files.
--full:  delete ES index + rebuild all + regenerate snapshot.
--file <relpath>: single-file index.

Snapshot: wiki/.index_snapshot.json — {relpath: mtime}
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.indexer.doc_indexer import index_directory, index_single_file
from app.retrievers.es_retriever import get_es_client, INDEX_NAME

WIKI_DIR = "/app/wiki"
SNAPSHOT_FILE = os.path.join(WIKI_DIR, ".index_snapshot.json")


def _load_snapshot() -> dict:
    if not os.path.exists(SNAPSHOT_FILE):
        return {}
    try:
        with open(SNAPSHOT_FILE) as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not a dict")
            return data
    except Exception as e:
        print(f"Snapshot corrupted ({e}), starting fresh")
        return {}


def _save_snapshot(data: dict):
    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Snapshot saved: {len(data)} files")
    except Exception as e:
        print(f"FATAL: cannot write snapshot: {e}")
        sys.exit(1)


def _collect_files(dir_path: str) -> dict:
    """Walk dir_path, return {relpath: mtime} for all *.md files."""
    files = {}
    for fp in Path(dir_path).rglob("*.md"):
        rel = str(fp.relative_to(dir_path))
        files[rel] = int(fp.stat().st_mtime)
    return files


def _path_to_doc_id(rel_path: str) -> str:
    """Derive doc_id from relative path.
    e.g. 'services/svc_nginx_tech-arch.md' -> 'svc_nginx_tech-arch'"""
    stem = Path(rel_path).stem
    parts = rel_path.split("/")
    svc_id = "unknown"
    for p in parts:
        if "-" in p:
            svc_id = p.split("-", 1)[0]
            break
    return f"{svc_id}_{stem}"


def _delete_doc_es(es, doc_id: str) -> int:
    """Delete ES chunks by doc_id prefix + Neo4j Document if orphaned."""
    try:
        resp = es.delete_by_query(
            index=INDEX_NAME,
            body={"query": {"prefix": {"doc_id": doc_id}}},
            refresh=True,
        )
        deleted = resp.get("deleted", 0)
        if deleted:
            print(f"  ES deleted {deleted} chunks for {doc_id}")
        return deleted
    except Exception as e:
        print(f"  ES delete error [{doc_id}]: {e}")
        return 0


def _delete_doc_neo4j(doc_id: str):
    try:
        from app.retrievers.graph_retriever import get_driver, delete_document_node
        driver = get_driver()
        result = delete_document_node(driver, doc_id)
        print(f"  Neo4j: {result['detail']}")
    except Exception as e:
        print(f"  Neo4j delete error [{doc_id}]: {e}")


async def _do_incremental(dir_path: str):
    """Mtime-based incremental index + delete removed files."""
    es = get_es_client()
    old_snap = _load_snapshot()
    current = _collect_files(dir_path)

    changed = 0
    skipped = 0
    deleted = 0
    new_snap = {}

    # Delete removed files
    for rel_path in list(old_snap):
        if rel_path not in current:
            doc_id = _path_to_doc_id(rel_path)
            _delete_doc_es(es, doc_id)
            _delete_doc_neo4j(doc_id)
            deleted += 1

    # Index changed + new files
    for rel_path, mtime in current.items():
        old_mtime = old_snap.get(rel_path, 0)
        if mtime == old_mtime:
            skipped += 1
            new_snap[rel_path] = mtime
            continue

        full_path = os.path.join(dir_path, rel_path)
        print(f"Indexing [{rel_path}]")
        s, f = await index_single_file(es, full_path)
        if s > 0:
            changed += s
            new_snap[rel_path] = mtime
        else:
            print(f"  Failed to index: {rel_path}")

    _save_snapshot(new_snap)
    print(f"Done: {changed} chunks indexed, {skipped} skipped, {deleted} deleted")


async def main():
    parser = argparse.ArgumentParser(description="Incremental index wiki")
    parser.add_argument("--full", action="store_true", help="Full rebuild")
    parser.add_argument("--file", type=str, help="Single file relative to wiki root")
    args = parser.parse_args()

    if args.full:
        es = get_es_client()
        es.indices.delete(index=INDEX_NAME, ignore_unavailable=True)
        from app.retrievers.es_retriever import init_index
        init_index(es)
        if os.path.exists(SNAPSHOT_FILE):
            os.remove(SNAPSHOT_FILE)
        print("Full rebuild...")
        s, f = await index_directory(es, WIKI_DIR, clean=False)
        current = _collect_files(WIKI_DIR)
        _save_snapshot(current)
        print(f"Full done: {s} success, {f} failed")
    elif args.file:
        rel = args.file
        full = os.path.normpath(os.path.join(WIKI_DIR, rel))
        wiki_root = os.path.normpath(WIKI_DIR)
        if not full.startswith(wiki_root):
            print(f"错误: 路径必须在 wiki/ 内: {rel}")
            sys.exit(1)
        if not rel.endswith(".md"):
            print(f"错误: 只支持 .md 文件: {rel}")
            sys.exit(1)
        if not os.path.exists(full):
            print(f"错误: 文件不存在: {full}")
            sys.exit(1)
        es = get_es_client()
        print(f"Single file index: {rel}")
        s, f = await index_single_file(es, full)
        print(f"Indexed: {s} success, {f} failed")
        if s > 0:
            snap = _load_snapshot()
            snap[rel] = int(os.path.getmtime(full))
            _save_snapshot(snap)
        if f:
            sys.exit(1)
    else:
        await _do_incremental(WIKI_DIR)


if __name__ == "__main__":
    asyncio.run(main())
