// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

"use strict";

const fs = require("node:fs");
const { spawnSync } = require("node:child_process");

const [name, version] = process.argv.slice(1);
if (!/^[a-z][a-z0-9_-]{0,39}$/.test(name) || !/^[0-9]+\.[0-9]+\.[0-9]+$/.test(version)) {
  throw new Error("Invalid npm candidate identity");
}
const directory = fs.mkdtempSync("/tmp/compatibility-npm-");
const manifest = {
  name: "documentdb-compatibility-candidate",
  version: "1.0.0",
  private: true,
  dependencies: { [name]: version },
};
fs.writeFileSync(`${directory}/package.json`, JSON.stringify(manifest));
const result = spawnSync(
  "npm",
  [
    "install", "--package-lock-only", "--ignore-scripts", "--save-exact",
    "--engine-strict", "--omit=optional", "--no-audit", "--no-fund",
    "--registry=https://registry.npmjs.org", "--cache=/tmp/npm-cache",
    "--userconfig=/dev/null",
  ],
  { cwd: directory, encoding: "utf8", timeout: 120000, maxBuffer: 1024 * 1024 },
);
if (result.error || result.status !== 0) {
  console.error(result.error?.message || (result.stderr || result.stdout).slice(-4000));
  process.exit(1);
}
const contents = fs.readFileSync(`${directory}/package-lock.json`);
if (contents.length > 64 * 1024) {
  throw new Error("npm candidate lockfile exceeded its size limit");
}
process.stdout.write(JSON.stringify({ manifest, lockfile: JSON.parse(contents) }));
