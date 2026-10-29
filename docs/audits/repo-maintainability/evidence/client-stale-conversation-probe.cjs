/*
 * Audit-only, no-network reproduction of overlapping conversation detail loads.
 * Run from any directory with the existing frontend dependencies installed:
 *   node docs/audits/repo-maintainability/evidence/client-stale-conversation-probe.cjs
 *
 * This executes the current loader implementation with fake React state setters
 * and API promises. It does not mount React, exercise effects or prove browser UI
 * behavior. No source, app data or network state is changed.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../../../..");
const ts = require(path.join(root, "frontend/node_modules/typescript"));
const sourcePath = path.join(root, "frontend/lib/use-conversation-detail.ts");
const pending = new Map();
const states = [];

function requireMock(name) {
  if (name === "react") return {
    useCallback: callback => callback,
    useEffect: () => {},
    useState: initial => {
      const index = states.length;
      states.push(initial);
      return [initial, next => {
        states[index] = typeof next === "function" ? next(states[index]) : next;
      }];
    },
  };
  if (name === "./api") return {
    getConversation: id => new Promise(resolve => pending.set(`${id}:detail`, resolve)),
    getConversationEvents: id => new Promise(resolve => pending.set(`${id}:events`, resolve)),
  };
  if (name === "./conversation-feed") return {
    buildCommittedConversationMessages: detail => [{ id: detail.id }],
    buildLiveConversationMessage: () => null,
  };
  if (name === "./conversation-feed-state") return {
    mergeConversationFeed: (_current, messages) => messages,
  };
  if (name === "./agent-stream-state") return {
    buildFallbackAgentStatus: () => null,
    findLatestAgentStatus: () => null,
  };
  throw new Error(`Unexpected dependency: ${name}`);
}

const javascript = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loadedModule = { exports: {} };
new Function("require", "module", "exports", javascript)(requireMock, loadedModule, loadedModule.exports);

const hook = loadedModule.exports.useConversationDetail({
  activeConversationId: "A",
  activeBranchKey: "main",
  activeStreamRunIdRef: { current: null },
});

function finish(id) {
  pending.get(`${id}:detail`)({ id, latest_run_status: "completed" });
  pending.get(`${id}:events`)({ events: [], artifacts: [], timeline_items: [] });
}

async function main() {
  const older = hook.loadConversation("A", "main");
  const newer = hook.loadConversation("B", "main");
  finish("B");
  await newer;
  const afterNewer = { activeConversation: states[0].id, messages: states[3] };
  assert.equal(afterNewer.activeConversation, "B");
  finish("A");
  await older;
  const afterOlder = { activeConversation: states[0].id, messages: states[3] };
  const defectReproduced = afterOlder.activeConversation === "A";
  console.log(JSON.stringify({ source: path.relative(root, sourcePath), afterNewer, afterOlder, defectReproduced }, null, 2));
  assert.equal(defectReproduced, true, "Audit baseline changed: the original stale-write reproduction no longer holds; reassess this probe.");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
