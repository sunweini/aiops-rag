#!/bin/bash
# sync-health-check.sh — daily ES ↔ Neo4j consistency audit
API="http://localhost:8001/api/v1"
REPORT_DIR="/root/.openclaw/workspace-shared/rag/reports"
mkdir -p "$REPORT_DIR"

TODAY=$(date +%Y-%m-%d)
REPORT="$REPORT_DIR/sync-health-$TODAY.json"

curl -s "$API/health/sync" | python3 -m json.tool > "$REPORT" 2>/dev/null

if [ $? -ne 0 ]; then
    echo "[$(date)] sync-health check failed — API unreachable" >> "$REPORT_DIR/sync-health-errors.log"
    exit 1
fi

ORPHAN_COUNT=$(python3 -c "import json; d=json.load(open('$REPORT')); print(len(d.get('orphan_docs',[])))")
DANGLING_COUNT=$(python3 -c "import json; d=json.load(open('$REPORT')); print(len(d.get('dangling_doc_refs',[])))")

if [ "$ORPHAN_COUNT" -gt 0 ] || [ "$DANGLING_COUNT" -gt 0 ]; then
    echo "[$(date)] Sync issues: $ORPHAN_COUNT orphan docs, $DANGLING_COUNT dangling refs" >> "$REPORT_DIR/sync-health-alerts.log"
    echo "Report: $REPORT" >> "$REPORT_DIR/sync-health-alerts.log"
fi

echo "[$(date)] sync-health: $ORPHAN_COUNT orphans, $DANGLING_COUNT dangling" >> "$REPORT_DIR/sync-health-summary.log"
