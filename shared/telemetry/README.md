# Shared telemetry stack

This stack runs a tracing-enabled DocumentDB image, an OpenTelemetry Collector,
and Jaeger. Every playground can use the same `documentdb-local` container on
port `10260`.

```text
Application or test
  -> documentdb-local:10260
       -> OpenTelemetry Collector:4317
            -> Jaeger:4317
                 -> Jaeger UI:16686
```

The DocumentDB image contains PostgreSQL 17, the DocumentDB extensions, and the
gateway in one container. The demo does not need separate PostgreSQL and
gateway services.

By default, the stack pulls this image:

```text
ghcr.io/documentdb/documentdb/documentdb-local:trace-4fbbfcb8
```

It was built from DocumentDB commit
[`4fbbfcb8`](https://github.com/documentdb/documentdb/commit/4fbbfcb879cfdedaab5e5f4b37698fbc28f0a7e9),
which contains the gateway tracing support used by this demo.

## Prerequisites

- Docker with Docker Compose
- Bash 3.2 or newer
- `curl`
- Python 3 for the Mongoose trace verification helper

## 1. Configure the stack

The checked-in defaults are sufficient for local use. To customize them:

```bash
cp .env.example .env
```

Important settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCUMENTDB_IMAGE` | `ghcr.io/documentdb/documentdb/documentdb-local:trace-4fbbfcb8` | Published image containing the tracing changes. |
| `DOCUMENTDB_PORT` | `10260` | Host gateway port. |
| `OTEL_COLLECTOR_GRPC_PORT` | `4317` | Host OTLP/gRPC port for local applications. |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Gateway trace sampling ratio. |
| `DOCUMENTDB_SQL_COMMENTER_ENABLED` | `false` | Add sampled trace context to generated SQL for log correlation. |
| `JAEGER_UI_PORT` | `16686` | Host Jaeger UI port. |

The gateway configuration is mounted from
[`gateway-setup.json`](gateway-setup.json). Tracing must be enabled there
because JSON configuration takes precedence over environment variables.

## 2. Start or stop the stack

```bash
./scripts/up.sh
open http://localhost:16686
```

Stop the containers while preserving DocumentDB data:

```bash
./scripts/down.sh
```

Remove the containers and named volumes:

```bash
./scripts/down.sh --volumes
```

## Use a playground

Once the stack is running, the existing playground scripts reuse its
`documentdb-local` container automatically.

For the connected Mongoose application and gateway trace:

```bash
cd ../../playgrounds/mongoose
./scripts/run-telemetry-demo.sh
```

Then run `./scripts/verify-telemetry.sh` in another terminal. The verification
script prints a direct Jaeger trace URL when the application and gateway spans
are joined successfully.

Other playgrounds can use the same stack by starting it here first and then
running their normal application or test scripts.
