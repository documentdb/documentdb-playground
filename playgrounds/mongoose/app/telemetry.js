'use strict';

const {
  context,
  propagation,
  SpanKind,
  SpanStatusCode,
  trace,
} = require('@opentelemetry/api');

const tracingEnabled = /^(1|true|yes)$/i.test(process.env.OTEL_TRACES_ENABLED || '');

let sdk;
if (tracingEnabled) {
  const { OTLPTraceExporter } = require('@opentelemetry/exporter-trace-otlp-grpc');
  const { ExpressInstrumentation } = require('@opentelemetry/instrumentation-express');
  const { HttpInstrumentation } = require('@opentelemetry/instrumentation-http');
  const { NodeSDK } = require('@opentelemetry/sdk-node');

  sdk = new NodeSDK({
    traceExporter: new OTLPTraceExporter(),
    instrumentations: [new HttpInstrumentation(), new ExpressInstrumentation()],
  });
  sdk.start();
}

const tracer = trace.getTracer('documentdb-mongoose-playground');
let propagationInstalled = false;

const optionIndexes = new Map([
  ['aggregate', 1],
  ['deleteMany', 1],
  ['deleteOne', 1],
  ['find', 1],
  ['findOne', 1],
  ['findOneAndDelete', 1],
  ['findOneAndReplace', 2],
  ['findOneAndUpdate', 2],
  ['insertMany', 1],
  ['insertOne', 1],
  ['replaceOne', 2],
  ['updateMany', 2],
  ['updateOne', 2],
]);

function currentTraceComment() {
  const carrier = {};
  propagation.inject(context.active(), carrier);
  if (typeof carrier.traceparent !== 'string') {
    return undefined;
  }

  const traceContext = { traceparent: carrier.traceparent };
  if (typeof carrier.tracestate === 'string') {
    traceContext.tracestate = carrier.tracestate;
  }
  return JSON.stringify(traceContext);
}

function installMongooseTracePropagation(mongoose) {
  if (!tracingEnabled || propagationInstalled) {
    return;
  }

  const Collection = mongoose.mongo && mongoose.mongo.Collection;
  if (!Collection) {
    throw new Error('Mongoose does not expose the driver Collection type');
  }

  for (const [methodName, optionsIndex] of optionIndexes) {
    const original = Collection.prototype[methodName];
    if (typeof original !== 'function') {
      continue;
    }

    Collection.prototype[methodName] = function tracedCollectionOperation(...args) {
      const comment = currentTraceComment();
      if (comment) {
        const options = args[optionsIndex];
        if (options == null) {
          args[optionsIndex] = { comment };
        } else if (typeof options === 'object' && options.comment == null) {
          args[optionsIndex] = { ...options, comment };
        }
      }
      return original.apply(this, args);
    };
  }

  propagationInstalled = true;
}

async function traceDocumentDbOperation(operationName, namespace, operation) {
  if (!tracingEnabled) {
    return operation();
  }

  return tracer.startActiveSpan(
    `mongoose.${operationName}`,
    {
      kind: SpanKind.CLIENT,
      attributes: {
        'db.system.name': 'documentdb',
        'db.operation.name': operationName,
        'db.namespace': namespace,
      },
    },
    async (span) => {
      try {
        const result = await operation();
        span.setStatus({ code: SpanStatusCode.OK });
        return result;
      } catch (err) {
        span.recordException(err);
        span.setStatus({
          code: SpanStatusCode.ERROR,
          message: err && err.message ? err.message : String(err),
        });
        throw err;
      } finally {
        span.end();
      }
    }
  );
}

async function shutdownTelemetry() {
  if (sdk) {
    await sdk.shutdown();
  }
}

module.exports = {
  installMongooseTracePropagation,
  shutdownTelemetry,
  traceDocumentDbOperation,
  tracingEnabled,
};
