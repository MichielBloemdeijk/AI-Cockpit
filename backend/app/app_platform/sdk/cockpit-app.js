/* Cockpit app SDK (cockpit-app.v1).
 *
 * Load it from an app's ui/index.html with
 *   <script src="/api/app-host/_sdk/cockpit-app.js"></script>
 * App UIs run in a sandboxed frame without network access. Every call becomes a
 * message to the Cockpit page hosting the frame, which performs it with a token
 * scoped to this app and posts the result back.
 *
 *   await cockpit.ready                       -> { slug, title, version, target }
 *   cockpit.onTarget(function (target) {...}) -- Cockpit opened another record
 *   cockpit.viewing(collection, key)          -- tell Jarvis which record is shown
 *   await cockpit.data.get(collection, key)   -> { key, value, revision } | null
 *   await cockpit.data.put(collection, key, value, { expectedRevision })
 *   await cockpit.data.delete(collection, key, { expectedRevision })
 *   await cockpit.data.query(collection, { where, order, limit }) -> [records]
 *   await cockpit.files.list({ prefix })      -> [{ name, size, content_type, updated_at }]
 *   await cockpit.files.put(name, fileOrBlobOrText, { contentType }) -> { name, size, ... }
 *   await cockpit.files.get(name)             -> Blob | null  (show it with URL.createObjectURL)
 *   await cockpit.files.delete(name)          -> true | false
 *   await cockpit.functions.call(name, input) -> the function's output (http-triggered functions only)
 *
 * Another app's collection is "<app>.<collection>" ("recipes.recipe") and needs
 * a grant in the manifest (grants.read, or grants.write to change it); Cockpit's
 * own data is read-only as "system.tasks", "system.results" and
 * "system.conversations". Files need a "files" section in the manifest; names are paths like
 * "photos/a.jpg". A failed function call rejects with the function's error.
 *
 * `target` is { collection, key } when Cockpit opened the app on a record (a
 * notification, a link or Jarvis), otherwise null. Show that record; call
 * cockpit.viewing whenever the user opens another one, so "this one" resolves.
 *
 * Failed calls reject with an Error whose `status` is the HTTP status (409 means
 * the record changed since it was read).
 */
(function () {
  "use strict";
  var pending = new Map();
  var targetListeners = [];
  var nextId = 1;
  var resolveReady;
  var ready = new Promise(function (resolve) { resolveReady = resolve; });

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent || !event.data || typeof event.data !== "object") return;
    var message = event.data;
    if (message.cockpit === "hello") {
      resolveReady({ slug: message.slug, title: message.title, version: message.version,
        target: message.target || null });
      return;
    }
    if (message.cockpit === "target") {
      targetListeners.forEach(function (listener) { listener(message.target || null); });
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
    onTarget: function (listener) { targetListeners.push(listener); },
    viewing: function (collection, key) {
      window.parent.postMessage({ cockpit: "viewing", collection: String(collection), key: String(key) }, "*");
    },
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
    files: {
      list: function (options) { return call("files.list", { prefix: options && options.prefix }); },
      put: function (name, data, options) {
        var contentType = options && options.contentType;
        var blob = data instanceof Blob ? data : new Blob([data], { type: contentType || "text/plain" });
        return call("files.put", { name: name, data: blob, contentType: contentType || blob.type || null });
      },
      get: function (name) { return call("files.get", { name: name }); },
      delete: function (name) { return call("files.delete", { name: name }); },
    },
    functions: {
      call: function (name, input) {
        return call("functions.call", { name: name, input: input === undefined ? null : input });
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
