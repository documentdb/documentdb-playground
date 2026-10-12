# PyMongo with DocumentDB (local)

This playground shows how to use [PyMongo](https://pymongo.readthedocs.io/), the
official **synchronous Python driver** for MongoDB, against DocumentDB — running
**entirely on your machine**. Unlike the Mongoose and Beanie playgrounds (which
use ODMs), this one talks to DocumentDB at the **raw driver level**: no schema
classes, just collections and BSON documents. It includes:

- a small **Flask + PyMongo REST API** (`app/`), and
- a standalone **PyMongo CRUD/compatibility test suite**
  (`app/pymongo_crud_test.py`) that exercises connect, index creation, insert,
  query, update, aggregation, unique-index enforcement, and delete.

There is **no Kubernetes and no cloud**. DocumentDB runs as the
[`documentdb-local`](https://github.com/documentdb/documentdb) emulator in a
single Docker container, and the app/test run as local Python processes that
connect straight to it.

> **What is PyMongo?** PyMongo is the **official MongoDB driver for Python** — a
> library your application imports to talk to a MongoDB-compatible database. It
> is *not* an ODM: you work directly with databases, collections, and dict-like
> BSON documents. Here it is used by the demo **app**
> ([`app/main.py`](app/main.py)) and the standalone **test script**
> ([`app/pymongo_crud_test.py`](app/pymongo_crud_test.py)).

## Architecture

Everything is local. The emulator container exposes the MongoDB wire protocol on
`localhost:10260`; the Python processes connect to it directly.

```
   Your machine (WSL / Linux / macOS)
┌──────────────────────────────────────────────────────────────────┐
│  ┌────────────────────┐         ┌──────────────────────────────┐  │
│  │  pymongo app /     │  TLS,   │  documentdb-local (Docker)   │  │
│  │  test script       │  wire   │  ┌────────────┐ ┌─────────┐  │  │
│  │  (Python + PyMongo)│────────▶│  │  Gateway   │▶│Postgres │  │  │
│  │                    │ :10260  │  │ (10260)    │ │ (engine)│  │  │
│  └────────────────────┘         │  └────────────┘ └─────────┘  │  │
│                                 └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

PyMongo talks to the emulator exactly as it would to a standalone `mongod`, with
a few required options (see [Connecting PyMongo to
DocumentDB](#connecting-pymongo-to-documentdb)).

## Prerequisites

- **Docker** (to run the `documentdb-local` emulator)
- **Python 3.10+** with `venv` (to run the app and test suite)

The scripts create a Python virtualenv and install dependencies for you. On
Windows, run these from a **WSL** shell.

## Quick Start

From this directory (`playgrounds/pymongo/`). `run-test.sh` and `run-app.sh`
are **two independent operations** — each starts DocumentDB on its own if it
isn't already running.

### Option A — run the test suite

```bash
# Run the full CRUD/compatibility suite end-to-end.
# Starts DocumentDB in Docker (first run pulls the image), then runs the tests.
./scripts/run-test.sh
```

### Option B — run the demo REST API

```bash
# Starts DocumentDB (if not already running) and serves the API on :3000.
# This stays in the foreground until you press Ctrl-C.
./scripts/run-app.sh
```

Stop the database when you are done:

```bash
./scripts/stop-documentdb.sh
```

The suite should end with `Passed: 16  Failed: 0`.

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

## Connecting PyMongo to DocumentDB

The DocumentDB gateway speaks the MongoDB wire protocol but advertises itself as
a **standalone** server over **TLS** (with a self-signed cert). PyMongo
therefore needs these options (see [`app/db.py`](app/db.py)):

```python
from pymongo import MongoClient

client = MongoClient(
    uri,
    directConnection=True,             # gateway is standalone, not a replica set
    tls=True,                          # gateway only accepts TLS
    tlsAllowInvalidCertificates=True,  # emulator uses a self-signed cert
)
db = client["pymongo_demo"]
```

The connection string built by the scripts is:

```
mongodb://<user>:<pass>@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true
```

If your connection string contains `replicaSet=rs0`, strip it — a direct
connection to the standalone gateway conflicts with it. Both
[`app/db.py`](app/db.py) and the test script strip it automatically.

For production against a real (non-emulator) deployment, set `TLS_INSECURE=false`
and pass a CA bundle via `tlsCAFile` instead of `tlsAllowInvalidCertificates`.

## Configuration Reference

All settings are passed via environment variables; there is no config file.

### Emulator + scripts (`scripts/`)

Read by [`lib.sh`](scripts/lib.sh) and the `start`/`stop`/`run` scripts.

| Variable               | Default                                                  | Description                                              |
| ---------------------- | -------------------------------------------------------- | -------------------------------------------------------- |
| `DOCUMENTDB_IMAGE`     | `ghcr.io/documentdb/documentdb/documentdb-local:latest`  | Emulator image to pull/run.                              |
| `DOCUMENTDB_CONTAINER` | `documentdb-local`                                       | Docker container name.                                   |
| `DOCUMENTDB_HOST`      | `localhost`                                              | Host the app/test connect to.                            |
| `DOCUMENTDB_PORT`      | `10260`                                                  | Host port mapped to the gateway.                         |
| `DOCUMENTDB_USERNAME`  | `docdbadmin`                                             | Emulator admin username. **Do not use `documentdb`** (reserved — the gateway rejects it as "Username is invalid"). |
| `DOCUMENTDB_PASSWORD`  | `Documentdb!Local1`                                      | Emulator admin password. If you use special characters, URL-encode them in the connection string. |
| `PORT`                 | `3000`                                                   | Local port the Flask app listens on (`run-app.sh`).      |

### App + test script (`app/`)

Read by [`app/db.py`](app/db.py), [`app/main.py`](app/main.py), and
[`app/pymongo_crud_test.py`](app/pymongo_crud_test.py). The scripts set
`MONGO_URI` for you from the variables above.

| Variable                      | Default          | Description                                                                                  |
| ----------------------------- | ---------------- | -------------------------------------------------------------------------------------------- |
| `MONGO_URI`                   | _(set by scripts)_ | DocumentDB connection string. `replicaSet=rs0` is stripped automatically. The test script also accepts it as the first CLI argument. |
| `MONGO_DB`                    | `pymongo_demo` (app), `pymongo_test` (test) | Database name PyMongo connects to.                            |
| `TLS_INSECURE`                | `true`           | When `true`, accepts the self-signed cert. Set `false` for CA-verified TLS.                  |
| `SERVER_SELECTION_TIMEOUT_MS` | `10000`          | How long PyMongo waits to select a server before erroring.                                   |
| `PORT`                        | `3000`           | Port the Flask API listens on.                                                               |

## DocumentDB Compatibility Notes

| PyMongo feature                          | Status        | Notes                                                                 |
| ---------------------------------------- | ------------- | --------------------------------------------------------------------- |
| CRUD (`insert_*`/`find*`/`update_*`/`delete_*`) | ✅ Supported | Standard document operations work as expected.                  |
| `find_one` / `_id` point lookups         | ✅ Supported  | Works on the current `documentdb-local:latest` image.                 |
| `create_indexes`                         | ✅ Supported  | Built asynchronously by the engine. Avoid `collation`.                |
| Unique indexes                           | ✅ Supported  | Duplicate keys raise `DuplicateKeyError` (code `11000`).              |
| `find_one_and_update` (returns new)      | ✅ Supported  | `ReturnDocument.AFTER` returns the updated document.                  |
| Aggregation pipelines                    | ✅ Common stages | `$match`, `$group`, `$unwind`, `$sort`, etc. Atlas-only stages differ. |
| `$vectorSearch` / vector search          | ✅ Supported  | DocumentDB supports a `cosmosSearch` vector index (e.g. `vector-ivf`) queried via the `$vectorSearch` stage. The test suite creates one and runs a nearest-neighbor query. |
| Index `collation`                        | ❌ Not supported | `createIndex.collation is not implemented yet`; omit it.           |
| Change streams / transactions            | ⚠️ Check version | Verify against your DocumentDB version before relying on them.     |

The CRUD test suite ([`app/pymongo_crud_test.py`](app/pymongo_crud_test.py))
covers the supported rows above and prints a pass/fail summary.

## Running the Test Suite Manually

`scripts/run-test.sh` sets `MONGO_URI` and runs the suite for you. To run it
directly against any reachable connection string:

```bash
cd app
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
MONGO_URI="mongodb://docdbadmin:Documentdb!Local1@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true" \
  .venv/bin/python pymongo_crud_test.py
```

Expected output:

```
PyMongo DocumentDB compatibility test
=====================================
  ✅ connect
  ✅ create indexes
  ✅ insert_one
  ✅ insert_many
  ✅ find_one by _id
  ✅ find with filter + sort + limit
  ✅ count_documents
  ✅ update_one ($set)
  ✅ find_one_and_update (returns new)
  ✅ aggregation ($unwind/$group)
  ✅ unique index enforcement (duplicate sku rejected)
  ✅ delete_one
  ✅ vector index + insert (cosmosSearch vector-ivf)
  ✅ $vectorSearch returns nearest neighbor
  ✅ vector cleanup (drop collection)
  ✅ cleanup (drop collection)
=====================================
Passed: 16  Failed: 0
```

## What the Scripts Do

| Script                        | Purpose                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------ |
| `scripts/start-documentdb.sh` | Start the local emulator container and wait until the gateway is ready.                    |
| `scripts/run-test.sh`         | Start DocumentDB (if needed), set up the venv, and run the PyMongo CRUD/compatibility suite. |
| `scripts/run-app.sh`          | Start DocumentDB (if needed), set up the venv, and run the Flask + PyMongo demo app.       |
| `scripts/stop-documentdb.sh`  | Stop and remove the emulator container (full reset of its data).                           |
| `scripts/lib.sh`              | Shared helpers: container lifecycle, readiness wait, connection-string builder, and venv setup. |

## Verification

- `./scripts/run-test.sh` ends with `Passed: 16  Failed: 0`.
- With `./scripts/run-app.sh` running, `curl http://localhost:3000/health`
  returns `{"status":"healthy","db":"connected"}`, `POST /books` returns `201`
  with the created document, and `GET /stats/genres` returns per-genre counts.

## Cleanup

- **App / test:** press `Ctrl-C` to stop the app; the test exits on its own.
  Optionally remove the virtualenv: `rm -rf app/.venv`.
- **Emulator:** `./scripts/stop-documentdb.sh` removes the container and all its
  data.

## Troubleshooting

### `AuthenticationFailed: Username is invalid.`

The emulator rejects certain reserved usernames — notably `documentdb`. Use a
different admin username (the default here is `docdbadmin`). If you changed
`DOCUMENTDB_USERNAME`, recreate the container so the new credentials take effect:

```bash
./scripts/stop-documentdb.sh && ./scripts/start-documentdb.sh
```

### `ServerSelectionTimeoutError` / TLS handshake failures

The gateway requires TLS. Confirm the connection string includes `tls=true` and
`tlsAllowInvalidCertificates=true` (the scripts add these). Make sure the
emulator is running: `docker ps` should list `documentdb-local`, and
`docker logs documentdb-local` should show the gateway accepting connections.

### Port `10260` already in use

Another process (or a previous emulator) holds the port. Stop it, or run on a
different port:

```bash
DOCUMENTDB_PORT=10261 ./scripts/start-documentdb.sh
DOCUMENTDB_PORT=10261 ./scripts/run-test.sh
```

### Credentials changed but auth still fails

The username/password are baked into the container at creation time. Changing
`DOCUMENTDB_USERNAME`/`DOCUMENTDB_PASSWORD` only takes effect after you recreate
the container (`stop-documentdb.sh` then `start-documentdb.sh`).

### `createIndex.collation is not implemented yet`

An index uses `collation`. Remove it; DocumentDB does not support collation
indexes. The indexes in this playground intentionally avoid it.

## Directory Layout

```
pymongo/
├── README.md
├── app/
│   ├── requirements.txt
│   ├── db.py                    # MongoClient connection (DocumentDB options)
│   ├── main.py                  # Flask REST API (/books, /health, /stats)
│   ├── models/
│   │   └── book.py              # Collection name, indexes, doc builder/serializer
│   └── pymongo_crud_test.py     # Standalone CRUD/compatibility test suite
└── scripts/
    ├── lib.sh                   # Container lifecycle + connection-string builder + venv setup
    ├── start-documentdb.sh      # Start the local emulator (Docker)
    ├── run-app.sh               # Run the demo app locally
    ├── run-test.sh              # Run the test suite locally
    └── stop-documentdb.sh       # Stop + remove the emulator
```
