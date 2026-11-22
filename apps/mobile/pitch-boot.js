/* Boot script for offline sandboxed Magnm-Pitch in Magnm Mobile. */
(function () {
  var MAX_PAYLOAD_JSON_CHARS = 2 * 1024 * 1024;
  var MAX_PDF_BYTES = 10 * 1024 * 1024;
  var booted = false;

  var ancestorOrigin =
    window.location &&
    window.location.ancestorOrigins &&
    window.location.ancestorOrigins.length > 0
      ? window.location.ancestorOrigins[0]
      : "";
  var parentOrigin = ancestorOrigin || "*";

  function postToParent(msg) {
    if (!window.parent || window.parent === window) return;
    try {
      window.parent.postMessage(msg, parentOrigin);
    } catch (_err) {
      // Ignore postMessage delivery errors when parent is unavailable.
    }
  }

  function installSandboxGuards() {
    if (window.history && typeof window.history.replaceState === "function") {
      var origReplaceState = window.history.replaceState;
      window.history.replaceState = function () {
        try {
          return origReplaceState.apply(window.history, arguments);
        } catch (_err) {
          return undefined;
        }
      };
    }

    if (window.jspdf && typeof window.jspdf.jsPDF === "function") {
      var OrigJsPdf = window.jspdf.jsPDF;
      // jsPDF puts save on every instance, so overriding the prototype would never run.
      var SharingJsPdf = function () {
        var args = [null].concat(Array.prototype.slice.call(arguments));
        var doc = new (Function.prototype.bind.apply(OrigJsPdf, args))();
        doc.save = sharePdfWithApp;
        return doc;
      };
      SharingJsPdf.prototype = OrigJsPdf.prototype;
      Object.keys(OrigJsPdf).forEach(function (key) {
        SharingJsPdf[key] = OrigJsPdf[key];
      });
      window.jspdf.jsPDF = SharingJsPdf;
    }
  }

  function sharePdfWithApp(filename) {
    var bytes = new Uint8Array(this.output("arraybuffer"));
    var tooBig = bytes.byteLength > MAX_PDF_BYTES;
    if (bytes.byteLength > 0 && !tooBig) {
      var cleanName =
        typeof filename === "string" && filename.trim() ? filename.trim().slice(0, 180) : "magnm-estimate.pdf";
      postToParent({ type: "magnm:pitch-pdf-bytes", filename: cleanName, bytes: bytes });
    }
    // Pitch says "downloaded" right after save returns, which isn't what happens on a phone.
    setTimeout(function () {
      var status = document.getElementById("status");
      if (status) status.textContent = tooBig ? "PDF exceeds 10 MB limit." : "Quotation PDF ready to share.";
    }, 0);
    return this;
  }

  function loadScript(src, onload) {
    var script = document.createElement("script");
    script.src = src;
    script.async = false;
    script.onload = onload;
    document.body.appendChild(script);
  }

  function mountPayloadAndBoot(payload) {
    if (booted) return;
    var serialized = JSON.stringify(payload);
    if (!serialized || serialized.length > MAX_PAYLOAD_JSON_CHARS) {
      return;
    }
    booted = true;

    var existing = document.getElementById("magnm-pitch-data");
    if (existing && existing.parentNode) {
      existing.parentNode.removeChild(existing);
    }
    var dataEl = document.createElement("script");
    dataEl.type = "application/json";
    dataEl.id = "magnm-pitch-data";
    dataEl.textContent = serialized;
    document.body.appendChild(dataEl);

    installSandboxGuards();

    loadScript("app.js", function () {
      loadScript("magnm-host.js", function () {
        if (document.readyState !== "loading" && typeof window.init === "function") {
          window.init();
        }
      });
    });
  }

  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      postToParent({ type: "magnm:pitch-exit-present" });
    }
  });

  // The sandbox has no allow-forms, so a submit button never fires submit. Raise the event ourselves:
  // Pitch's handler builds the PDF, and nothing can be posted anywhere.
  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest ? event.target.closest("button") : null;
    if (!button || button.type !== "submit" || !button.form || event.defaultPrevented) return;
    event.preventDefault();
    button.form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent) return;
    if (ancestorOrigin && event.origin !== ancestorOrigin) return;
    if (!ancestorOrigin && event.origin && event.origin !== "null") {
      parentOrigin = event.origin;
    }
    var msg = event.data;
    if (!msg || typeof msg !== "object" || Array.isArray(msg)) return;
    if (msg.type === "magnm:pitch-payload") {
      if (!msg.payload || typeof msg.payload !== "object" || Array.isArray(msg.payload)) {
        return;
      }
      mountPayloadAndBoot(msg.payload);
    }
  });

  postToParent({ type: "magnm:pitch-ready" });
})();
