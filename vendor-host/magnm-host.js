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

  function stripAngleBrackets(value) {
    if (typeof value === "string") {
      return value.replace(/[<>]/g, "");
    }
    if (Array.isArray(value)) {
      return value.map(stripAngleBrackets);
    }
    if (value && typeof value === "object") {
      var out = {};
      Object.keys(value).forEach(function (k) {
        out[k] = stripAngleBrackets(value[k]);
      });
      return out;
    }
    return value;
  }

  if (data.custom_items !== undefined) {
    data.custom_items = stripAngleBrackets(data.custom_items);
  }
  if (data.preset !== undefined) {
    data.preset = stripAngleBrackets(data.preset);
  }

  var INITIAL_PRICES = {};
  if (typeof ITEMS !== "undefined") {
    Object.keys(ITEMS).forEach(function (id) {
      INITIAL_PRICES[id] = ITEMS[id].p;
    });
  }

  function applyPrices(priceMap) {
    if (typeof ITEMS === "undefined") return;
    Object.keys(INITIAL_PRICES).forEach(function (id) {
      if (ITEMS[id]) ITEMS[id].p = INITIAL_PRICES[id];
    });
    if (!priceMap || typeof priceMap !== "object") return;
    var priceKeys = Object.keys(priceMap);
    for (var i = 0; i < priceKeys.length; i++) {
      var id = priceKeys[i];
      var price = priceMap[id];
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

  function applyCustomItems(customItems) {
    if (typeof ITEMS === "undefined" || typeof GROUPS === "undefined") return;
    Object.keys(ITEMS).forEach(function (id) {
      if (id.indexOf("custom_") === 0) {
        delete ITEMS[id];
      }
    });
    if (!Array.isArray(customItems)) return;
    for (var j = 0; j < customItems.length; j++) {
      var item = customItems[j];
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

  // 1. Override base item prices for existing items only (non-negative integers).
  applyPrices(data.prices);
  if (typeof ITEMS !== "undefined") {
    Object.keys(ITEMS).forEach(function (id) {
      INITIAL_PRICES[id] = ITEMS[id].p;
    });
  }

  // 2. Register custom items (ids must start with custom_, group must exist in GROUPS).
  applyCustomItems(data.custom_items);

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

  // 4. Override buildPlannerSections so preset.sections filters Pitch's themed groups and appends Additional services.
  var origBuildPlannerSections =
    typeof window.buildPlannerSections === "function" ? window.buildPlannerSections : null;
  var TICK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';
  var SHEET_KEYS = ["p2", "p3", "p4", "p5"];

  function createItemButton(id, it) {
    if (!it) return null;
    var b = document.createElement("button");
    b.type = "button";
    b.className = "item";
    b.setAttribute("role", "checkbox");
    b.setAttribute("data-id", id);
    b.innerHTML =
      '<span class="box">' +
      TICK +
      '</span><span><span class="name"></span><span class="desc"></span><span class="note"></span></span><span class="price"></span>';
    b.querySelector(".name").textContent = it.n;
    b.querySelector(".desc").textContent = it.d;
    b.querySelector(".price").textContent =
      typeof window.money === "function" ? window.money(it.p) : String(it.p);
    b.addEventListener("click", function () {
      if (typeof window.toggle === "function") window.toggle(id);
    });
    return b;
  }

  function filterSheetSections(sheetKey, sectionIds) {
    var container = document.getElementById(sheetKey + "-groups");
    if (!container) return;

    var wantedIds = [];
    var allowedSet = {};
    for (var i = 0; i < sectionIds.length; i++) {
      var id = String(sectionIds[i] || "").trim();
      if (!id) continue;
      if (!Object.prototype.hasOwnProperty.call(allowedSet, id)) {
        allowedSet[id] = true;
        wantedIds.push(id);
      }
    }

    var renderedIds = {};
    var groups = Array.prototype.slice.call(container.querySelectorAll(".group"));
    groups.forEach(function (groupEl) {
      var buttons = Array.prototype.slice.call(groupEl.querySelectorAll("button.item[data-id]"));
      buttons.forEach(function (btn) {
        var btnId = btn.getAttribute("data-id");
        if (!Object.prototype.hasOwnProperty.call(allowedSet, btnId)) {
          if (btn.parentNode) btn.parentNode.removeChild(btn);
        } else {
          renderedIds[btnId] = true;
        }
      });
      if (!groupEl.querySelector("button.item[data-id]")) {
        if (groupEl.parentNode) groupEl.parentNode.removeChild(groupEl);
      }
    });

    if (typeof ITEMS === "undefined") return;
    var extraIds = wantedIds.filter(function (id) {
      return !Object.prototype.hasOwnProperty.call(renderedIds, id) && ITEMS[id];
    });

    if (extraIds.length > 0) {
      var groupEl = document.createElement("div");
      groupEl.className = "group";

      var labelEl = document.createElement("div");
      labelEl.className = "label";
      var labelSpan = document.createElement("span");
      labelSpan.textContent = "Additional services";
      labelEl.appendChild(labelSpan);
      groupEl.appendChild(labelEl);

      var itemsEl = document.createElement("div");
      itemsEl.className = "items";
      extraIds.forEach(function (id) {
        var b = createItemButton(id, ITEMS[id]);
        if (b) itemsEl.appendChild(b);
      });
      groupEl.appendChild(itemsEl);
      container.appendChild(groupEl);
    }
  }

  function updatePortfolioAndBanner(preset) {
    var bannerLinks = document.querySelector("#client-banner .banner-links");
    if (bannerLinks) {
      bannerLinks.hidden = true;
      bannerLinks.style.display = "none";
    }
    if (!preset) return;
    var portfolioEl = document.querySelector("#p1 .who a");
    if (portfolioEl && typeof preset.portfolio === "string" && preset.portfolio.trim()) {
      var rawPort = preset.portfolio.trim();
      portfolioEl.textContent = rawPort;
      if (/^https?:\/\//i.test(rawPort)) {
        portfolioEl.setAttribute("href", rawPort);
      } else {
        portfolioEl.setAttribute("href", "https://" + rawPort);
      }
    }
  }

  function renderArchitectureSafe(preset) {
    var container = document.getElementById("architecture-block-container");
    if (!container) return;
    container.innerHTML = "";
    var arch = preset && preset.architectureBlock;
    if (!arch || !Array.isArray(arch.roles) || !arch.roles.length) return;

    var block = document.createElement("div");
    block.className = "block";
    if (arch.heading) {
      var h2 = document.createElement("h2");
      h2.textContent = arch.heading;
      block.appendChild(h2);
    }
    if (arch.sub) {
      var sub = document.createElement("p");
      sub.className = "sub";
      sub.textContent = arch.sub;
      block.appendChild(sub);
    }
    var rolesEl = document.createElement("div");
    rolesEl.className = "roles";
    arch.roles.forEach(function (role) {
      var roleEl = document.createElement("div");
      roleEl.className = "role";
      var labelEl = document.createElement("span");
      labelEl.className = "label";
      labelEl.textContent = role.label || "";
      roleEl.appendChild(labelEl);
      var ol = document.createElement("ol");
      (Array.isArray(role.points) ? role.points : []).forEach(function (pt) {
        var li = document.createElement("li");
        li.textContent = pt || "";
        ol.appendChild(li);
      });
      roleEl.appendChild(ol);
      rolesEl.appendChild(roleEl);
    });
    block.appendChild(rolesEl);
    container.appendChild(block);
  }

  window.buildPlannerSections = function (preset) {
    updatePortfolioAndBanner(preset);
    if (origBuildPlannerSections) {
      origBuildPlannerSections(preset);
    }
    if (preset && preset.sections && typeof preset.sections === "object") {
      SHEET_KEYS.forEach(function (key) {
        var ids = Array.isArray(preset.sections[key]) ? preset.sections[key] : [];
        filterSheetSections(key, ids);
      });
    }
  };

  // 5. Inject proposal preset and activate its slug via ?client=<slug>.
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

  // 6. Mirror PDF exports to the engine when a proposal_id is active.
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
          })
            .then(function (resp) {
              return resp && resp.ok && resp.json ? resp.json() : null;
            })
            .then(
              function (res) {
                if (res && window.parent && window.parent !== window) {
                  window.parent.postMessage(
                    {
                      type: "magnm:pitch-pdf-saved",
                      proposal_id: data.proposal_id,
                      result: res
                    },
                    window.location.origin
                  );
                }
              },
              function () {}
            );
        } catch (err) {
          // Ignore upload failures; browser download still executes.
        }
      }
      return origSave.apply(this, arguments);
    };
  }

  // 7. Live preview updates from Studio editor + Escape exit forwarding in present mode.
  window.addEventListener("message", function (event) {
    if (event.origin !== window.location.origin) return;
    var msg = event.data;
    if (!msg || typeof msg !== "object" || msg.type !== "magnm:pitch-preview-update") return;
    var payload = msg.payload || {};
    var cleanCustomItems = Array.isArray(payload.custom_items)
      ? stripAngleBrackets(payload.custom_items)
      : null;
    var cleanPreset =
      payload.preset && typeof payload.preset === "object"
        ? stripAngleBrackets(payload.preset)
        : null;
    if (payload.prices && typeof payload.prices === "object") {
      applyPrices(payload.prices);
    } else {
      applyPrices(null);
    }
    if (cleanCustomItems) {
      applyCustomItems(cleanCustomItems);
    }
    if (cleanPreset) {
      var nextPreset = cleanPreset;
      if (typeof activePreset !== "undefined") activePreset = nextPreset;
      var heroLead = document.getElementById("hero-lead");
      if (heroLead) heroLead.textContent = nextPreset.lead || "";
      var heroWhat = document.getElementById("hero-what");
      if (heroWhat) heroWhat.textContent = nextPreset.whatWeDo || "";
      var heroPlan = document.getElementById("hero-plan");
      if (heroPlan) heroPlan.textContent = nextPreset.thisPlan || "";
      var subTitle = document.getElementById("p1-subtitle");
      if (subTitle) subTitle.textContent = nextPreset.slug === "general" ? "Software Planner" : "Client Proposal";
      var fName = document.getElementById("f-name");
      if (fName) fName.value = nextPreset.clientName || "";
      var fBiz = document.getElementById("f-biz");
      if (fBiz) fBiz.value = nextPreset.businessName || "";

      var banner = document.getElementById("client-banner");
      var bannerText = document.getElementById("banner-text");
      if (banner && bannerText) {
        if (nextPreset.slug && nextPreset.slug !== "general") {
          bannerText.innerHTML = "";
          var strong = document.createElement("strong");
          strong.textContent = nextPreset.badge || nextPreset.name || nextPreset.slug;
          bannerText.appendChild(strong);
          bannerText.appendChild(document.createTextNode(" (Route: "));
          var code = document.createElement("code");
          code.textContent = "/clients/" + nextPreset.slug;
          bannerText.appendChild(code);
          bannerText.appendChild(document.createTextNode(")"));
          banner.hidden = false;
        } else {
          banner.hidden = true;
        }
      }

      renderArchitectureSafe(nextPreset);
      if (typeof sel !== "undefined") {
        sel = {};
        var defaults = Array.isArray(nextPreset.initialSelected) ? nextPreset.initialSelected : [];
        defaults.forEach(function (id) {
          if (typeof ITEMS !== "undefined" && ITEMS[id] && typeof window.addWithDeps === "function") {
            window.addWithDeps(id);
          }
        });
      }
      window.buildPlannerSections(nextPreset);
      if (typeof window.update === "function") window.update();
    }
  });

  if (data.mode === "present" || data.mode === "form") {
    window.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && window.parent && window.parent !== window) {
        window.parent.postMessage({ type: "magnm:pitch-exit-present" }, window.location.origin);
      }
    });
  }
})();
