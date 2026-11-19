/* Studio host adapter for vendored Magnm-Pitch. Runs after app.js and before DOMContentLoaded init. */
(function () {
  var dataEl = document.getElementById("magnm-pitch-data");
  if (!dataEl) return;

  var data;
  try {
    data = JSON.parse(dataEl.textContent || "{}");
  } catch (e) {
    return;
  }
  if (!data || typeof data !== "object") return;

  // 1. Override base item prices for existing items only (non-negative integers).
  if (data.prices && typeof data.prices === "object" && typeof ITEMS !== "undefined") {
    var priceKeys = Object.keys(data.prices);
    for (var i = 0; i < priceKeys.length; i++) {
      var id = priceKeys[i];
      var price = data.prices[id];
      if (
        Object.prototype.hasOwnProperty.call(ITEMS, id) &&
        typeof price === "number" &&
        isFinite(price) &&
        price >= 0 &&
        Math.floor(price) === price
      ) {
        ITEMS[id].p = price;
      }
    }
  }

  // 2. Register custom items (ids must start with custom_, group must exist in GROUPS).
  if (Array.isArray(data.custom_items) && typeof ITEMS !== "undefined" && typeof GROUPS !== "undefined") {
    for (var j = 0; j < data.custom_items.length; j++) {
      var item = data.custom_items[j];
      if (
        item &&
        typeof item === "object" &&
        typeof item.id === "string" &&
        item.id.indexOf("custom_") === 0 &&
        GROUPS.indexOf(item.g) !== -1 &&
        typeof item.n === "string" &&
        typeof item.d === "string" &&
        typeof item.p === "number" &&
        isFinite(item.p) &&
        item.p >= 0 &&
        Math.floor(item.p) === item.p
      ) {
        ITEMS[item.id] = {
          g: item.g,
          n: item.n,
          d: item.d,
          p: item.p,
          r: Array.isArray(item.r) ? item.r.slice() : []
        };
      }
    }
  }

  // 3. Disable Pitch's built-in /admin route inside the Studio host.
  var origParseRoute = typeof window.parseRoute === "function" ? window.parseRoute : null;
  if (origParseRoute) {
    var currentRoute = origParseRoute();
    if (currentRoute && currentRoute.view === "admin") {
      var cleanParams = new URLSearchParams(window.location.search || "");
      cleanParams.delete("admin");
      if (!cleanParams.get("client")) {
        cleanParams.set("client", "general");
      }
      var basePath = window.location.pathname || "/pitch/";
      if (basePath === "/admin" || basePath.indexOf("/admin/") === 0) {
        basePath = "/pitch/";
      }
      var cleanQs = cleanParams.toString();
      window.history.replaceState(null, "", basePath + (cleanQs ? "?" + cleanQs : ""));
    }

    window.parseRoute = function () {
      var r = origParseRoute();
      if (r && r.view === "admin") {
        return { view: "planner", client: "general" };
      }
      return r;
    };
  }

  // 4. Inject proposal preset and activate its slug via ?client=<slug>.
  if (data.preset && typeof data.preset === "object" && typeof data.preset.slug === "string" && data.preset.slug) {
    var slug = data.preset.slug.toLowerCase();
    var origGetAllPresets = typeof window.getAllPresets === "function" ? window.getAllPresets : null;
    window.getAllPresets = function () {
      var all = origGetAllPresets ? origGetAllPresets() : {};
      all[slug] = data.preset;
      return all;
    };

    var params = new URLSearchParams(window.location.search || "");
    params.delete("admin");
    params.set("client", slug);
    if (typeof data.mode === "string" && data.mode) {
      params.set("mode", data.mode);
    }
    if (data.proposal_id) {
      params.set("proposal", String(data.proposal_id));
    }
    var targetPath = window.location.pathname || "/pitch/";
    if (targetPath.indexOf("/clients/") === 0 || targetPath === "/admin" || targetPath.indexOf("/admin/") === 0) {
      targetPath = "/pitch/";
    }
    window.history.replaceState(null, "", targetPath + "?" + params.toString());
  }

  // 5. Mirror PDF exports to the engine when a proposal_id is active.
  if (
    window.jspdf &&
    window.jspdf.jsPDF &&
    window.jspdf.jsPDF.prototype &&
    typeof window.jspdf.jsPDF.prototype.save === "function"
  ) {
    var origSave = window.jspdf.jsPDF.prototype.save;
    window.jspdf.jsPDF.prototype.save = function () {
      if (data.proposal_id && typeof fetch === "function") {
        try {
          var pdfBlob = this.output("blob");
          var endpoint = "/api/pitch/proposals/" + encodeURIComponent(String(data.proposal_id)) + "/pdf";
          fetch(endpoint, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/pdf" },
            body: pdfBlob
          }).then(function () {}, function () {});
        } catch (err) {
          // Ignore upload failures; browser download still executes.
        }
      }
      return origSave.apply(this, arguments);
    };
  }
})();
