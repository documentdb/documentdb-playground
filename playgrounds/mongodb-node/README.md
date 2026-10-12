# MongoDB Node.js Driver with DocumentDB (local)

This playground uses the official [MongoDB Node.js driver](https://www.mongodb.com/docs/drivers/node/current/)
against a local [DocumentDB](https://github.com/documentdb/documentdb) instance.
It includes:

- an **Express REST API** using native collections (`app/server.js`), and
- a standalone **CRUD/compatibility suite** (`app/mongodb-crud-test.js`) that
  exercises connection, indexes, inserts, queries, updates, aggregation,
  unique-index enforcement, vector search, deletion, and cleanup.

The Node.js driver is the low-level MongoDB client used directly here, without
an ODM such as Mongoose. DocumentDB runs in Docker while the app and tests run
as local Node.js processes.

## Prerequisites

- **Docker** (Docker Desktop or a Docker daemon)
- **Node.js 18+** and **npm**

## Quick Start

From `playgrounds/mongodb-node/`, choose either independent operation:

```bash
# Start DocumentDB and run the compatibility suite.
./scripts/run-test.sh

# Or start DocumentDB and serve the API on port 3000.
./scripts/run-app.sh
```

Both commands reuse the same `documentdb-local` container if it is running.
The API uses the `mongodb_node_demo` database and the suite uses
`mongodb_node_test`, so they can run at the same time.

Stop and remove DocumentDB when finished:

```bash
./scripts/stop-documentdb.sh
```

Set `KEEP_DB=0` when running the suite to remove the container automatically:

```bash
KEEP_DB=0 ./scripts/run-test.sh
```

## Trying the API

With `./scripts/run-app.sh` running:

```bash
curl -s http://localhost:3000/health

curl -s -X POST http://localhost:3000/books \
  -H 'Content-Type: application/json' \
  -d '{"title":"Dune","author":"Herbert","genres":["sci-fi"],"pages":412,"rating":5}'

curl -s http://localhost:3000/books | jq .
curl -s 'http://localhost:3000/books?author=Herbert' | jq .
curl -s http://localhost:3000/stats/genres | jq .
```

The API also provides `GET`, `PATCH`, and `DELETE /books/:id`.

## Connecting the Native Driver

DocumentDB's local gateway uses TLS, a self-signed certificate, and standalone
topology. The client therefore uses these options from `app/db.js`:

```js
const client = new MongoClient(uri, {
  directConnection: true,
  tls: true,
  tlsAllowInvalidCertificates: true,
  serverSelectionTimeoutMS: 10000,
});
```

The playground removes `replicaSet` from supplied URIs because it conflicts
with `directConnection` against the standalone gateway. For production, set
`TLS_INSECURE=false` and configure a trusted CA rather than accepting an
unverified certificate.

## Scripts

| Script | Purpose |
| --- | --- |
| `scripts/run-test.sh` | Start DocumentDB, install dependencies, and run the compatibility suite. |
| `scripts/run-app.sh` | Start DocumentDB, install dependencies, and run the Express API. |
| `scripts/start-documentdb.sh` | Start the local container and wait for readiness. |
| `scripts/stop-documentdb.sh` | Stop and remove the local container. |
| `scripts/lib.sh` | Shared lifecycle and connection-string helpers. |

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `DOCUMENTDB_IMAGE` | `ghcr.io/documentdb/documentdb/documentdb-local:latest` | Local DocumentDB image. |
| `DOCUMENTDB_CONTAINER` | `documentdb-local` | Container name. |
| `DOCUMENTDB_PORT` | `10260` | Loopback host port. |
| `DOCUMENTDB_USERNAME` | `docdbadmin` | Gateway username. |
| `DOCUMENTDB_PASSWORD` | `Documentdb!Local1` | Development password. |
| `MONGO_URI` | Local URI using the defaults above | Driver connection string. |
| `MONGO_DB` | `mongodb_node_demo` or `mongodb_node_test` | App or test database. |
| `TLS_INSECURE` | `true` | Accept the local self-signed certificate. |
| `SERVER_SELECTION_TIMEOUT_MS` | `10000` | App server-selection timeout. |
| `PORT` | `3000` | REST API port. |
| `RATE_LIMIT_WINDOW_MS` | `60000` | Time window for rate limiting data routes. |
| `RATE_LIMIT_MAX` | `100` | Maximum requests per client within the rate-limit window. |
| `KEEP_DB` | `1` | Set to `0` to remove DocumentDB after tests. |

## Running the Suite Manually

```bash
cd app
npm install
MONGO_URI='mongodb://docdbadmin:Documentdb!Local1@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true' \
  npm run test:crud
```

Expected summary:

```text
MongoDB Node.js driver DocumentDB compatibility test
====================================================
  ✅ connect
  ✅ create indexes
  ✅ insertOne
  ...
  ✅ $vectorSearch returns nearest neighbor
  ✅ cleanup (drop collection)
====================================================
Passed: 16  Failed: 0
```

## Compatibility Notes

| Native driver feature | Status | Notes |
| --- | --- | --- |
| CRUD and `_id` lookups | Supported | Uses `insertOne`, `findOne`, `updateOne`, and `deleteOne`. |
| Compound and unique indexes | Supported | Index collation is not supported by DocumentDB. |
| Aggregation | Common stages supported | The suite covers `$unwind`, `$group`, and `$sort`. |
| Vector search | Supported | Uses a `cosmosSearch` vector index and `$vectorSearch`. |
| Transactions/change streams | Not covered | The local gateway advertises standalone topology. |

## Directory Layout

```text
mongodb-node/
├── README.md
├── app/
│   ├── package.json
│   ├── db.js
│   ├── server.js
│   └── mongodb-crud-test.js
└── scripts/
    ├── lib.sh
    ├── start-documentdb.sh
    ├── stop-documentdb.sh
    ├── run-app.sh
    └── run-test.sh
```

## Troubleshooting

- If Docker is unreachable, start Docker Desktop or the Docker daemon.
- For selection timeouts, check `docker logs documentdb-local` and verify port
  `10260` is reachable on `localhost`.
- If credentials change, remove the old container before restarting it because
  credentials are set when the container is created.
- Omit index `collation`; DocumentDB does not implement it.