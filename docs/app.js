// Used Guitar Tracker front end. Reads data/listings.json (written by scraper/scrape.py).
(() => {
  const PAGE = 60;
  const DAY = 86400000;
  const $ = (id) => document.getElementById(id);

  // Persistent per-browser settings.
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem("ugt." + key); return v === null ? fallback : JSON.parse(v); }
      catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem("ugt." + key, JSON.stringify(value)); } catch { /* private mode */ }
    },
  };

  const defaults = { q: "", mineOnly: false, newOnly: false, age: "0", pmin: "", pmax: "",
                     cats: [], shops: [], brands: [], sort: "new" };
  let state = { ...defaults, ...store.get("filters", {}) };
  let myBrands = new Set(store.get("myBrands", []));
  let data = { items: [], sites: {} };
  let shown = PAGE;

  // "New since last visit": a visit ends after 30 minutes without opening the page,
  // so reloading during a visit keeps the NEW badges.
  const nowIso = new Date().toISOString();
  const lastSeen = store.get("lastSeen", null);
  if (!lastSeen || Date.now() - Date.parse(lastSeen) > 30 * 60000) {
    store.set("lastVisit", store.get("visitStart", null));
    store.set("visitStart", nowIso);
  }
  store.set("lastSeen", nowIso);
  const lastVisit = store.get("lastVisit", null);
  const visitCutoff = lastVisit ? Date.parse(lastVisit) : 0;

  const money = (n) => n == null ? "—" : "£" + n.toLocaleString("en-GB", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
  const ago = (iso) => {
    const d = (Date.now() - Date.parse(iso)) / DAY;
    if (d < 1) return "today";
    if (d < 2) return "yesterday";
    if (d < 30) return Math.floor(d) + " days ago";
    return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const normBrand = (b) => (b || "Unknown").trim();
  const firstSeenOfSite = {};

  function previousPrice(item) {
    const h = item.price_history || [];
    return h.length > 1 ? h[h.length - 2][1] : null;
  }
  function isNew(item) {
    // Items present on a site's very first scrape aren't "new" stock.
    if (Date.parse(item.first_seen) <= firstSeenOfSite[item.site]) return false;
    return visitCutoff ? Date.parse(item.first_seen) > visitCutoff : Date.now() - Date.parse(item.first_seen) < DAY;
  }

  function save() { store.set("filters", state); }

  function filtered() {
    const q = state.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const pmin = parseFloat(state.pmin), pmax = parseFloat(state.pmax);
    const ageDays = parseInt(state.age, 10);
    return data.items.filter((it) => {
      if (state.mineOnly && !myBrands.has(normBrand(it.brand))) return false;
      if (state.brands.length && !state.brands.includes(normBrand(it.brand))) return false;
      if (state.cats.length && !state.cats.includes(it.category)) return false;
      if (state.shops.length && !state.shops.includes(it.site)) return false;
      if (!isNaN(pmin) && (it.price ?? 0) < pmin) return false;
      if (!isNaN(pmax) && (it.price ?? Infinity) > pmax) return false;
      if (ageDays && Date.now() - Date.parse(it.first_seen) > ageDays * DAY) return false;
      if (state.newOnly && !isNew(it)) return false;
      if (q.length) {
        const hay = (it.brand + " " + it.title).toLowerCase();
        if (!q.every((w) => hay.includes(w))) return false;
      }
      return true;
    });
  }

  function sorted(items) {
    const by = {
      "new": (a, b) => b.first_seen.localeCompare(a.first_seen),
      "price-asc": (a, b) => (a.price ?? Infinity) - (b.price ?? Infinity),
      "price-desc": (a, b) => (b.price ?? -1) - (a.price ?? -1),
      "drop": (a, b) => ((previousPrice(b) ?? b.price) - b.price) - ((previousPrice(a) ?? a.price) - a.price),
      "brand": (a, b) => normBrand(a.brand).localeCompare(normBrand(b.brand)) || a.title.localeCompare(b.title),
    }[state.sort];
    return items.slice().sort(by);
  }

  function card(it) {
    const prev = previousPrice(it);
    const dropped = prev != null && it.price != null && it.price < prev;
    const site = data.sites[it.site]?.name || it.site;
    const badges = [
      isNew(it) ? '<span class="badge">NEW</span>' : "",
      dropped ? '<span class="badge drop">PRICE DROP</span>' : "",
    ].join("");
    const img = it.image ? `<img src="${esc(it.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "🎸";
    return `<a class="card" href="${esc(it.url)}" target="_blank" rel="noopener">
      <div class="img">${img}<div class="badges">${badges}</div></div>
      <div class="body">
        <div class="brand">${esc(it.brand || "—")}</div>
        <div class="title">${esc(it.title)}</div>
        <div class="foot">
          <span class="price">${money(it.price)}${dropped ? `<span class="was">${money(prev)}</span>` : ""}</span>
          <span class="meta">${esc(site)}<br>${ago(it.first_seen)}</span>
        </div>
      </div>
    </a>`;
  }

  function counts(items, key) {
    const m = new Map();
    for (const it of items) m.set(key(it), (m.get(key(it)) || 0) + 1);
    return m;
  }

  function renderChips(el, values, selected, label, onToggle) {
    el.innerHTML = values.map(([v, n]) =>
      `<button class="chip" data-v="${esc(v)}" aria-pressed="${selected.includes(v)}">${esc(label(v))}<span class="n">${n}</span></button>`
    ).join("");
    el.onclick = (e) => {
      const b = e.target.closest(".chip");
      if (b) onToggle(b.dataset.v);
    };
  }

  function toggleIn(list, v) { return list.includes(v) ? list.filter((x) => x !== v) : [...list, v]; }

  function renderFacets() {
    const all = data.items;
    const catOrder = ["Electric", "Acoustic", "Bass", "Amps & Cabs", "Pedals & Effects", "Other"];
    const cats = counts(all, (i) => i.category);
    renderChips($("cats"), catOrder.filter((c) => cats.has(c)).map((c) => [c, cats.get(c)]), state.cats, (v) => v,
      (v) => { state.cats = toggleIn(state.cats, v); update(); });

    const shops = counts(all, (i) => i.site);
    renderChips($("shops"), Object.keys(data.sites).map((s) => [s, shops.get(s) || 0]), state.shops,
      (v) => data.sites[v]?.name || v, (v) => { state.shops = toggleIn(state.shops, v); update(); });

    const bq = $("brandQ").value.trim().toLowerCase();
    const brands = [...counts(all, (i) => normBrand(i.brand))]
      .filter(([b]) => !bq || b.toLowerCase().includes(bq))
      .sort((a, b) => (myBrands.has(b[0]) - myBrands.has(a[0])) || b[1] - a[1] || a[0].localeCompare(b[0]));
    $("brands").innerHTML = brands.map(([b, n]) => `<li>
      <button class="star ${myBrands.has(b) ? "on" : ""}" data-star="${esc(b)}" title="Add to My brands">${myBrands.has(b) ? "★" : "☆"}</button>
      <label><input type="checkbox" data-brand="${esc(b)}" ${state.brands.includes(b) ? "checked" : ""}><span>${esc(b)}</span></label>
      <span class="n">${n}</span></li>`).join("") || '<li class="n">No brands</li>';
  }

  function renderGrid() {
    const items = sorted(filtered());
    $("count").textContent = `${items.length.toLocaleString()} item${items.length === 1 ? "" : "s"}`;
    $("grid").innerHTML = items.length
      ? items.slice(0, shown).map(card).join("")
      : '<p class="empty">Nothing matches these filters.</p>';
    $("more").hidden = items.length <= shown;
  }

  function update() {
    shown = PAGE;
    save();
    syncInputs();
    renderFacets();
    renderGrid();
  }

  function syncInputs() {
    $("q").value = state.q;
    $("mineOnly").checked = state.mineOnly;
    $("newOnly").checked = state.newOnly;
    $("age").value = state.age;
    $("pmin").value = state.pmin;
    $("pmax").value = state.pmax;
    $("sort").value = state.sort;
  }

  function renderHeader() {
    const gen = data.generated_at ? new Date(data.generated_at) : null;
    $("updated").textContent = gen
      ? `Updated ${gen.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}` +
        (lastVisit ? ` · last visit ${ago(lastVisit)}` : "")
      : "No data yet — run the scraper.";
    $("siteStatus").innerHTML = Object.entries(data.sites).map(([, s]) =>
      `<a class="site-pill ${s.last_error ? "err" : ""}" href="${esc(s.url)}" target="_blank" rel="noopener"
         title="${esc(s.last_error || "Last updated " + (s.last_success || "never"))}">${esc(s.name)} <b>${s.count}</b>${s.last_error ? " ⚠" : ""}</a>`
    ).join("");
  }

  function bind() {
    let t;
    $("q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value; update(); }, 150); });
    for (const id of ["mineOnly", "newOnly"]) $(id).addEventListener("change", (e) => { state[id] = e.target.checked; update(); });
    for (const id of ["age", "sort", "pmin", "pmax"]) $(id).addEventListener("change", (e) => { state[id] = e.target.value; update(); });
    $("brandQ").addEventListener("input", renderFacets);
    $("brands").addEventListener("click", (e) => {
      const star = e.target.closest("[data-star]");
      if (!star) return;
      const b = star.dataset.star;
      myBrands.has(b) ? myBrands.delete(b) : myBrands.add(b);
      store.set("myBrands", [...myBrands]);
      update();
    });
    $("brands").addEventListener("change", (e) => {
      const b = e.target.dataset.brand;
      if (b) { state.brands = toggleIn(state.brands, b); update(); }
    });
    $("reset").addEventListener("click", () => { state = { ...defaults }; $("brandQ").value = ""; update(); });
    $("more").addEventListener("click", () => { shown += PAGE; renderGrid(); });
    $("filterToggle").addEventListener("click", () => $("filters").classList.toggle("open"));
  }

  async function init() {
    bind();
    syncInputs();
    try {
      const res = await fetch("data/listings.json", { cache: "no-cache" });
      data = await res.json();
    } catch (err) {
      $("updated").textContent = "Couldn't load data/listings.json — " + err.message;
      return;
    }
    for (const it of data.items) {
      const t = Date.parse(it.first_seen);
      firstSeenOfSite[it.site] = Math.min(firstSeenOfSite[it.site] ?? Infinity, t);
    }
    renderHeader();
    update();
  }

  init();
})();
