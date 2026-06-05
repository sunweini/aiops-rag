#!/usr/bin/env python3
"""Initialize ES index with IK+BM25 config."""

import sys
sys.path.insert(0, "/app")

from app.retrievers.es_retriever import get_es_client, init_index


def main():
    es = get_es_client()
    if not es.ping():
        print("ES not reachable")
        sys.exit(1)
    init_index(es)
    print("ES index initialized")


if __name__ == "__main__":
    main()
