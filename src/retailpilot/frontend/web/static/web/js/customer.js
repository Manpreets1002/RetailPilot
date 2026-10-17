// Customer shop: pick a category, see best picks for the delivery city, and keep a cart.
(function () {
  "use strict";

  const CART_KEY = "rp-cart";
  const el = (id) => document.getElementById(id);
  const state = {
    category: null,
    picks: [],
    cart: loadCart(),
  };

  // ------------------------------------------------------------ helpers

  function loadCart() {
    try {
      return JSON.parse(localStorage.getItem(CART_KEY) || "[]");
    } catch (e) {
      return [];
    }
  }

  function saveCart() {
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(state.cart));
    } catch (e) {
      /* storage may be blocked; the cart then lasts for this page visit only */
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

  function inCart(asin) {
    return state.cart.some((item) => item.asin === asin);
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
          ${inCart(item.asin)
            ? '<button class="btn btn-small" type="button" disabled>In cart ✓</button>'
            : `<button class="btn btn-small" type="button" data-add="${esc(item.asin)}">Add to cart <span aria-hidden="true">+</span></button>`}
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
    const button = event.target.closest("[data-add]");
    if (!button) return;
    addToCart(button.dataset.add);
  });

  // ------------------------------------------------------------ cart

  function addToCart(asin) {
    const item = state.picks.find((p) => p.asin === asin);
    if (!item || inCart(asin)) return;
    state.cart.push({ asin: item.asin, title: item.title, brand: item.brand, price: item.price, url: item.url });
    saveCart();
    renderCart();
    renderPicks();
    // Tell the recommender the shopper wants this product. Failure here does not affect the cart.
    api("/api/interest/", { method: "POST", body: { asin, interest: "buy" } }).catch(() => {});
  }

  function removeFromCart(asin) {
    state.cart = state.cart.filter((item) => item.asin !== asin);
    saveCart();
    renderCart();
    renderPicks();
  }

  function renderCart() {
    el("cart-count").textContent = state.cart.length;
    const total = state.cart.reduce((sum, item) => sum + (Number(item.price) || 0), 0);
    el("cart-total").textContent = money(total);
    el("cart-items").innerHTML = state.cart.length
      ? state.cart.map((item) => `
          <div class="cart-item">
            <div class="title">${esc((item.title || item.asin).slice(0, 80))}</div>
            <div class="meta">${money(item.price)}</div>
            <div class="actions">
              <a class="btn btn-ghost btn-small" href="${esc(amazonUrl(item))}" target="_blank" rel="noopener">Open on Amazon</a>
              <button class="btn btn-ghost btn-small" type="button" data-remove="${esc(item.asin)}">Remove</button>
            </div>
          </div>`).join("")
      : '<p class="fine">Your cart is empty. Add picks from the shop to see them here.</p>';
  }

  function closeCart() {
    el("cart-drawer").hidden = true;
    el("cart-btn").focus();
  }

  el("cart-btn").addEventListener("click", () => {
    el("cart-drawer").hidden = false;
    el("close-cart").focus();
  });
  el("close-cart").addEventListener("click", closeCart);
  el("cart-drawer").addEventListener("click", (event) => {
    if (event.target === el("cart-drawer")) { closeCart(); return; }
    const button = event.target.closest("[data-remove]");
    if (button) removeFromCart(button.dataset.remove);
  });
  el("clear-cart").addEventListener("click", () => {
    state.cart = [];
    saveCart();
    renderCart();
    renderPicks();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el("cart-drawer").hidden) closeCart();
  });

  // ------------------------------------------------------------ start

  async function init() {
    renderCart();
    try {
      const cities = (await api("/api/cities/")).cities.filter((c) => c.covered);
      el("city").innerHTML = cities.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("")
        || '<option value="">No city has data today</option>';
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
