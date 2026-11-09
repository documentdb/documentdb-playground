// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

"use strict";

const assert = require("node:assert/strict");
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { classify, collect, junit, runScenario } = require("../report.cjs");
const reporter = path.resolve(__dirname, "../report.cjs");

function execute(source) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "compat-node-unit-"));
    const profile = path.join(directory, "profile.cjs");
    fs.writeFileSync(profile, source);
    try {
        return spawnSync(process.execPath, ["-e", `
            const { run } = require("node:test");
            const { collect } = require(${JSON.stringify(reporter)});
            collect(run({ files: [process.argv[1]], concurrency: 1, isolation: "none" }))
                .then(tests => {
                    process.stdout.write(JSON.stringify(tests));
                    process.exitCode = tests.some(test => ["failed", "error"].includes(test.outcome)) ? 1 : 0;
                })
                .catch(error => { console.error(error); process.exitCode = 2; });
        `, profile], { encoding: "utf8", timeout: 10000, env: { ...process.env, NODE_TEST_CONTEXT: undefined } });
    } finally {
        fs.rmSync(directory, { recursive: true });
    }
}

test("registered scenarios are gated and classified by the production reporter", () => {
    const result = execute(`
        const { register } = require(${JSON.stringify(reporter)});
        const fixture = () => ({ close() {} });
        const server = code => Object.assign(new Error("Server"), { name: "MongoServerError", code });
        process.env.COMPATIBILITY_ACTIVE = "0";
        process.env.COMPATIBILITY_DEMONSTRATION = "0";
        register({ test_inactive() {} }, fixture);
        process.env.COMPATIBILITY_ACTIVE = "1";
        register({ test_pass() {}, test_operation() { throw server(115); }, test_timeout() { throw server(50); } }, fixture);
        register({ test_hidden_demonstration() {} }, fixture, true);
        process.env.COMPATIBILITY_DEMONSTRATION = "1";
        register({ test_demonstration() {} }, fixture, true);
    `);
    assert.equal(result.status, 1, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout).map(record => [record.id, record.outcome]), [
        ["test_inactive", "skipped"],
        ["test_pass", "passed"],
        ["test_operation", "failed"],
        ["test_timeout", "error"],
        ["test_hidden_demonstration", "skipped"],
        ["test_demonstration", "passed"],
    ]);
});

for (const [name, source, message] of [
    ["duplicate scenario names", "const { test } = require('node:test'); test('test_a', () => {}); test('test_a', () => {});", /Duplicate scenario/],
    ["collection errors", "throw new Error('Collection failed');", /Collection failed/],
    ["late asynchronous errors", "require('node:test').test('test_a', () => { setImmediate(() => { throw new Error('Late failure'); }); });", /Late failure/],
]) {
    test(`${name} cannot pass`, () => {
        const result = execute(source);
        assert.equal(result.status, 2, result.stdout + result.stderr);
        assert.match(result.stderr, message);
    });
}

test("missing runner summary cannot pass", async () => {
    await assert.rejects(collect([
        { type: "test:pass", data: { name: "test_profile", details: {} } },
    ]), /did not complete successfully/);
});

test("fixture errors cannot mask or become compatibility failures", async () => {
    const setup = () => { throw Object.assign(new Error("Setup failed"), { name: "MongoServerError" }); };
    const cleanup = () => ({ close() { throw new Error("Cleanup failed"); } });
    await assert.rejects(runScenario(setup, () => {}), error => classify(error) === "error");
    await assert.rejects(runScenario(cleanup, () => {}), error => classify(error) === "error");
    await assert.rejects(runScenario(cleanup, () => assert.fail("Mismatch")), error => classify(error) === "failed");
    await assert.rejects(runScenario(() => ({ close() {} }), () => { throw undefined; }));
});

test("JUnit preserves outcomes and escapes diagnostics", () => {
    const output = junit([
        { id: "test_pass", outcome: "passed", message: "" },
        { id: "test_failure", outcome: "failed", message: "<mismatch> & \"value\"" },
        { id: "test_error", outcome: "error", message: "Unavailable" },
        { id: "test_skip", outcome: "skipped", message: "Skipped" },
    ], "driver & integration");
    assert.match(output, /name="driver &amp; integration" tests="4" failures="1" errors="1" skipped="1"/);
    assert.match(output, /<failure>&lt;mismatch&gt; &amp; &quot;value&quot;<\/failure>/);
});
