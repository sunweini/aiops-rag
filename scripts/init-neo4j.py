#!/usr/bin/env python3
"""Initialize Neo4j schema constraints."""

import sys
sys.path.insert(0, "/app")

from app.retrievers.graph_retriever import get_driver, init_schema


def main():
    driver = get_driver()
    init_schema(driver)
    driver.close()
    print("Neo4j schema initialized")


if __name__ == "__main__":
    main()
