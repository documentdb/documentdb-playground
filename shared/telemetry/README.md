# Shared telemetry stack

This stack runs a tracing-enabled local DocumentDB image, an OpenTelemetry
Collector, and Jaeger. Every playground can use the same `documentdb-local`
container on port `10260`.

```text
Application or test
  -> documentdb-local:10260
       -> OpenTelemetry Collector:4317
            -> Jaeger:4317
                 -> Jaeger UI:16686
```

The local DocumentDB image contains PostgreSQL, the DocumentDB extensions, and
the gateway in one container. The image build has two stages, but the demo does
not need separate PostgreSQL and gateway services.

## Prerequisites

- Docker with Docker Compose
- A DocumentDB source checkout containing the gateway tracing changes
- Bash 3.2 or newer
- `curl`
- Python 3 for the Mongoose trace verification helper

## 1. Build the custom image

Run the build helper with a DocumentDB source directory. The directory can be a
standalone source checkout or a parent repository whose source is under `oss/`.

```bash
DOCUMENTDB_SOURCE_DIR=/path/to/documentdb \
  ./scripts/build-documentdb-image.sh
```

The default local tag is `documentdb-local:gateway-tracing`. Override it with
`DOCUMENTDB_IMAGE` or the script's second positional argument:

```bash
./scripts/build-documentdb-image.sh \
  /path/to/documentdb \
  documentdb-local:my-tracing-build
```

The helper first builds the DocumentDB extension package, then builds the
self-contained image with the gateway binary from the same source revision.

## 2. Configure the stack

The checked-in defaults are sufficient for local use. To customize them:

```bash
cp .env.example .env
```

Important settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOCUMENTDB_IMAGE` | `documentdb-local:gateway-tracing` | Local image containing the tracing changes. |
| `DOCUMENTDB_PORT` | `10260` | Host gateway port. |
| `OTEL_COLLECTOR_GRPC_PORT` | `4317` | Host OTLP/gRPC port for local applications. |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Gateway trace sampling ratio. |
| `DOCUMENTDB_SQL_COMMENTER_ENABLED` | `false` | Add sampled trace context to generated SQL for log correlation. |
| `JAEGER_UI_PORT` | `16686` | Host Jaeger UI port. |

The gateway configuration is mounted from
[`gateway-setup.json`](gateway-setup.json). Tracing must be enabled there
because JSON configuration takes precedence over environment variables.

## 3. Start or stop the stack

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
