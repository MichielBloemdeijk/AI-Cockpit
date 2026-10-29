/* Does the timeline feed path keep what the legacy reconstruction attaches?
 * Loads the real frontend/lib/conversation-feed.ts (and its relative imports)
 * with the TypeScript compiler; no browser, network or backend.
 * Run: node docs/audits/repo-maintainability/evidence/feed-parity-probe.cjs
 */
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../../..');
const ts = require(path.join(root, 'frontend/node_modules/typescript'));
const cache = new Map();

function load(file) {
  const resolved = file.endsWith('.ts') ? file : `${file}.ts`;
  if (cache.has(resolved)) return cache.get(resolved).exports;
  const module = { exports: {} };
  cache.set(resolved, module);
  const js = ts.transpileModule(fs.readFileSync(resolved, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  new Function('require', 'module', 'exports', js)((name) => {
    if (name.startsWith('.')) return load(path.join(path.dirname(resolved), name));
    throw new Error(`Unexpected dependency ${name} in ${resolved}`);
  }, module, module.exports);
  return module.exports;
}

const { buildCommittedConversationMessages } = load(path.join(root, 'frontend/lib/conversation-feed.ts'));
const at = '2026-10-29T12:00:00Z';
const usage = { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15, cost: 0.002 };
const detail = {
  id: 'c1', messages: [
    { id: 'u1', role: 'user', content: 'question', run_id: 'r1', created_at: at, is_final: true },
    { id: 'a1', role: 'assistant', content: 'synthesis', run_id: 'r1', created_at: at, is_final: true,
      source_event_id: 'e2', source_task_id: 't1' },
  ],
};
const artifacts = [
  { id: 'x1', run_id: 'r1', artifact_type: 'council.model.response', content_json: { model: 'm1', content: 'one', usage } },
  { id: 'x2', run_id: 'r1', artifact_type: 'council.synthesis.response', content_json: { model: 'm2', content: 'synthesis', usage } },
];
const events = [{ id: 'e2', run_id: 'r1', sequence: 2, event_type: 'llm.request.completed', payload_json: { usage }, created_at: at }];
const timeline = [
  { item_id: 'user:u1', run_id: 'r1', sequence: 1, role: 'user', kind: 'message', content: 'question', created_at: at, badges: [], sections: [] },
  { item_id: 'assistant:a1', run_id: 'r1', sequence: 3, role: 'assistant', kind: 'message', content: 'synthesis', created_at: at, badges: [], sections: [] },
];

function summarize(messages) {
  const assistant = messages.find((message) => message.role === 'assistant') || {};
  const user = messages.find((message) => message.role === 'user') || {};
  return {
    council_panel: Boolean(assistant.councilData),
    usage_shown: Boolean(assistant.usage),
    task_link: Boolean(assistant.taskId),
    user_branchable_flag: user.branchable,
  };
}

const legacy = summarize(buildCommittedConversationMessages(detail, artifacts, events, []));
const current = summarize(buildCommittedConversationMessages(detail, artifacts, events, timeline));
console.log(JSON.stringify({ legacy_path: legacy, timeline_path: current,
  regression: JSON.stringify(legacy) !== JSON.stringify(current) }, null, 2));
