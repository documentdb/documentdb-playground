# Beanie with DocumentDB (local)

This playground shows how to use [Beanie](https://beanie-odm.dev/), a popular
**asynchronous Python ODM** built on [PyMongo](https://pymongo.readthedocs.io/)'s
native async API and [Pydantic](https://docs.pydantic.dev/), against DocumentDB —
running **entirely on your machine**. It includes:

- a small **FastAPI + Beanie REST API** (`app/`), and
- a standalone **Beanie CRUD/compatibility test suite**
  (`app/beanie_crud_test.py`) that exercises connect, index creation, insert,
  query, update, aggregation, unique-index enforcement, and delete.

There is **no Kubernetes and no cloud**. DocumentDB runs as the
[`documentdb-local`](https://github.com/documentdb/documentdb) emulator in a
single Docker container, and the app/test run as local Python processes that
connect straight to it.

> **What is Beanie?** Beanie is a **Python library** (an async ODM, Object
> Document Mapper), not a CLI tool or a server. Your application imports it to
> define `Document` models (Pydantic classes) and talk to a MongoDB-compatible
> database over PyMongo's `AsyncMongoClient`. Here it is used by the demo **app**
> ([`app/main.py`](app/main.py)) and the standalone **test script**
> ([`app/beanie_crud_test.py`](app/beanie_crud_test.py)).

## Architecture

Everything is local. The emulator container exposes the MongoDB wire protocol on
`localhost:10260`; the Python processes connect to it directly.

```
   Your machine (WSL / Linux / macOS)
┌──────────────────────────────────────────────────────────────────┐
│  ┌────────────────────┐         ┌──────────────────────────────┐  │
│  │  beanie app /      │  TLS,   │  documentdb-local (Docker)   │  │
│  │  test script       │  wire   │  ┌────────────┐ ┌─────────┐  │  │
│  │  (Python + Beanie) │────────▶│  │  Gateway   │▶│Postgres │  │  │
│  │                    │ :10260  │  │ (10260)    │ │ (engine)│  │  │
│  └────────────────────┘         │  └────────────┘ └─────────┘  │  │
│                                 └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

Beanie talks to the emulator through PyMongo's `AsyncMongoClient` exactly as it
would to a standalone `mongod`, with a few required options (see [Connecting
Beanie to DocumentDB](#connecting-beanie-to-documentdb)).

## Prerequisites

- **Docker** (to run the `documentdb-local` emulator)
- **Python 3.10 to 3.13** with `venv` (to run the app and test suite). Beanie
  2.x declares `Requires-Python <3.14`, so pip cannot install it on newer
  interpreters (see [Troubleshooting](#no-matching-distribution-found-for-beanie)).

The scripts create a Python virtualenv and install dependencies for you. On
Windows, run these from a **WSL** shell.

## Quick Start

From this directory (`playgrounds/beanie/`). `run-test.sh` and `run-app.sh`
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

With `./scripts/run-app.sh` running, the API is on `http://localhost:3000`
(interactive docs at `http://localhost:3000/docs`):

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

## Connecting Beanie to DocumentDB

The DocumentDB gateway speaks the MongoDB wire protocol but advertises itself as
a **standalone** server over **TLS** (with a self-signed cert). Beanie 2.x talks
to it through PyMongo's `AsyncMongoClient` (not Motor), and that client needs
these options (see [`app/db.py`](app/db.py)):

```python
from pymongo import AsyncMongoClient
from beanie import init_beanie

client = AsyncMongoClient(
    uri,
    directConnection=True,             # gateway is standalone, not a replica set
    tls=True,                          # gateway only accepts TLS
    tlsAllowInvalidCertificates=True,  # emulator uses a self-signed cert
)
await init_beanie(database=client[db_name], document_models=[Book])
```

For operations Beanie does not model, `Book.get_pymongo_collection()` returns
the underlying PyMongo `AsyncCollection`. Its I/O methods are coroutines,
including `aggregate()`, which resolves to a cursor
(`cursor = await collection.aggregate(pipeline)`), whereas `find()` returns a
cursor without awaiting. Close the client with `await client.close()`.

The connection string built by the scripts is:

```
mongodb://<user>:<pass>@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true
```

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
| `PORT`                 | `3000`                                                   | Local port the FastAPI app listens on (`run-app.sh`).    |

### App + test script (`app/`)

Read by [`app/db.py`](app/db.py), [`app/main.py`](app/main.py), and
[`app/beanie_crud_test.py`](app/beanie_crud_test.py). The scripts set `MONGO_URI`
for you from the variables above.

| Variable                      | Default          | Description                                                                                  |
| ----------------------------- | ---------------- | -------------------------------------------------------------------------------------------- |
| `MONGO_URI`                   | _(set by scripts)_ | DocumentDB connection string. The test script also accepts it as the first CLI argument.  |
| `MONGO_DB`                    | `beanie_demo` (app), `beanie_test` (test) | Database name Beanie connects to.                                    |
| `TLS_INSECURE`                | `true`           | When `true`, accepts the self-signed cert. Set `false` for CA-verified TLS.                  |
| `SERVER_SELECTION_TIMEOUT_MS` | `10000`          | How long PyMongo waits to select a server before erroring.                                   |
| `PORT`                        | `3000`           | Port the FastAPI/Uvicorn API listens on.                                                     |

## DocumentDB Compatibility Notes

Verified against `documentdb-local:latest` (release `0.117`):

| Beanie feature                          | Status        | Notes                                                                 |
| --------------------------------------- | ------------- | --------------------------------------------------------------------- |
| CRUD (`insert`/`find`/`save`/`delete`)  | ✅ Supported  | Standard document operations work as expected.                        |
| `get(id)` / `_id` point lookups         | ✅ Supported  | Works on `0.114`. (Older gateway `0.109.0` failed these with "trying to open a pruned relation".) |
| Index creation via `Settings.indexes`   | ✅ Supported  | Built asynchronously by the engine; `createIndexes` returns in ~2s. Avoid `collation`. |
| Unique indexes                          | ✅ Supported  | Duplicate keys raise `DuplicateKeyError` (code `11000`).              |
| Aggregation pipelines                   | ✅ Common stages | `$match`, `$group`, `$unwind`, `$sort`, etc. Atlas-only stages differ. |
| `$vectorSearch` / vector search         | ✅ Supported  | DocumentDB supports a `cosmosSearch` vector index (e.g. `vector-ivf`) queried via the `$vectorSearch` stage. The test suite creates one and runs a nearest-neighbor query. |
| Index `collation`                       | ❌ Not supported | `createIndex.collation is not implemented yet`; omit it.           |
| Change streams / transactions           | ⚠️ Check version | Verify against your DocumentDB version before relying on them.     |

The CRUD test suite ([`app/beanie_crud_test.py`](app/beanie_crud_test.py))
covers the supported rows above and prints a pass/fail summary.

## Running the Test Suite Manually

`scripts/run-test.sh` sets `MONGO_URI` and runs the suite for you. To run it
directly against any reachable connection string:

```bash
cd app
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
MONGO_URI="mongodb://docdbadmin:Documentdb!Local1@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true" \
  .venv/bin/python beanie_crud_test.py
```

Expected output:

```
Beanie DocumentDB compatibility test
====================================
  ✅ connect
  ✅ create indexes
  ✅ insert_one (Document.insert)
  ✅ insert_many
  ✅ get by _id (Document.get)
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
====================================
Passed: 16  Failed: 0
```

## What the Scripts Do

| Script                        | Purpose                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------- |
| `scripts/start-documentdb.sh` | Start the local emulator container and wait until the gateway is ready.                    |
| `scripts/run-test.sh`         | Start DocumentDB (if needed), set up the venv, and run the Beanie CRUD/compatibility suite. |
| `scripts/run-app.sh`          | Start DocumentDB (if needed), set up the venv, and run the FastAPI + Beanie demo app.     |
| `scripts/stop-documentdb.sh`  | Stop and remove the emulator container (full reset of its data).                          |
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

### `No matching distribution found for beanie`

pip reports this, along with `Ignored the following versions that require a
different python version`, when the virtualenv uses Python 3.14 or newer. Beanie
2.x supports Python 3.10 to 3.13. The scripts create `app/.venv` with `python3`
only when it does not exist yet, so recreate it with a supported interpreter and
rerun:

```bash
rm -rf app/.venv
python3.13 -m venv app/.venv
./scripts/run-test.sh
```

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

A model index uses `collation`. Remove it; DocumentDB does not support collation
indexes. The models in this playground intentionally avoid it.

## Directory Layout

```
beanie/
├── README.md
├── app/
│   ├── requirements.txt
│   ├── db.py                    # AsyncMongoClient connection + init_beanie (DocumentDB options)
│   ├── main.py                  # FastAPI REST API (/books, /health, /stats)
│   ├── models/
│   │   └── book.py              # Example Beanie Document model
│   └── beanie_crud_test.py      # Standalone CRUD/compatibility test suite
└── scripts/
    ├── lib.sh                   # Connection-string builder + readiness wait + venv setup
    ├── start-documentdb.sh      # Start the local emulator (Docker)
    ├── run-app.sh               # Run the demo app locally
    ├── run-test.sh              # Run the test suite locally
    └── stop-documentdb.sh       # Stop + remove the emulator
```
