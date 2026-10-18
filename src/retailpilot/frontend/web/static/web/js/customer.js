// Customer shop: search for a product, or pick a category to see the best picks and every product for the delivery city.
// Products can be compared side by side, and each one can also be sent to Ask RetailPilot.
(function () {
  "use strict";

  const COMPARE_KEY = "rp-compare";
  const MAX_COMPARE = 4;
  const PAGE_SIZE = 12;
  const SUGGEST_COUNT = 6;
  const SUGGEST_DELAY = 200;
  const el = (id) => document.getElementById(id);
  const state = {
    category: null,
    picks: [],
    all: [],
    allTotal: 0,
    allToken: 0,
    searchQuery: "",
    search: [],
    searchTotal: 0,
    searchToken: 0,
    known: new Map(),
    compare: readList(COMPARE_KEY),
  };

  // ------------------------------------------------------------ helpers

  function readList(key) {
    try {
      return JSON.parse(localStorage.getItem(key) || "[]");
    } catch (e) {
      return [];
    }
  }

  function writeList(key, list) {
    try {
      localStorage.setItem(key, JSON.stringify(list));
    } catch (e) {
      /* storage may be blocked; the lists then last for this page visit only */
    }
  }

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  async function api(path, options = {}) {
    const init = { method: options.method || "GET", headers: { Accept: "application/json" } };
    if (options.body) {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRFToken"] = csrfToken();
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Something went wrong.");
    return data;
  }

  function money(value) {
    return typeof value === "number" ? "₹" + Math.round(value).toLocaleString("en-IN") : "–";
  }

  function esc(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function amazonUrl(item) {
    return item.url || `https://www.amazon.in/dp/${item.asin}`;
  }

  function inCompare(asin) {
    return state.compare.some((item) => item.asin === asin);
  }

  function snapshot(item) {
    return {
      asin: item.asin,
      title: item.title,
      brand: item.brand,
      price: item.price,
      mrp: item.mrp,
      discount_pct: item.discount_pct,
      rating: item.rating,
      rating_count: item.rating_count,
      battery_hours: item.battery_hours,
      delivery_date: item.delivery_date,
      seller: item.seller,
      key_features: item.key_features || [],
      url: item.url,
    };
  }

  // Remembers products by ASIN so the buttons on any list can find them.
  function remember(items) {
    items.forEach((item) => state.known.set(item.asin, item));
    return items;
  }

  function note(id, text) {
    el(id).textContent = text || "";
    el(id).hidden = !text;
  }

  function cityName() {
    return el("city").selectedOptions[0] ? el("city").selectedOptions[0].textContent : "your city";
  }

  function productsUrl(params) {
    const query = Object.entries(params)
      .filter(([, value]) => value !== null && value !== undefined && value !== "")
      .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
      .join("&");
    return `/api/products/?${query}`;
  }

  // Sends the product to the Ask RetailPilot panel with the product as context.
  function askAbout(item) {
    const city = el("city").selectedOptions[0] ? el("city").selectedOptions[0].textContent : "my city";
    document.dispatchEvent(new CustomEvent("rp:ask", {
      detail: {
        question: `Tell me about ${item.title}: its price, specs, and pros and cons.`,
        context: `I am looking at ${item.title} (ASIN ${item.asin}) in ${city}.`,
      },
    }));
  }

  // One product card. Picks carry the RetailPilot tag; the other lists show the card without it.
  function cardHtml(item, picked) {
    return `
      <div class="pick pick-sm">
        <div class="pick-topline">${picked ? '<span class="pick-tag"><i aria-hidden="true">✦</i> RETAILPILOT PICK</span>' : ""}<span class="pick-brand">${esc(item.brand || "Marketplace listing")}</span></div>
        <div class="pick-image">
          ${item.image_url
            ? `<img src="${esc(item.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
            : '<span class="image-placeholder" aria-hidden="true">RP</span>'}
        </div>
        <div class="pick-title">${esc((item.title || "").slice(0, 110))}</div>
        <div class="pick-price-row">
          <div><span class="price-caption">Observed price</span><div class="price">${money(item.price)}</div></div>
          ${item.discount_pct ? `<span class="pick-discount">${esc(item.discount_pct)}% below MRP</span>` : ""}
        </div>
        <div class="pick-details">
          <span class="rating-chip">${item.rating ? `<b>${Number(item.rating).toFixed(1)} ★</b> ${Number(item.rating_count || 0).toLocaleString("en-IN")} ratings` : "No ratings yet"}</span>
          <span class="delivery-chip"><i aria-hidden="true">⌁</i> ${esc(item.delivery_date || "See listing for delivery")}</span>
        </div>
        <div class="actions">
          <a class="btn btn-ghost btn-small" href="${esc(amazonUrl(item))}" target="_blank" rel="noopener">View on Amazon <span aria-hidden="true">↗</span></a>
          <button class="btn btn-ghost btn-small" type="button" data-ask="${esc(item.asin)}">Ask RetailPilot</button>
          ${inCompare(item.asin)
            ? '<button class="btn btn-small" type="button" disabled>In compare ✓</button>'
            : `<button class="btn btn-small" type="button" data-compare="${esc(item.asin)}">Add to compare <span aria-hidden="true">+</span></button>`}
        </div>
      </div>`;
  }

  // ------------------------------------------------------------ categories (step 1)

  function renderCategories(categories) {
    el("categories").innerHTML = categories.map((c) => `
      <button class="cat-card" data-name="${esc(c.name)}" type="button">
        <span class="cat-icon" aria-hidden="true">${esc((c.name || "?").charAt(0).toUpperCase())}<i>↗</i></span>
        <span class="cat-name">${esc(c.name)}</span>
        <span class="cat-meta">${Number(c.products).toLocaleString("en-IN")} products <i aria-hidden="true">→</i></span>
      </button>`).join("") || '<p class="fine">No categories have data yet. Run the crawler first.</p>';
    el("categories").setAttribute("aria-busy", "false");
  }

  el("categories").addEventListener("click", (event) => {
    const card = event.target.closest(".cat-card");
    if (!card) return;
    state.category = card.dataset.name;
    showPicks();
  });

  el("back-cats").addEventListener("click", () => {
    state.category = null;
    state.picks = [];
    state.all = [];
    state.allTotal = 0;
    state.allToken++;
    el("step-picks").hidden = true;
    el("step-categories").hidden = false;
  });

  // ------------------------------------------------------------ search with suggestions

  const searchInput = el("shop-search-input");
  const suggestBox = el("shop-suggest");
  let suggestTimer = null;
  let suggestToken = 0;
  let activeSuggest = -1;

  function closeSuggest() {
    suggestBox.hidden = true;
    suggestBox.innerHTML = "";
    searchInput.setAttribute("aria-expanded", "false");
    activeSuggest = -1;
  }

  function renderSuggest(items) {
    activeSuggest = -1;
    suggestBox.innerHTML = items.map((item) => `
      <li role="option" aria-selected="false">
        <button class="suggest-item" type="button" data-suggest="${esc(item.asin)}" tabindex="-1">
          <span class="suggest-thumb">${item.image_url ? `<img src="${esc(item.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</span>
          <span class="suggest-title">${esc((item.title || "").slice(0, 90))}</span>
          <b class="suggest-price">${money(item.price)}</b>
        </button>
      </li>`).join("");
    suggestBox.hidden = items.length === 0;
    searchInput.setAttribute("aria-expanded", String(items.length > 0));
  }

  // Moves the highlighted suggestion with the arrow keys, wrapping at both ends.
  function moveSuggest(step) {
    const options = suggestBox.querySelectorAll(".suggest-item");
    if (!options.length) return;
    const count = options.length;
    if (activeSuggest < 0) activeSuggest = step > 0 ? 0 : count - 1;
    else activeSuggest = (activeSuggest + step + count) % count;
    options.forEach((option, i) => {
      option.classList.toggle("is-active", i === activeSuggest);
      option.parentElement.setAttribute("aria-selected", String(i === activeSuggest));
    });
  }

  function chooseSuggest(asin) {
    const item = state.known.get(asin);
    if (!item) return;
    searchInput.value = item.title;
    closeSuggest();
    runSearch(item.title);
  }

  async function fetchSuggest(query) {
    const city = el("city").value;
    const token = ++suggestToken;
    if (!city) return closeSuggest();
    try {
      const data = await api(productsUrl({ city, q: query, limit: SUGGEST_COUNT }));
      if (token !== suggestToken) return;
      renderSuggest(remember(data.items || []));
    } catch (err) {
      if (token === suggestToken) closeSuggest();
    }
  }

  searchInput.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    const query = searchInput.value.trim();
    if (query.length < 2) {
      suggestToken++;
      closeSuggest();
      return;
    }
    suggestTimer = setTimeout(() => fetchSuggest(query), SUGGEST_DELAY);
  });

  searchInput.addEventListener("keydown", (event) => {
    if (suggestBox.hidden) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveSuggest(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveSuggest(-1);
    } else if (event.key === "Enter" && activeSuggest >= 0) {
      event.preventDefault();
      chooseSuggest(suggestBox.querySelectorAll(".suggest-item")[activeSuggest].dataset.suggest);
    } else if (event.key === "Escape") {
      closeSuggest();
    }
  });

  suggestBox.addEventListener("click", (event) => {
    const option = event.target.closest("[data-suggest]");
    if (option) chooseSuggest(option.dataset.suggest);
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".shop-search-box")) closeSuggest();
  });

  el("shop-search").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = searchInput.value.trim();
    closeSuggest();
    if (query) runSearch(query);
  });

  // Step 1 for a search: the categories the query matches, each opening its own step 2 list.
  function renderSearchCategories(categories) {
    el("search-category-chips").innerHTML = categories.map((c) => `
      <button class="cat-chip" type="button" data-category="${esc(c.name)}">${esc(c.name)} <span>${Number(c.products).toLocaleString("en-IN")}</span></button>`).join("");
    el("search-categories").hidden = categories.length === 0;
    if (categories.length) {
      openSearchCategory(categories[0].name);
    } else {
      state.category = null;
      el("step-picks").hidden = true;
    }
  }

  // While a search is on, the aisle grid is replaced by every product in the category the search lands in.
  function openSearchCategory(name) {
    state.category = name;
    state.all = [];
    state.allTotal = 0;
    state.allToken++;
    el("step-categories").hidden = true;
    el("step-picks").hidden = false;
    el("step-picks").classList.add("search-mode");
    el("pick-title").textContent = name;
    el("pick-city").textContent = cityName();
    fetchAll(true);
  }

  // Clearing the search brings the aisle grid back.
  function leaveSearchMode() {
    state.category = null;
    state.all = [];
    state.allTotal = 0;
    state.allToken++;
    el("step-picks").classList.remove("search-mode");
    el("step-picks").hidden = true;
    el("step-categories").hidden = false;
  }

  async function fetchSearchCategories(query, token) {
    try {
      const data = await api(`/api/category-search/?q=${encodeURIComponent(query)}`);
      if (token === state.searchToken) renderSearchCategories(data.matches || []);
    } catch (err) {
      if (token === state.searchToken) renderSearchCategories([]);
    }
  }

  el("search-category-chips").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-category]");
    if (!chip) return;
    openSearchCategory(chip.dataset.category);
    el("step-picks").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  async function runSearch(query) {
    const city = el("city").value;
    const token = ++state.searchToken;
    state.searchQuery = query;
    state.search = [];
    state.searchTotal = 0;
    el("search-results").hidden = false;
    el("step-categories").hidden = true;
    el("search-title").textContent = `Products matching “${query}”`;
    note("search-note", "Searching…");
    renderSearch();
    renderSearchCategories([]);
    fetchSearchCategories(query, token);
    if (!city) return note("search-note", "No delivery city is available right now.");
    try {
      const data = await api(productsUrl({ city, q: query, limit: PAGE_SIZE }));
      if (token !== state.searchToken) return;
      state.search = remember(data.items || []);
      state.searchTotal = data.total || 0;
      renderSearch();
      el("search-title").textContent = state.searchTotal
        ? `${state.searchTotal.toLocaleString("en-IN")} products matching “${query}”`
        : `No products match “${query}”`;
      note("search-note", state.searchTotal ? "" : data.note || "Try a shorter word, such as headphones, charger or laptop.");
    } catch (err) {
      if (token !== state.searchToken) return;
      note("search-note", err.message);
    }
  }

  async function loadMoreSearch() {
    const city = el("city").value;
    const token = state.searchToken;
    el("more-search").disabled = true;
    try {
      const data = await api(productsUrl({ city, q: state.searchQuery, offset: state.search.length, limit: PAGE_SIZE }));
      if (token !== state.searchToken) return;
      state.search = state.search.concat(remember(data.items || []));
      state.searchTotal = data.total || 0;
      renderSearch();
    } catch (err) {
      if (token === state.searchToken) note("search-note", err.message);
    } finally {
      el("more-search").disabled = false;
    }
  }

  el("more-search").addEventListener("click", loadMoreSearch);

  el("clear-search").addEventListener("click", () => {
    state.searchToken++;
    state.searchQuery = "";
    state.search = [];
    state.searchTotal = 0;
    searchInput.value = "";
    closeSuggest();
    renderSearchCategories([]);
    leaveSearchMode();
    el("search-results").hidden = true;
    searchInput.focus();
  });

  // ------------------------------------------------------------ picks (step 2) and all products

  function showPicks() {
    el("step-categories").hidden = true;
    el("step-picks").hidden = false;
    el("pick-title").textContent = state.category;
    el("pick-city").textContent = cityName();
    fetchPicks();
    fetchAll(true);
  }

  function renderPicks() {
    el("picks").innerHTML = state.picks.map((item) => cardHtml(item, true)).join("");
    el("picks").hidden = state.picks.length === 0;
    el("more-picks").parentElement.hidden = state.picks.length === 0;
    el("picks").setAttribute("aria-busy", "false");
  }

  function renderAll() {
    el("all-products").innerHTML = state.all.map((item) => cardHtml(item, false)).join("");
    el("all-count").textContent = state.allTotal ? `${state.allTotal.toLocaleString("en-IN")} products` : "";
    el("all-more").hidden = state.all.length >= state.allTotal;
  }

  function renderSearch() {
    el("search-products").innerHTML = state.search.map((item) => cardHtml(item, false)).join("");
    el("search-more").hidden = state.search.length >= state.searchTotal;
  }

  // Redraws every product card, so the compare buttons stay in step with the compare list.
  function renderCards() {
    renderPicks();
    renderAll();
    renderSearch();
  }

  async function fetchPicks() {
    const city = el("city").value;
    el("picks").setAttribute("aria-busy", "true");
    note("picks-note", "");
    if (!city) {
      state.picks = [];
      renderPicks();
      note("picks-note", "No delivery city is available right now.");
      return;
    }
    try {
      const data = await api(`/api/recommendations/?category=${encodeURIComponent(state.category)}&city=${encodeURIComponent(city)}`);
      state.picks = remember(data.items || []);
      renderPicks();
      note("picks-note", data.note || (!state.picks.length && data.latest_observed_at ? `Latest data is from ${data.latest_observed_at}.` : ""));
    } catch (err) {
      state.picks = [];
      renderPicks();
      note("picks-note", err.message);
    }
  }

  // Loads the first page of a category (reset) or the next page of it.
  async function fetchAll(reset) {
    const city = el("city").value;
    const token = reset ? ++state.allToken : state.allToken;
    if (reset) {
      state.all = [];
      state.allTotal = 0;
    }
    note("all-note", "");
    if (!city) {
      renderAll();
      return note("all-note", "No delivery city is available right now.");
    }
    el("more-products").disabled = true;
    try {
      const data = await api(productsUrl({ city, category: state.category, offset: state.all.length, limit: PAGE_SIZE }));
      if (token !== state.allToken) return;
      state.all = state.all.concat(remember(data.items || []));
      state.allTotal = data.total || 0;
      renderAll();
      note("all-note", state.allTotal ? "" : data.note || "No priced products in this category for this city.");
    } catch (err) {
      if (token !== state.allToken) return;
      renderAll();
      note("all-note", err.message);
    } finally {
      el("more-products").disabled = false;
    }
  }

  el("more-picks").addEventListener("click", fetchPicks);
  el("more-products").addEventListener("click", () => fetchAll(false));

  el("city").addEventListener("change", () => {
    closeSuggest();
    if (state.category && !state.searchQuery) showPicks();
    if (state.searchQuery) runSearch(state.searchQuery);
  });

  function onCardClick(event) {
    const compareBtn = event.target.closest("[data-compare]");
    if (compareBtn) {
      addToCompare(compareBtn.dataset.compare);
      return;
    }
    const askBtn = event.target.closest("[data-ask]");
    if (askBtn) {
      const item = state.known.get(askBtn.dataset.ask);
      if (item) askAbout(item);
    }
  }

  el("picks").addEventListener("click", onCardClick);
  el("all-products").addEventListener("click", onCardClick);
  el("search-products").addEventListener("click", onCardClick);

  // ------------------------------------------------------------ compare

  function askComparison() {
    if (!state.compare.length) return;
    // Close the compare drawer so the chat sidebar is not hidden behind it.
    el("cart-drawer").hidden = true;
    const city = el("city").selectedOptions[0] ? el("city").selectedOptions[0].textContent : "my city";
    const names = state.compare.map((c, i) => `${i + 1}. ${c.title} (ASIN ${c.asin})`).join("\n");
    document.dispatchEvent(new CustomEvent("rp:ask", {
      detail: {
        question: "Compare these products for me. Which is the best value, and why?",
        context: `I am comparing these products in ${city}:
${names}`,
      },
    }));
  }

  function addToCompare(asin) {
    const item = state.known.get(asin);
    if (!item || inCompare(asin)) return;
    if (state.compare.length >= MAX_COMPARE) {
      alert(`You can compare up to ${MAX_COMPARE} products. Remove one to add another.`);
      return;
    }
    state.compare.push(snapshot(item));
    writeList(COMPARE_KEY, state.compare);
    renderCompareCount();
    renderCards();
  }

  function removeFromCompare(asin) {
    state.compare = state.compare.filter((item) => item.asin !== asin);
    writeList(COMPARE_KEY, state.compare);
    renderCompareCount();
    renderCards();
    renderCompare();
  }

  function renderCompareCount() {
    el("compare-count").textContent = state.compare.length;
  }

  function renderCompare() {
    const body = el("compare-body");
    if (!state.compare.length) {
      body.innerHTML = '<p class="fine">Add picks from the shop to compare them side by side.</p>';
    } else {
      const cols = state.compare;
      const row = (label, render) => `<tr><th scope="row">${label}</th>${cols.map((c) => `<td>${render(c)}</td>`).join("")}</tr>`;
      body.innerHTML = `
        <div class="compare-ask">
          <button class="btn" type="button" data-ask-comparison>Ask RetailPilot about this comparison</button>
        </div>
        <div class="compare-scroll">
          <table class="compare-table">
            <thead>
              <tr><th></th>${cols.map((c) => `
                <th scope="col"><div class="compare-title">${esc((c.title || "").slice(0, 70))}</div>
                  <button class="link-btn" type="button" data-remove-compare="${esc(c.asin)}">Remove</button></th>`).join("")}</tr>
            </thead>
            <tbody>
              ${row("Price", (c) => `<b>${money(c.price)}</b>`)}
              ${row("MRP", (c) => money(c.mrp))}
              ${row("Discount", (c) => (c.discount_pct ? `${esc(c.discount_pct)}% off` : "–"))}
              ${row("Rating", (c) => (c.rating ? `${Number(c.rating).toFixed(1)} ★ (${Number(c.rating_count || 0).toLocaleString("en-IN")})` : "No ratings yet"))}
              ${row("Battery", (c) => (c.battery_hours ? `${esc(c.battery_hours)} h` : "–"))}
              ${row("Delivery", (c) => esc(c.delivery_date || "See listing"))}
              ${row("Seller", (c) => esc(c.seller || "–"))}
              ${row("Key features", (c) => (c.key_features || []).slice(0, 2).map((f) => `<div>${esc(f)}</div>`).join("") || "–")}
              ${row("Listing", (c) => `<a href="${esc(amazonUrl(c))}" target="_blank" rel="noopener">View on Amazon ↗</a>`)}
            </tbody>
          </table>
        </div>`;
    }
  }

  el("compare-body").addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-compare]");
    if (remove) return removeFromCompare(remove.dataset.removeCompare);
    if (event.target.closest("[data-ask-comparison]")) askComparison();
  });

  // ------------------------------------------------------------ drawer

  function closeCompare() {
    el("cart-drawer").hidden = true;
    el("cart-btn").focus();
  }

  el("cart-btn").addEventListener("click", () => {
    renderCompare();
    el("cart-drawer").hidden = false;
    el("close-cart").focus();
  });
  el("close-cart").addEventListener("click", closeCompare);
  el("cart-drawer").addEventListener("click", (event) => {
    if (event.target === el("cart-drawer")) closeCompare();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el("cart-drawer").hidden) closeCompare();
  });

  // ------------------------------------------------------------ start

  async function init() {
    renderCompareCount();
    try {
      const cities = (await api("/api/cities/")).cities.filter((c) => c.covered);
      el("city").innerHTML = cities.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("")
        || '<option value="">No city has data in the last crawl</option>';
    } catch (err) {
      el("city").innerHTML = '<option value="">Unavailable</option>';
    }
    try {
      renderCategories((await api("/api/categories/")).categories);
    } catch (err) {
      el("categories").innerHTML = `<p class="fine">${esc(err.message)}</p>`;
      el("categories").setAttribute("aria-busy", "false");
    }
  }

  init();
})();
