# documentdb-playground

A collection of small, self-contained **playgrounds** that show how to use
[DocumentDB](https://github.com/documentdb/documentdb) — the MongoDB-compatible,
open-source database engine built on PostgreSQL — from popular MongoDB drivers
and ODMs.

Each playground is a quick, hands-on test guide. Most run DocumentDB **locally
in Docker** — everything on your machine, no cloud required — so you can validate
that your driver of choice behaves the way you expect. Others may target
different environments (for example Kubernetes or a cloud deployment); each
playground's README states what it needs.

## Playgrounds

| Playground                              | Language / Stack         | What it shows                                                                    |
| --------------------------------------- | ------------------------ | -------------------------------------------------------------------------------- |
| [mongoose](playgrounds/mongoose/)       | Node.js — Mongoose ODM   | Express REST API + a CRUD/compatibility test suite using the Mongoose ODM.       |
| [mongodb-node](playgrounds/mongodb-node/) | Node.js — MongoDB driver | Express REST API + a CRUD/compatibility suite using the native Node.js driver.   |
| [beanie](playgrounds/beanie/)           | Python — Beanie ODM      | FastAPI REST API + a CRUD/compatibility test suite using the Beanie ODM.         |
| [pymongo](playgrounds/pymongo/)         | Python — PyMongo driver  | Flask REST API + a CRUD/compatibility test suite using the raw PyMongo driver.   |

More MongoDB driver playgrounds are planned. Contributions are welcome.

## Getting Started

Pick a playground from the table above and follow its README. As a rule, each
one only needs:

- **Docker** (Docker Desktop or a Docker daemon) — to run DocumentDB locally
- The language runtime for that playground (for example Node.js for `mongoose`)

For example, to try the Mongoose playground:

```bash
cd playgrounds/mongoose
./scripts/run-test.sh   # start DocumentDB locally and run the compatibility suite
./scripts/run-app.sh    # or run the demo REST API
```

To try the MongoDB Node.js native driver playground:

```bash
cd playgrounds/mongodb-node
./scripts/run-test.sh   # start DocumentDB locally and run the compatibility suite
./scripts/run-app.sh    # or run the demo REST API
```

To try the Beanie playground:

```bash
cd playgrounds/beanie
./scripts/run-test.sh   # start DocumentDB locally and run the compatibility suite
./scripts/run-app.sh    # or run the demo REST API
```

To try the PyMongo playground:

```bash
cd playgrounds/pymongo
./scripts/run-test.sh   # start DocumentDB locally and run the compatibility suite
./scripts/run-app.sh    # or run the demo REST API
```

## Shared telemetry demo

[`shared/telemetry/`](shared/telemetry/) provides a reusable local stack with a
tracing-enabled DocumentDB image, an OpenTelemetry Collector, and Jaeger. Start
it once, then use any playground against the shared `documentdb-local`
container.

The Mongoose playground also includes optional application instrumentation and
client-to-gateway trace-context propagation:

```bash
cd playgrounds/mongoose
./scripts/run-telemetry-demo.sh
```

See the [shared telemetry guide](shared/telemetry/README.md) for image,
configuration, and verification details.

## Repository Layout

```
documentdb-playground/
├── README.md
├── LICENSE
├── shared/
│   └── telemetry/    # Collector + Jaeger + tracing-enabled DocumentDB stack
└── playgrounds/
    ├── mongoose/     # Node.js + Mongoose ODM
    ├── mongodb-node/ # Node.js + MongoDB native driver
    ├── beanie/       # Python + Beanie ODM
    └── pymongo/      # Python + PyMongo driver
```

## License

See [LICENSE](LICENSE).