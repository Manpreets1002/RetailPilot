// Customer shop: pick a category, see the best picks for the delivery city and compare them.
// Each pick can also be sent to Ask RetailPilot.
(function () {
  "use strict";

  const COMPARE_KEY = "rp-compare";
  const MAX_COMPARE = 4;
  const el = (id) => document.getElementById(id);
  const state = {
    category: null,
    picks: [],
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
    el("step-picks").hidden = true;
    el("step-categories").hidden = false;
  });

  // ------------------------------------------------------------ search

  const searchResult = el("shop-search-result");

  function routeTo(name) {
    searchResult.hidden = true;
    state.category = name;
    showPicks();
  }

  function showSearchMessage(html) {
    searchResult.innerHTML = html;
    searchResult.hidden = false;
  }

  el("shop-search").addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = el("shop-search-input").value.trim();
    if (!query) return;
    showSearchMessage('<p class="fine">Finding the right category…</p>');
    try {
      const data = await api(`/api/category-search/?q=${encodeURIComponent(query)}`);
      if (data.best) {
        routeTo(data.best);
      } else if ((data.matches || []).length) {
        showSearchMessage(`<p class="fine">${Number(data.matched || 0).toLocaleString("en-IN")} products match “${esc(query)}”, across more than one category. Choose one:</p><div class="search-chips">${data.matches.map((m) => `<button class="btn btn-ghost btn-small" type="button" data-route="${esc(m.name)}">${esc(m.name)} <span>${Number(m.products).toLocaleString("en-IN")}</span></button>`).join("")}</div>`);
      } else {
        showSearchMessage(`<p class="fine">We don’t have “${esc(query)}” in our electronics data yet. Try ${esc((data.examples || []).join(", "))}.</p>`);
      }
    } catch (err) {
      showSearchMessage(`<p class="fine">${esc(err.message)}</p>`);
    }
  });

  searchResult.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-route]");
    if (chip) routeTo(chip.dataset.route);
  });

  // ------------------------------------------------------------ picks (step 2)

  function showPicks() {
    el("step-categories").hidden = true;
    el("step-picks").hidden = false;
    el("pick-title").textContent = state.category;
    el("pick-city").textContent = el("city").selectedOptions[0] ? el("city").selectedOptions[0].textContent : "your city";
    fetchPicks();
  }

  function renderPicks() {
    el("picks").innerHTML = state.picks.map((item) => `
      <div class="pick">
        <div class="pick-topline"><span class="pick-tag"><i aria-hidden="true">✦</i> RETAILPILOT PICK</span><span class="pick-brand">${esc(item.brand || "Marketplace listing")}</span></div>
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
        ${(item.key_features || []).length
          ? `<ul class="pick-features">${item.key_features.slice(0, 2).map((feature) => `<li>${esc(feature)}</li>`).join("")}</ul>`
          : ""}
        <div class="actions">
          <a class="btn btn-ghost btn-small" href="${esc(amazonUrl(item))}" target="_blank" rel="noopener">View on Amazon <span aria-hidden="true">↗</span></a>
          <button class="btn btn-ghost btn-small" type="button" data-ask="${esc(item.asin)}">Ask RetailPilot</button>
          ${inCompare(item.asin)
            ? '<button class="btn btn-small" type="button" disabled>In compare ✓</button>'
            : `<button class="btn btn-small" type="button" data-compare="${esc(item.asin)}">Add to compare <span aria-hidden="true">+</span></button>`}
        </div>
      </div>`).join("");
    el("picks").hidden = state.picks.length === 0;
    el("more-picks").parentElement.hidden = state.picks.length === 0;
    el("picks").setAttribute("aria-busy", "false");
  }

  async function fetchPicks() {
    const city = el("city").value;
    el("picks").setAttribute("aria-busy", "true");
    el("picks-note").hidden = true;
    if (!city) {
      state.picks = [];
      renderPicks();
      el("picks-note").hidden = false;
      el("picks-note").textContent = "No delivery city is available right now.";
      return;
    }
    try {
      const data = await api(`/api/recommendations/?category=${encodeURIComponent(state.category)}&city=${encodeURIComponent(city)}`);
      state.picks = data.items || [];
      renderPicks();
      const note = data.note || (!state.picks.length && data.latest_observed_at ? `Latest data is from ${data.latest_observed_at}.` : "");
      el("picks-note").hidden = !note;
      el("picks-note").textContent = note;
    } catch (err) {
      state.picks = [];
      renderPicks();
      el("picks-note").hidden = false;
      el("picks-note").textContent = err.message;
    }
  }

  el("more-picks").addEventListener("click", fetchPicks);
  el("city").addEventListener("change", () => {
    if (state.category) showPicks();
  });

  el("picks").addEventListener("click", (event) => {
    const compareBtn = event.target.closest("[data-compare]");
    if (compareBtn) {
      addToCompare(compareBtn.dataset.compare);
      return;
    }
    const askBtn = event.target.closest("[data-ask]");
    if (askBtn) {
      const item = state.picks.find((p) => p.asin === askBtn.dataset.ask);
      if (item) askAbout(item);
    }
  });

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
    const item = state.picks.find((p) => p.asin === asin);
    if (!item || inCompare(asin)) return;
    if (state.compare.length >= MAX_COMPARE) {
      alert(`You can compare up to ${MAX_COMPARE} products. Remove one to add another.`);
      return;
    }
    state.compare.push(snapshot(item));
    writeList(COMPARE_KEY, state.compare);
    renderCompareCount();
    renderPicks();
  }

  function removeFromCompare(asin) {
    state.compare = state.compare.filter((item) => item.asin !== asin);
    writeList(COMPARE_KEY, state.compare);
    renderCompareCount();
    renderPicks();
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
