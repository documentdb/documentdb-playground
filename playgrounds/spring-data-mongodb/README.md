# Spring Data MongoDB with DocumentDB (local)

This playground shows how to use [Spring Data MongoDB](https://spring.io/projects/spring-data-mongodb)
— the Spring ecosystem's MongoDB abstraction for **Java** — against DocumentDB,
running **entirely on your machine**. It is the JVM counterpart to the Node.js
(Mongoose / native driver) and Python (Beanie / PyMongo) playgrounds. It
includes:

- a small **Spring Boot + Spring Data MongoDB REST API** (`app/`), and
- a standalone **CRUD/compatibility test suite**
  ([`CrudCompatibilityTest`](app/src/main/java/com/example/playground/CrudCompatibilityTest.java))
  that exercises connect, index creation, insert, query, update, aggregation,
  unique-index enforcement, delete, and vector search.

There is **no Kubernetes and no cloud**. DocumentDB runs as the
[`documentdb-local`](https://github.com/documentdb/documentdb) emulator in a
single Docker container, and the app/test run as local JVM processes (via Maven)
that connect straight to it.

> **What is Spring Data MongoDB?** It is a **Java library** that layers
> repositories (`MongoRepository`) and a template API (`MongoTemplate`) over the
> official MongoDB Java driver. Your application defines `@Document` classes and
> repository interfaces; Spring Data generates queries and maps documents to
> objects. Here it is used by the demo **app**
> ([`BookController`](app/src/main/java/com/example/playground/web/BookController.java))
> and the standalone **test**
> ([`CrudCompatibilityTest`](app/src/main/java/com/example/playground/CrudCompatibilityTest.java)).

## Architecture

Everything is local. The emulator container exposes the MongoDB wire protocol on
`localhost:10260`; the JVM processes connect to it directly.

```
   Your machine (WSL / Linux / macOS)
┌──────────────────────────────────────────────────────────────────┐
│  ┌────────────────────┐         ┌──────────────────────────────┐  │
│  │  spring-data app / │  TLS,   │  documentdb-local (Docker)   │  │
│  │  test (Java + JVM) │  wire   │  ┌────────────┐ ┌─────────┐  │  │
│  │  Spring Data +     │────────▶│  │  Gateway   │▶│Postgres │  │  │
│  │  MongoDB Java driver│ :10260 │  │ (10260)    │ │ (engine)│  │  │
│  └────────────────────┘         │  └────────────┘ └─────────┘  │  │
│                                 └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- **Docker** (to run the `documentdb-local` emulator)
- **JDK 17+** (Spring Boot 3 requires Java 17 or newer)
- **Maven** (a system `mvn`; the scripts also use `./mvnw` if you add a wrapper)

On Windows, run these from a **WSL** shell.

## Quick Start

From this directory (`playgrounds/spring-data-mongodb/`). `run-test.sh` and
`run-app.sh` are **two independent operations** — each starts DocumentDB on its
own if it isn't already running.

### Option A — run the test suite

```bash
# Run the full CRUD/compatibility suite end-to-end.
# Starts DocumentDB in Docker (first run pulls the image), then runs the tests.
./scripts/run-test.sh
```

Expected output:

```
Spring Data MongoDB DocumentDB compatibility test
=================================================
  ✅ connect
  ✅ create indexes
  ✅ insert (single)
  ✅ insert (many)
  ✅ findById
  ✅ find with filter + sort + limit
  ✅ count
  ✅ updateFirst ($set)
  ✅ findAndModify (returns new)
  ✅ aggregation ($unwind/$group/$sort)
  ✅ unique index enforcement (duplicate sku rejected)
  ✅ delete (single)
  ✅ vector index + insert (cosmosSearch vector-ivf)
  ✅ $vectorSearch returns nearest neighbor
  ✅ vector cleanup (drop collection)
  ✅ cleanup (drop collection)
=================================================
Passed: 16  Failed: 0
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

Set `KEEP_DB=0` when running the suite to remove the container automatically:

```bash
KEEP_DB=0 ./scripts/run-test.sh
```

The API uses the `springdata_demo` database and the suite uses
`springdata_test`, so they can run at the same time.

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
curl -s 'http://localhost:3000/books?author=Herbert' | jq .

# Count books per genre (aggregation)
curl -s http://localhost:3000/stats/genres | jq .
```

The API also provides `GET`, `PATCH`, and `DELETE /books/{id}`.

## Connecting Spring Data to DocumentDB

The DocumentDB gateway speaks the MongoDB wire protocol but advertises itself as
a **standalone** server over **TLS** (with a self-signed cert). Spring Data uses
the MongoDB Java driver underneath, so the playground supplies its own
`MongoClient` built from a sanitized connection string plus TLS settings (see
[`MongoConfig`](app/src/main/java/com/example/playground/config/MongoConfig.java)
and [`MongoClientFactory`](app/src/main/java/com/example/playground/support/MongoClientFactory.java)):

```java
@Bean
MongoClient mongoClient() {
    return MongoClientFactory.fromEnv(); // sanitizes the URI + trusts the local cert
}
```

Two things differ from the sibling playgrounds because of how the **Java** driver
handles TLS:

- **Self-signed certificate.** Node.js/Python accept it with
  `tlsAllowInvalidCertificates=true`. The Java driver has no connection-string
  option that bypasses certificate-chain validation (its `tlsInsecure` only
  disables *hostname* verification). So, when `TLS_INSECURE` is not `false`, the
  playground installs a trust-all `SSLContext` and allows invalid hostnames in
  `MongoClientSettings` — the Java equivalent of the other drivers' option.
- **`replicaSet` is stripped**, because it conflicts with `directConnection=true`
  against the standalone gateway.

The connection string built by the scripts is:

```
mongodb://<user>:<pass>@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true
```

The app strips `tlsAllowInvalidCertificates` (handled by the trust-all
`SSLContext` instead) and `replicaSet`, connecting with:

```
mongodb://<user>:<pass>@localhost:10260/?tls=true&directConnection=true
```

For production against a real (non-emulator) deployment, set `TLS_INSECURE=false`
and configure a truststore containing the server's CA instead of trusting all
certificates.

## Configuration Reference

All settings are passed via environment variables; there is no secrets file.

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
| `KEEP_DB`              | `1`                                                      | `run-test.sh` only: set `0` to remove the container after tests. |
| `PORT`                 | `3000`                                                   | Local port the Spring Boot app listens on (`run-app.sh`). |

### App + test (`app/`)

Read by [`MongoConfig`](app/src/main/java/com/example/playground/config/MongoConfig.java),
[`MongoClientFactory`](app/src/main/java/com/example/playground/support/MongoClientFactory.java),
and [`CrudCompatibilityTest`](app/src/main/java/com/example/playground/CrudCompatibilityTest.java).
The scripts set `MONGO_URI` for you from the variables above.

| Variable       | Default                                          | Description                                                                          |
| -------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `MONGO_URI`    | _(set by scripts)_                               | DocumentDB connection string. `replicaSet` and `tlsAllowInvalidCertificates` are stripped automatically (the local cert is trusted via an SSLContext). The test also accepts it as the first CLI argument. |
| `MONGO_DB`     | `springdata_demo` (app), `springdata_test` (test)| Database name Spring Data connects to.                                               |
| `TLS_INSECURE` | `true`                                           | When `true`, trusts the self-signed cert and allows invalid hostnames. Set `false` for CA-verified TLS. |
| `PORT`         | `3000`                                           | Port the Spring Boot API listens on.                                                 |

## Running the Suite Manually

The scripts handle everything, but you can also run the suite directly against
any reachable DocumentDB connection string:

```bash
cd app
MONGO_URI='mongodb://docdbadmin:Documentdb!Local1@localhost:10260/?tls=true&tlsAllowInvalidCertificates=true&directConnection=true' \
  mvn -q compile exec:java
```

## DocumentDB Compatibility Notes

| Spring Data feature                          | Status        | Notes                                                                 |
| -------------------------------------------- | ------------- | --------------------------------------------------------------------- |
| CRUD (`insert`/`find`/`save`/`remove`)       | ✅ Supported  | Standard `MongoTemplate` and repository operations work as expected.  |
| `findById` / `_id` point lookups             | ✅ Supported  | Works on the current `documentdb-local:latest` image.                 |
| Index creation (`auto-index-creation`, `IndexOperations`) | ✅ Supported | Built asynchronously by the engine. Avoid `collation`.   |
| Unique indexes                               | ✅ Supported  | Duplicate keys raise Spring's `DuplicateKeyException` (driver code `11000`). |
| `findAndModify` (returns new)                | ✅ Supported  | `FindAndModifyOptions.returnNew(true)` returns the updated document.  |
| Aggregation pipelines                        | ✅ Common stages | `$match`, `$group`, `$unwind`, `$sort`, etc. Atlas-only stages differ. |
| `$vectorSearch` / vector search              | ✅ Supported  | DocumentDB supports a `cosmosSearch` vector index (e.g. `vector-ivf`) queried via the `$vectorSearch` stage. The suite creates one and runs a nearest-neighbor query. |
| Index `collation`                            | ❌ Not supported | `createIndex.collation is not implemented yet`; omit it.           |
| Transactions / change streams                | ⚠️ Not covered | The local gateway advertises standalone topology; verify before relying on them. |

## Troubleshooting

### `Cannot reach the Docker daemon`

Start Docker Desktop (or your Docker daemon) and try again.

### `Maven is required` / `java (JDK 17+) is required`

Install a JDK 17+ and Maven, then re-run. The scripts prefer a project wrapper
(`./mvnw`) if present, otherwise a system `mvn`.

### Connection timeouts

- Confirm the container is up: `docker ps --filter name=documentdb-local`.
- Inspect readiness: `docker logs documentdb-local`.
- The port is bound to `127.0.0.1`; ensure your `MONGO_URI` host is `localhost`
  and the port is `10260`.

### TLS handshake / certificate errors

The local emulator uses a self-signed certificate. Keep `TLS_INSECURE=true`
(the default) for local runs; the app maps it to the Java driver's
`tlsInsecure` option.

### `createIndex.collation is not implemented yet`

An index uses `collation`. Remove it; DocumentDB does not implement collation
indexes. The models here intentionally avoid it.

## Directory Layout

```
spring-data-mongodb/
├── README.md
├── app/
│   ├── pom.xml
│   └── src/main/
│       ├── java/com/example/playground/
│       │   ├── Application.java              # Spring Boot entry point
│       │   ├── config/MongoConfig.java       # MongoClient (DocumentDB options)
│       │   ├── support/MongoUriSupport.java  # URI sanitizer (drops replicaSet/tls opts)
│       │   ├── support/MongoClientFactory.java # MongoClient + trust-all SSLContext
│       │   ├── model/Book.java               # @Document model + compound index
│       │   ├── repository/BookRepository.java
│       │   ├── web/BookController.java       # REST API (/books, /stats)
│       │   ├── web/HealthController.java      # /health
│       │   └── CrudCompatibilityTest.java    # Standalone compatibility suite
│       └── resources/application.properties
└── scripts/
    ├── lib.sh                   # Shared container lifecycle + connection-string builder
    ├── start-documentdb.sh
    ├── stop-documentdb.sh
    ├── run-app.sh
    └── run-test.sh
```
