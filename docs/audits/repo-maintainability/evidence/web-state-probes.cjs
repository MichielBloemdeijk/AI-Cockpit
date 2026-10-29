/* No-network audit reproductions. These load the actual TypeScript implementations
 * with fake platform dependencies; they are diagnostic proofs, not browser/ONNX
 * integration tests. Run: node docs/audits/repo-maintainability/evidence/web-state-probes.cjs
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const ts = require(path.join(root, 'frontend/node_modules/typescript'));
const results = [];
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };
function load(file, mocks) {
  // import.meta is used only by the unused default browser Worker factory.
  const source = fs.readFileSync(path.join(root, file), 'utf8').replaceAll('import.meta.url', '"file:///unused-audit-worker"');
  const js = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', js)(name => {
    if (name in mocks) return mocks[name];
    throw new Error(`Unexpected dependency ${name} in ${file}`);
  }, module, module.exports);
  return module.exports;
}

async function wakeCleanupSupersession() {
  const { JarvisWakeRuntime } = load('frontend/lib/jarvis-wake-runtime.ts', {
    './jarvis-wake-protocol': { JARVIS_WAKE_SAMPLE_RATE: 16000 },
    './jarvis-activation-audio': {},
  });
  const oldDestroy = deferred();
  const workers = [];
  const runtime = new JarvisWakeRuntime({ onWake() {} }, {
    getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
    createWorker: () => { const worker = { postMessage() {}, terminate() {} }; workers.push(worker); return worker; },
    createAudioCapture: async () => {
      const first = workers.length === 1;
      return { pause() {}, resume: async () => {}, destroy: () => first ? oldDestroy.promise : Promise.resolve() };
    },
  });
  await runtime.start({});
  workers[0].onmessage({ data: { type: 'ready' } });
  const oldFailure = workers[0].onerror({ message: 'old worker failed' });
  await runtime.start({});
  workers[1].onmessage({ data: { type: 'ready' } });
  const beforeOldCleanup = runtime.current().status;
  oldDestroy.resolve();
  await oldFailure;
  const afterOldCleanup = runtime.current().status;
  assert.equal(beforeOldCleanup, 'ready');
  assert.equal(afterOldCleanup, 'failed');
  results.push({ probe: 'wake cleanup supersession', beforeOldCleanup, afterOldCleanup, defectReproduced: true });
  await runtime.stop();
}

async function browserStopOrdering() {
  const { JarvisLiveController } = load('frontend/lib/jarvis-live.ts', {
    './api': {}, './jarvis-playback-evidence': {},
  });
  const backendClose = deferred();
  let stopped = false;
  const track = { stop() { stopped = true; } };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  let channel;
  const peer = {
    connectionState: 'connected', iceConnectionState: 'connected', iceGatheringState: 'complete',
    createDataChannel() { return channel = { readyState: 'open', send() {}, close() {} }; },
    createOffer: async () => ({ sdp: 'offer' }), setLocalDescription: async () => {},
    setRemoteDescription: async () => {}, addEventListener() {}, removeEventListener() {}, addTrack() {}, close() {},
  };
  const context = { state: 'running', currentTime: 0,
    createMediaStreamDestination: () => ({ stream }),
    createMediaStreamSource: () => ({ connect() {}, disconnect() {} }),
    close: async () => {},
  };
  const controller = new JarvisLiveController('owner', {}, {
    createCall: async () => ({ session: { id: 's', generation: 1 }, sdp_answer: 'answer', provider_capabilities: {} }),
    getBackendState: async () => ({ session: { id: 's', generation: 1, lifecycle_state: 'ready' } }),
    getUserMedia: async () => stream, closeCall: () => backendClose.promise,
    peerConnection: () => peer, audioContext: () => context,
    setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout,
  });
  await controller.start();
  // Keep the probe focused on HTTP cleanup ordering, avoiding the separate
  // bounded data-channel close acknowledgement wait.
  channel.readyState = 'closed';
  const stopping = controller.stop();
  await flush();
  const stoppedWhileBackendClosePending = stopped;
  backendClose.resolve();
  await stopping;
  assert.equal(stoppedWhileBackendClosePending, false);
  assert.equal(stopped, true);
  results.push({ probe: 'browser Stop audio ordering', stoppedWhileBackendClosePending, stoppedAfterBackendClose: stopped, defectReproduced: true });
}

async function partialOnnxInitialization() {
  const sessions = [];
  const { HeyBuddyOnnxInference } = load('frontend/lib/jarvis-hey-buddy-inference.ts', {
    'onnxruntime-web': { env: { wasm: {} }, Tensor: class {}, InferenceSession: {
      create: async url => {
        if (url === 'broken-wake') throw new Error('model missing');
        const session = { url, released: false, async release() { this.released = true; } };
        sessions.push(session); return session;
      },
    } },
  });
  await assert.rejects(HeyBuddyOnnxInference.create({
    vadModelUrl: 'vad', spectrogramModelUrl: 'mel', embeddingModelUrl: 'embedding', wakeModelUrl: 'broken-wake', wasmBaseUrl: '/',
  }), /model missing/);
  assert.equal(sessions.length, 3);
  assert.equal(sessions.filter(session => session.released).length, 0);
  results.push({ probe: 'partial ONNX model initialization', createdSessions: sessions.length, releasedSessions: 0, defectReproduced: true });
}

(async () => {
  await wakeCleanupSupersession();
  await browserStopOrdering();
  await partialOnnxInitialization();
  console.log(JSON.stringify(results, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
