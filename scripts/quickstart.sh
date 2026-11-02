#!/usr/bin/env bash
# Run Neo4j and the MCP server locally with Docker, then print how to connect Claude.
#
# First run writes .env with a random Neo4j password and MCP token (never printed).
# Later runs reuse it. Data lives in the `neo4j-data` volume and survives restarts;
# `docker compose down -v` deletes it.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null || { echo "Docker is required: https://docs.docker.com/get-docker/" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start it and retry." >&2; exit 1; }

secret() { openssl rand -hex 24; }

if [ ! -f .env ]; then
  (umask 077; sed -e "s/^NEO4J_PASSWORD=.*/NEO4J_PASSWORD=$(secret)/" \
                  -e "s/^MCP_AUTH_TOKEN=.*/MCP_AUTH_TOKEN=$(secret)/" .env.example > .env)
  echo "Created .env with a random Neo4j password and MCP token."
elif ! grep -q '^MCP_AUTH_TOKEN=' .env; then
  printf '\nMCP_AUTH_TOKEN=%s\n' "$(secret)" >> .env
  echo "Added a random MCP_AUTH_TOKEN to the existing .env."
fi

echo "Building the server and starting Neo4j (first run takes a minute or two)..."
docker compose up -d --build --wait

cat <<'MSG'

Ready: http://localhost:8000/mcp

Connect Claude Code (run in this directory; the token is read from .env, not shown):

  claude mcp add --transport http personal-kg http://localhost:8000/mcp \
    --header "Authorization: Bearer $(sed -n 's/^MCP_AUTH_TOKEN=//p' .env)"

Then start `claude` and try: "Remember that I'm learning Rust for a CLI side project."
Other clients: see "Connect other clients" in README.md.

Neo4j Browser: http://localhost:7474 (user neo4j, password in .env)
Stop: docker compose down     Delete all data: docker compose down -v
MSG
