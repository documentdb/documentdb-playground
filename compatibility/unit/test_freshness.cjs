// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../dashboard/freshness.js"), "utf8");
const day = 86400000;
const tested = Date.UTC(2024, 0, 1);

function status(previousState, conclusiveAt = new Date(tested).toISOString(), textContent = previousState) {
    return { dataset: { conclusiveAt, freshnessDays: "7", previousState }, textContent, className: textContent };
}

function load(element, now) {
    let interval;
    vm.runInNewContext(source, {
        document: { querySelectorAll: selector => (selector === "[data-compatibility-status]" ? [element] : []) },
        Date: { parse: Date.parse, now: () => now },
        setInterval: callback => { interval = callback; },
    });
    return next => { now = next; interval(); };
}

test("the freshness boundary stays fresh until a later timer tick", () => {
    const element = status("Working");
    const tick = load(element, tested + 7 * day);
    assert.equal(element.textContent, "Working");
    tick(tested + 7 * day + 1);
    assert.deepEqual([element.textContent, element.className], ["Stale (last: Working)", "Stale"]);
});

test("untested entries and server-side stale classifications are not refreshed", () => {
    const untested = status("Not tested", "");
    const stale = status("Working", undefined, "Stale");
    load(untested, tested + 8 * day);
    load(stale, tested + day);
    assert.deepEqual([untested.textContent, stale.textContent], ["Not tested", "Stale"]);
});
