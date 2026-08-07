'use strict';

const {
  installMongooseTracePropagation,
  shutdownTelemetry,
  traceDocumentDbOperation,
  tracingEnabled,
} = require('./telemetry');
const express = require('express');
const mongoose = require('mongoose');

const { connect } = require('./db');
const Book = require('./models/book');

installMongooseTracePropagation(mongoose);

const app = express();
app.use(express.json());

const PORT = Number(process.env.PORT || 3000);
const DB_NAME = process.env.MONGO_DB || 'mongoose_demo';
const BOOK_UPDATE_FIELDS = [
  'title',
  'author',
  'genres',
  'pages',
  'published',
  'inStock',
  'rating',
];

function traceBookOperation(operationName, operation) {
  return traceDocumentDbOperation(operationName, DB_NAME, operation);
}

function parseAuthor(value) {
  if (value === undefined) return undefined;
  if (typeof value !== 'string') return null;

  const author = value.trim();
  if (author.length === 0 || author.length > 100) return null;
  return author;
}

function pickBookUpdates(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return null;

  const changes = {};
  for (const field of BOOK_UPDATE_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(body, field)) {
      changes[field] = body[field];
    }
  }
  return changes;
}

// Liveness/readiness probe. Returns 200 only when the Mongoose connection is up.
app.get('/health', (req, res) => {
  const state = mongoose.connection.readyState; // 1 = connected
  if (state === 1) {
    return res.json({ status: 'healthy', db: 'connected' });
  }
  return res.status(503).json({ status: 'unhealthy', db: mongoose.STATES[state] });
});

// Create a book.
app.post('/books', async (req, res) => {
  try {
    const book = await traceBookOperation('insert', () => Book.create(req.body));
    res.status(201).json(book);
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

// List books, optionally filtered by author.
app.get('/books', async (req, res) => {
  try {
    const author = parseAuthor(req.query.author);
    if (author === null) {
      return res.status(400).json({
        error: 'author must be a non-empty string up to 100 characters',
      });
    }
    const filter = author === undefined ? {} : { author };
    const books = await traceBookOperation('find', () =>
      Book.find(filter).sort({ createdAt: -1 }).limit(100).lean()
    );
    res.json({ count: books.length, books });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// Fetch a single book by id.
app.get('/books/:id', async (req, res) => {
  try {
    const book = await traceBookOperation('findOne', () =>
      Book.findById(req.params.id).lean()
    );
    if (!book) return res.status(404).json({ error: 'not found' });
    res.json(book);
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

// Update a book.
app.patch('/books/:id', async (req, res) => {
  try {
    const changes = pickBookUpdates(req.body);
    if (!changes || Object.keys(changes).length === 0) {
      return res.status(400).json({ error: 'no supported book fields provided' });
    }
    const book = await traceBookOperation('findOneAndUpdate', () =>
      Book.findByIdAndUpdate(req.params.id, { $set: changes }, {
        new: true,
        runValidators: true,
      }).lean()
    );
    if (!book) return res.status(404).json({ error: 'not found' });
    res.json(book);
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

// Delete a book.
app.delete('/books/:id', async (req, res) => {
  try {
    const result = await traceBookOperation('findOneAndDelete', () =>
      Book.findByIdAndDelete(req.params.id).lean()
    );
    if (!result) return res.status(404).json({ error: 'not found' });
    res.status(204).end();
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

// Simple aggregation: count books per genre.
app.get('/stats/genres', async (req, res) => {
  try {
    const stats = await traceBookOperation('aggregate', () =>
      Book.aggregate([
        { $unwind: '$genres' },
        { $group: { _id: '$genres', count: { $sum: 1 } } },
        { $sort: { count: -1 } },
      ])
    );
    res.json(stats);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

async function main() {
  await connect();
  console.log('Connected to DocumentDB via Mongoose');
  if (tracingEnabled) {
    console.log('OpenTelemetry tracing enabled');
  }
  const server = app.listen(PORT, () =>
    console.log(`Mongoose demo API listening on :${PORT}`)
  );

  let shuttingDown = false;
  const shutdown = (signal) => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    console.log(`Received ${signal}, shutting down...`);
    server.close(() => {
      Promise.all([mongoose.connection.close(false), shutdownTelemetry()])
        .then(() => process.exit(0))
        .catch((err) => {
          console.error('Shutdown error:', err.message);
          process.exit(1);
        });
    });
  };
  ['SIGTERM', 'SIGINT'].forEach((sig) => process.on(sig, () => shutdown(sig)));
}

main().catch(async (err) => {
  console.error('Fatal startup error:', err.message);
  await shutdownTelemetry();
  process.exit(1);
});
