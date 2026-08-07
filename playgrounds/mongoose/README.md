# Mongoose with DocumentDB (local)

This playground shows how to use [Mongoose](https://mongoosejs.com/), the most
popular MongoDB ODM for Node.js, against a **local** [DocumentDB](https://github.com/documentdb/documentdb)
instance running in Docker. It includes:

- a small **Express + Mongoose REST API** (`app/`), and
- a standalone **Mongoose CRUD/compatibility test suite**
  (`app/mongoose-crud-test.js`) that exercises connect, schema/index creation,
  insert, query, update, aggregation, unique-index enforcement, and delete.

The normal app and test paths run entirely on your machine: DocumentDB in a
Docker container, with the app and tests as local Node.js processes. The
optional tracing demo uses a published image containing the gateway tracing
changes.

> **What is Mongoose?** Mongoose is a **Node.js library** (an ODM, Object Data
> Modeling layer), not a CLI tool or a server. Your application imports it to
> define schemas/models and talk to a MongoDB-compatible database. Here it is
> used by the demo **app** ([`app/server.js`](app/server.js)) and the standalone
> **test script** ([`app/mongoose-crud-test.js`](app/mongoose-crud-test.js)).

## Prerequisites

- **Docker** (Docker Desktop or a Docker daemon) — runs DocumentDB locally
- **Node.js 18.19.x or 20.6+** and **npm** — runs the app and test suite

That's it. The scripts pull the official `documentdb-local` image and start it
for you.

## Quick Start

From this directory (`playgrounds/mongoose/`). `run-test.sh` and `run-app.sh`
are **two independent operations** — each starts DocumentDB on its own if it
isn't already running. Pick whichever you need; you do **not** have to run one
before the other.

### Option A — run the test suite

```bash
# Run the full CRUD/compatibility test suite end-to-end.
# Starts DocumentDB in Docker (first run pulls the image), then runs the tests.
./scripts/run-test.sh
```

Expected output:

```
DocumentDB is ready.
Mongoose DocumentDB compatibility test
======================================
  ✅ connect
  ✅ create indexes (autoIndex / syncIndexes)
  ✅ insertOne (Model.create)
  ✅ insertMany
  ✅ findById (known issue resolved)
  ...
  ✅ $vectorSearch returns nearest neighbor
  ...
======================================
Passed: 16  Failed: 0  Known issues: 0
```

### Option B — run the demo REST API

```bash
# Starts DocumentDB (if not already running) and serves the API on :3000.
# This one stays in the foreground until you press Ctrl-C.
./scripts/run-app.sh
```

> **Running both:** they share the same local DocumentDB container but are
> otherwise isolated — the app serves on port `3000` and uses the
> `mongoose_demo` database, while the test suite uses `mongoose_test`. You can
> leave `run-app.sh` running in one terminal and run `run-test.sh` in another;
> they won't interfere (there's no port-forward to clobber anymore).

Stop the database when you're done:

```bash
./scripts/stop-documentdb.sh
```

## Trace the application and gateway

The shared telemetry stack runs a tracing-enabled DocumentDB image, an
OpenTelemetry Collector, and Jaeger. The Mongoose app emits HTTP and client
spans, then puts the active W3C trace context in each supported database
command's `comment` field. The gateway uses that context as the parent of
`gateway.request`.

```text
HTTP request
  -> Express server span
       -> Mongoose client span
            -> gateway.request
                 -> gateway.process_request
                      -> postgres.execute
```

Start the telemetry stack and Mongoose API. The stack pulls
`ghcr.io/documentdb/documentdb/documentdb-local:trace-4fbbfcb8`
automatically:

```bash
cd ../../playgrounds/mongoose
./scripts/run-telemetry-demo.sh
```

In another terminal, generate API traffic and verify that Jaeger received one
trace containing both the application and gateway services:

```bash
./scripts/verify-telemetry.sh
```

The verification helper also requires Python 3.

The verification script prints a direct trace URL. The Jaeger UI is also
available at <http://localhost:16686>.

The normal `run-app.sh` and `run-test.sh` paths remain unchanged. Application
tracing is enabled only when `OTEL_TRACES_ENABLED=true`.

## Trying the API

With `./scripts/run-app.sh` running, the API is on `http://localhost:3000`:

```bash
# Health
curl -s http://localhost:3000/health
# {"status":"healthy","db":"connected"}

# Create a book
curl -s -X POST http://localhost:3000/books \
  -H 'Content-Type: application/json' \
  -d '{"title":"Dune","author":"Herbert","genres":["sci-fi"],"pages":412,"rating":5}'

# List books
curl -s http://localhost:3000/books | jq .

# Count books per genre (aggregation)
curl -s http://localhost:3000/stats/genres | jq .
```

## Scripts

| Script                        | Purpose                                                                        |
| ----------------------------- | ------------------------------------------------------------------------------ |
| `scripts/run-test.sh`         | Start DocumentDB (if needed) and run the Mongoose CRUD/compatibility suite.     |
| `scripts/run-app.sh`          | Start DocumentDB (if needed) and run the Express + Mongoose API locally.        |
| `scripts/run-telemetry-demo.sh` | Start the shared Collector, Jaeger, and DocumentDB stack, then run the traced API. |
| `scripts/verify-telemetry.sh` | Generate API traffic and verify a connected application and gateway trace.      |
| `scripts/start-documentdb.sh` | Start the local DocumentDB container and wait until it is ready.                |
| `scripts/stop-documentdb.sh`  | Stop and remove the local DocumentDB container.                                |
| `scripts/lib.sh`              | Shared helpers: container lifecycle, readiness wait, connection-string builder. |

All scripts are idempotent — they reuse a running DocumentDB container instead
of starting a new one. By default `run-test.sh` leaves DocumentDB running for
fast re-runs; set `KEEP_DB=0` to remove it automatically when the tests finish.

## How DocumentDB Runs Locally

`scripts/start-documentdb.sh` runs the official image:

```bash
docker run -dt -p 127.0.0.1:10260:10260 --name documentdb-local \
  ghcr.io/documentdb/documentdb/documentdb-local:latest \
  --username docdbadmin --password 'Documentdb!Local1'
```

The gateway speaks the MongoDB wire protocol on port `10260`, so the resulting
connection string is a standard MongoDB URI:

```
mongodb://docdbadmin:Documentdb!Local1@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true
```

> The port is bound to `127.0.0.1` on purpose: it keeps the database off
> external interfaces and avoids a Docker Desktop/WSL2 quirk where the IPv4
> loopback mapping is otherwise unreachable from host processes.

## Connecting Mongoose to DocumentDB

The DocumentDB gateway advertises itself as a **standalone** server over
**TLS**. Mongoose therefore needs three options (see [`app/db.js`](app/db.js)):

```js
await mongoose.connect(uri, {
  directConnection: true,             // gateway is standalone, not a replica set
  tls: true,                          // gateway only accepts TLS
  tlsAllowInvalidCertificates: true,  // local image uses a self-signed cert
});
```

If your connection string contains `replicaSet=rs0`, strip it — the Node driver
treats it as conflicting with `directConnection`. Both `app/db.js` and the test
script strip it automatically.

For production, set `TLS_INSECURE=false`, provide the server's CA, and pass
`tlsCAFile` instead of `tlsAllowInvalidCertificates`.

## Configuration Reference

All settings are environment variables; there is no config file.

### DocumentDB container (`scripts/`)

Read by [`lib.sh`](scripts/lib.sh) and the scripts that source it.

| Variable               | Default                                                 | Description                                                       |
| ---------------------- | ------------------------------------------------------- | ---------------------------------------------------------------- |
| `DOCUMENTDB_IMAGE`     | `ghcr.io/documentdb/documentdb/documentdb-local:latest` | DocumentDB local image to run.                                   |
| `DOCUMENTDB_CONTAINER` | `documentdb-local`                                      | Docker container name.                                           |
| `DOCUMENTDB_PORT`      | `10260`                                                 | Host port mapped to the gateway.                                 |
| `DOCUMENTDB_USERNAME`  | `docdbadmin`                                            | Gateway username (avoid the reserved name `documentdb`).         |
| `DOCUMENTDB_PASSWORD`  | `Documentdb!Local1`                                     | Gateway password (dev default; change for anything real).        |
| `KEEP_DB`              | `1`                                                     | `run-test.sh` only: set `0` to remove the container after tests. |

### App + test script (`app/`)

Read by [`app/db.js`](app/db.js), [`app/server.js`](app/server.js), and
[`app/mongoose-crud-test.js`](app/mongoose-crud-test.js).

| Variable                      | Default                                       | Used by    | Description                                                                          |
| ----------------------------- | --------------------------------------------- | ---------- | ----------------------------------------------------------------------------------- |
| `MONGO_URI`                   | local URI (default creds)                     | app + test | DocumentDB connection string. `replicaSet=rs0` is stripped automatically. The test script also accepts it as the first CLI argument. |
| `MONGO_DB`                    | `mongoose_demo` (app), `mongoose_test` (test) | app + test | Database name Mongoose connects to.                                                 |
| `TLS_INSECURE`                | `true`                                        | app + test | When `true`, accepts the gateway's self-signed cert. Set `false` for CA-verified TLS.|
| `SERVER_SELECTION_TIMEOUT_MS` | `10000`                                       | app        | How long Mongoose waits to select a server before erroring.                         |
| `PORT`                        | `3000`                                        | app        | Port the Express API listens on.                                                    |
| `OTEL_TRACES_ENABLED`         | `false`                                       | app        | Enable HTTP, Mongoose client, and trace-context propagation instrumentation.        |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry default                         | app        | Collector endpoint used by the traced app.                                          |
| `OTEL_SERVICE_NAME`           | SDK default                                   | app        | Application service name shown in Jaeger.                                           |

## Running the Test Suite Manually

The scripts handle everything, but you can also run the suite directly against
any reachable DocumentDB connection string:

```bash
cd app
npm install
MONGO_URI="mongodb://docdbadmin:Documentdb!Local1@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true" \
  node mongoose-crud-test.js
```

## DocumentDB Compatibility Notes

| Mongoose feature            | Status with DocumentDB | Notes                                                                 |
| --------------------------- | ---------------------- | --------------------------------------------------------------------- |
| CRUD (`create`/`find`/…)    | ✅ Supported           | Standard document operations work as expected.                        |
| `autoIndex` / `syncIndexes` | ✅ Supported           | Avoid `collation` on indexes; DocumentDB does not implement them.     |
| Unique indexes              | ✅ Supported           | Duplicate keys raise the standard `E11000` error.                     |
| Aggregation pipelines       | ✅ Common stages       | `$match`, `$group`, `$unwind`, `$sort`, etc. Atlas-only stages differ.|
| `findById` / `_id` lookups  | ✅ Supported           | Works on the current `documentdb-local:latest` image.                 |
| `$vectorSearch` / vector search | ✅ Supported       | Create a `cosmosSearch` vector index (e.g. `vector-ivf`), then query with the `$vectorSearch` stage or `$search` + `cosmosSearch`. |
| Index `collation`           | ❌ Not supported       | `createIndex.collation is not implemented yet`; omit it.             |

The CRUD test suite ([`app/mongoose-crud-test.js`](app/mongoose-crud-test.js))
covers the supported rows above and prints a pass/fail summary. The `findById`
step is guarded so that if you run an **older** image where `_id` point lookups
fail (`trying to open a pruned relation`), it is reported as a known issue
instead of a hard failure.

## Troubleshooting

### `Cannot reach the Docker daemon`

Start Docker Desktop (or your Docker daemon) and try again.

### `MongooseServerSelectionError` / connection timeouts

- Confirm the container is up: `docker ps --filter name=documentdb-local`.
- Inspect readiness: `docker logs documentdb-local` should show the gateway and
  PostgreSQL running.
- The port is bound to `127.0.0.1`; make sure your `MONGO_URI` host matches
  (`localhost` / `127.0.0.1`) and the port is `10260`.

### `Username is invalid`

The gateway rejects some reserved names (for example `documentdb`). Use
`docdbadmin` (the default) or another non-reserved username, and recreate the
container so the new credentials take effect:

```bash
./scripts/stop-documentdb.sh
DOCUMENTDB_USERNAME=myuser ./scripts/start-documentdb.sh
```

### `createIndex.collation is not implemented yet`

A schema index uses `collation`. Remove it; DocumentDB does not support
collation indexes. The models in this playground intentionally avoid it.

## Directory Layout

```
mongoose/
├── README.md
├── app/
│   ├── package.json
│   ├── db.js                    # Mongoose connection (DocumentDB options)
│   ├── server.js                # Express REST API (/books, /health, /stats)
│   ├── telemetry.js             # Optional OpenTelemetry setup + context propagation
│   ├── models/book.js           # Example Mongoose schema/model
│   └── mongoose-crud-test.js    # Standalone CRUD/compatibility test suite
└── scripts/
    ├── lib.sh                   # Shared container lifecycle + connection-string builder
    ├── start-documentdb.sh      # Start the local DocumentDB container
    ├── stop-documentdb.sh       # Stop/remove the local DocumentDB container
    ├── run-app.sh               # Start DocumentDB + run the API locally
    ├── run-telemetry-demo.sh    # Start shared telemetry + run the traced API
    ├── verify-telemetry.sh      # Verify the joined app and gateway trace
    └── run-test.sh              # Start DocumentDB + run the test suite
```
