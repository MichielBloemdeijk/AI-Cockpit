/* Cockpit app SDK (cockpit-app.v1).
 *
 * Load it from an app's ui/index.html with
 *   <script src="/api/app-host/_sdk/cockpit-app.js"></script>
 * App UIs run in a sandboxed frame without network access. Every call becomes a
 * message to the Cockpit page hosting the frame, which performs it with a token
 * scoped to this app and posts the result back.
 *
 *   await cockpit.ready                       -> { slug, title, version }
 *   await cockpit.data.get(collection, key)   -> { key, value, revision } | null
 *   await cockpit.data.put(collection, key, value, { expectedRevision })
 *   await cockpit.data.delete(collection, key, { expectedRevision })
 *   await cockpit.data.query(collection, { where, order, limit }) -> [records]
 *
 * Failed calls reject with an Error whose `status` is the HTTP status (409 means
 * the record changed since it was read).
 */
(function () {
  "use strict";
  var pending = new Map();
  var nextId = 1;
  var resolveReady;
  var ready = new Promise(function (resolve) { resolveReady = resolve; });

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent || !event.data || typeof event.data !== "object") return;
    var message = event.data;
    if (message.cockpit === "hello") {
      resolveReady({ slug: message.slug, title: message.title, version: message.version });
      return;
    }
    if (message.cockpit !== "response") return;
    var waiter = pending.get(message.id);
    if (!waiter) return;
    pending.delete(message.id);
    if (message.ok) {
      waiter.resolve(message.result);
    } else {
      var error = new Error(message.error || "Request failed");
      error.status = message.status || 0;
      waiter.reject(error);
    }
  });

  function call(method, params) {
    return ready.then(function () {
      return new Promise(function (resolve, reject) {
        var id = nextId++;
        pending.set(id, { resolve: resolve, reject: reject });
        window.parent.postMessage({ cockpit: "request", id: id, method: method, params: params || {} }, "*");
      });
    });
  }

  window.cockpit = {
    ready: ready,
    data: {
      get: function (collection, key) { return call("data.get", { collection: collection, key: key }); },
      put: function (collection, key, value, options) {
        return call("data.put", { collection: collection, key: key, value: value,
          expectedRevision: options && options.expectedRevision });
      },
      delete: function (collection, key, options) {
        return call("data.delete", { collection: collection, key: key,
          expectedRevision: options && options.expectedRevision });
      },
      query: function (collection, options) {
        return call("data.query", { collection: collection, where: options && options.where,
          order: options && options.order, limit: options && options.limit });
      },
    },
  };
  // Uncaught errors are reported to Cockpit so Jarvis can see and repair them.
  var reported = 0;
  function report(message, detail) {
    if (reported++ >= 20) return;
    window.parent.postMessage({ cockpit: "error", message: String(message).slice(0, 1000),
      detail: String(detail || "").slice(0, 4000) }, "*");
  }
  window.addEventListener("error", function (event) {
    report(event.message, event.error && event.error.stack || (event.filename + ":" + event.lineno));
  });
  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason;
    report("Unhandled rejection: " + (reason && reason.message || reason), reason && reason.stack);
  });
  window.parent.postMessage({ cockpit: "ready" }, "*");
})();
