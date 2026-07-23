#!/usr/bin/env node

import fs from "node:fs";

const [path] = process.argv.slice(2);
if (!path) {
  console.error("usage: validate_node_junit.mjs PATH");
  process.exit(2);
}

let stat;
try {
  stat = fs.lstatSync(path);
} catch (error) {
  console.error(`Node JUnit quality gate failed: ${error.message}`);
  process.exit(1);
}
if (!stat.isFile() || stat.isSymbolicLink() || stat.size <= 0 || stat.size > 100 * 1024 * 1024) {
  console.error("Node JUnit quality gate failed: evidence must be one bounded regular file");
  process.exit(1);
}
const report = fs.readFileSync(path, "utf8");
if (/<!DOCTYPE|<!ENTITY/i.test(report) || !report.includes("</testsuites>")) {
  console.error("Node JUnit quality gate failed: unsafe or malformed XML envelope");
  process.exit(1);
}

const names = ["tests", "suites", "pass", "fail", "cancelled", "skipped", "todo"];
const counts = {};
for (const name of names) {
  const matches = [...report.matchAll(new RegExp(`<!-- ${name} (\\d+) -->`, "g"))];
  if (matches.length !== 1) {
    console.error(`Node JUnit quality gate failed: expected one ${name} summary`);
    process.exit(1);
  }
  counts[name] = Number.parseInt(matches[0][1], 10);
}
const cases = (report.match(/<testcase\b/g) ?? []).length;
if (counts.tests <= 0 || cases !== counts.tests || counts.pass !== counts.tests) {
  console.error(
    `Node JUnit quality gate failed: inconsistent counts tests=${counts.tests}, ` +
      `pass=${counts.pass}, cases=${cases}`,
  );
  process.exit(1);
}
if (counts.fail || counts.cancelled || counts.skipped || counts.todo) {
  console.error(
    "Node JUnit quality gate failed: policy requires zero failed, cancelled, skipped, or todo " +
      `tests; observed ${JSON.stringify(counts)}`,
  );
  process.exit(1);
}
console.log(
  JSON.stringify({
    schema_version: "finite-node-junit-gate/v1",
    status: "passed",
    testcases: cases,
    failed: 0,
    cancelled: 0,
    skipped: 0,
    todo: 0,
  }),
);
