#!/usr/bin/env bash
# Look at the cluster graph in Neo4j Browser without exposing the database.
#
# Port-forwards Bolt from neo4j-0 to localhost and copies the Neo4j password to the
# clipboard (never printed). Neo4j Browser (https://browser.neo4j.io) runs in your
# browser and talks to localhost over Bolt WebSocket, so nothing leaves this Mac
# except through the existing kubectl tunnel. Ctrl+C closes the tunnel.
set -euo pipefail

port=${1:-7687}

kubectl get secret personal-kg-secrets -n personal-kg \
  -o jsonpath='{.data.NEO4J_PASSWORD}' | base64 -d | pbcopy

cat <<EOF
Neo4j password copied to the clipboard.

1. Open https://browser.neo4j.io
2. Connect URL: bolt://localhost:${port}  (not neo4j://: routing would hand back the pod address)   user: neo4j   password: paste (⌘V)
3. Whole graph in one view (current facts + notes, no history):
     MATCH (n) WHERE NOT n:Revision AND n.deletedAt IS NULL
     OPTIONAL MATCH (n)-[r]-(m) WHERE NOT m:Revision AND m.deletedAt IS NULL AND r.validTo IS NULL
     RETURN n, r, m
   Everything, history included:  MATCH (n) OPTIONAL MATCH (n)-[r]-(m) RETURN n, r, m
   (Clicking a label in the sidebar shows only that label's nodes, without relationships.)

Read only, please: writes here bypass the MCP server's validation and history.
Tunnel is open until Ctrl+C.
EOF

exec kubectl port-forward -n personal-kg pod/neo4j-0 "${port}:7687"
