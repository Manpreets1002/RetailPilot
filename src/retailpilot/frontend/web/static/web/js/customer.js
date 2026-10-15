(function () {
  "use strict";

  const IDLE_MS = 5 * 60 * 1000;
  const CHECK_MS = 15 * 1000;
  const shopper = document.getElementById("shop").dataset.shopper;

  const el = (id) => document.getElementById(id);
  const state = {
    picks: [],
    pickCategory: null,
    threadId: null,
    pending: null,
    messages: [],
    lastActivity: Date.now(),
    nudged: false,
    nudgeItem: null,
  };

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

  // ------------------------------------------------------------ theme

  function applyTheme(mode) {
    document.body.classList.toggle("theme-dark", mode === "dark");
    document.body.classList.toggle("theme-light", mode !== "dark");
    el("theme-toggle").textContent = mode === "dark" ? "Light mode" : "Dark mode";
    try { localStorage.setItem("rp-theme", mode); } catch (e) { /* storage may be blocked */ }
  }

  el("theme-toggle").addEventListener("click", () => {
    applyTheme(document.body.classList.contains("theme-dark") ? "light" : "dark");
  });

  // ------------------------------------------------------------ view switching

  function showHome() {
    el("home").hidden = false;
    el("chat").hidden = true;
  }

  function showChat() {
    el("home").hidden = true;
    el("chat").hidden = false;
  }

  function renderMessages() {
    const box = el("messages");
    box.innerHTML = state.messages
      .map((m) => `<div class="msg ${m.role}">${esc(m.content)}</div>`)
      .join("");
    box.scrollTop = box.scrollHeight;
    el("pending").hidden = !state.pending;
    el("pending").textContent = state.pending ? "RetailPilot asks: " + state.pending : "";
    el("chat-input").placeholder = state.pending ? "Answer RetailPilot" : "Ask about a product, price or pros and cons";
  }

  // ------------------------------------------------------------ threads

  async function loadThreads() {
    try {
      const data = await api("/api/threads/");
      const list = el("thread-list");
      if (!data.threads.length) {
        list.innerHTML = '<p class="fine">Your chats will appear here.</p>';
        return;
      }
      list.innerHTML = data.threads
        .map((t) => `<button class="thread${t.thread_id === state.threadId ? " active" : ""}" data-id="${esc(t.thread_id)}" type="button">${esc((t.title || "Untitled chat").slice(0, 40))}</button>`)
        .join("");
    } catch (err) {
      el("thread-list").innerHTML = `<p class="fine">${esc(err.message)}</p>`;
    }
  }

  el("thread-list").addEventListener("click", async (event) => {
    const button = event.target.closest(".thread");
    if (!button) return;
    try {
      const data = await api(`/api/threads/${encodeURIComponent(button.dataset.id)}/messages/`);
      state.threadId = button.dataset.id;
      state.messages = data.messages.map((m) => ({ role: m.author === "user" ? "user" : "assistant", content: m.content }));
      const last = data.messages[data.messages.length - 1];
      state.pending = last && last.kind === "question" ? last.content : null;
      state.picks = [];
      showChat();
      renderMessages();
      loadThreads();
    } catch (err) {
      alert(err.message);
    }
  });

  el("new-chat").addEventListener("click", () => {
    state.threadId = null;
    state.messages = [];
    state.pending = null;
    showHome();
    loadThreads();
  });

  // ------------------------------------------------------------ best picks

  function renderPicks() {
    const box = el("picks");
    box.innerHTML = state.picks.map((item) => `
      <div class="pick" data-asin="${esc(item.asin)}">
        <div class="meta">${esc(item.brand || "")}</div>
        <div class="title">${esc((item.title || "").slice(0, 90))}</div>
        <div class="price">${money(item.price)} <span class="meta">MRP ${money(item.mrp)}${item.discount_pct ? " · " + item.discount_pct + "% off" : ""}</span></div>
        <div class="meta">${item.rating ? item.rating.toFixed(1) + " ★ (" + Number(item.rating_count || 0).toLocaleString("en-IN") + ")" : "No ratings yet"} · Delivery ${esc(item.delivery_date || "see listing")}</div>
        <div class="actions">
          ${item.url ? `<a class="btn btn-ghost" href="${esc(item.url)}" target="_blank" rel="noopener">View on Amazon</a>` : ""}
          <button class="btn buy-btn" type="button">I want to buy</button>
        </div>
      </div>`).join("");
    el("picks-actions").hidden = state.picks.length === 0;
    el("picks").hidden = state.picks.length === 0;
  }

  async function fetchPicks() {
    const category = el("category").value;
    const city = el("city").value;
    el("picks-note").hidden = true;
    try {
      const data = await api(`/api/recommendations/?category=${encodeURIComponent(category)}&city=${encodeURIComponent(city)}`);
      state.picks = data.items || [];
      state.pickCategory = category;
      renderPicks();
      const note = data.note || (!data.items.length && data.latest_observed_at ? `Latest data is from ${data.latest_observed_at}.` : "");
      el("picks-note").hidden = !note;
      el("picks-note").textContent = note;
    } catch (err) {
      state.picks = [];
      renderPicks();
      el("picks-note").hidden = false;
      el("picks-note").textContent = err.message;
    }
  }

  el("show-picks").addEventListener("click", fetchPicks);
  el("more-picks").addEventListener("click", fetchPicks);

  el("picks").addEventListener("click", async (event) => {
    const button = event.target.closest(".buy-btn");
    if (!button) return;
    const asin = button.closest(".pick").dataset.asin;
    await setInterest(asin, "buy");
    button.textContent = "Saved ✓";
    button.disabled = true;
  });

  async function setInterest(asin, interest) {
    try {
      await api("/api/interest/", { method: "POST", body: { asin, interest } });
    } catch (err) {
      alert(err.message);
    }
  }

  // ------------------------------------------------------------ chat

  el("chat-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = el("chat-input");
    const text = input.value.trim();
    if (!text) return;
    markActive();
    input.value = "";
    state.picks = [];
    renderPicks();
    el("picks-note").hidden = true;
    showChat();
    state.messages.push({ role: "user", content: text });
    renderMessages();
    try {
      const data = state.pending
        ? await api(`/api/chat/${encodeURIComponent(state.threadId)}/answer/`, { method: "POST", body: { answer: text } })
        : await api("/api/chat/", { method: "POST", body: { message: text, thread_id: state.threadId, role: "customer", city: el("city").value } });
      state.threadId = data.thread_id;
      state.pending = data.question || null;
      state.messages.push({ role: "assistant", content: data.question || data.reply || "" });
    } catch (err) {
      state.messages.push({ role: "assistant", content: "Sorry, I could not answer: " + err.message });
    }
    renderMessages();
    loadThreads();
  });

  function markActive() {
    state.lastActivity = Date.now();
    hideNudge();
  }

  // ------------------------------------------------------------ (i) panel

  async function renderShown() {
    const list = el("shown-list");
    list.innerHTML = '<p class="fine">Loading…</p>';
    try {
      const data = await api("/api/shown/");
      if (!data.shown.length) {
        list.innerHTML = '<p class="fine">Nothing shown yet. Pick a category to see today\'s best.</p>';
        return;
      }
      list.innerHTML = data.shown.map((item) => {
        const status = item.interest === "buy" ? '<p class="fine">You want to buy this</p>'
          : item.interest === "skip" ? '<p class="fine">Not for you</p>'
          : `<div class="actions"><button class="btn" data-act="buy" data-asin="${esc(item.asin)}" type="button">I want to buy</button>
             <button class="btn btn-ghost" data-act="skip" data-asin="${esc(item.asin)}" type="button">Not for me</button></div>`;
        return `<div class="shown-item"><b>${esc((item.title || item.asin).slice(0, 80))}</b><br>${money(item.price)} · ${esc(item.city || "")}
          ${item.url ? `<br><a href="${esc(item.url)}" target="_blank" rel="noopener">View on Amazon</a>` : ""}${status}</div>`;
      }).join("");
    } catch (err) {
      list.innerHTML = `<p class="fine">${esc(err.message)}</p>`;
    }
  }

  el("shown-btn").addEventListener("click", () => {
    el("shown-drawer").hidden = false;
    renderShown();
  });
  el("close-drawer").addEventListener("click", () => { el("shown-drawer").hidden = true; });
  el("shown-drawer").addEventListener("click", async (event) => {
    if (event.target === el("shown-drawer")) { el("shown-drawer").hidden = true; return; }
    const button = event.target.closest("[data-act]");
    if (!button) return;
    await setInterest(button.dataset.asin, button.dataset.act);
    renderShown();
  });

  // ------------------------------------------------------------ idle nudge

  function hideNudge() {
    el("nudge").hidden = true;
  }

  function checkIdle() {
    if (state.nudged || !state.picks.length) return;
    if (Date.now() - state.lastActivity < IDLE_MS) return;
    state.nudged = true;
    state.nudgeItem = state.picks[0];
    el("nudge-body").innerHTML = `<div>${esc((state.nudgeItem.title || "").slice(0, 70))}</div><b>${money(state.nudgeItem.price)}</b>`;
    el("nudge").hidden = false;
  }

  ["click", "keydown", "mousemove", "scroll", "touchstart"].forEach((name) => {
    document.addEventListener(name, () => {
      state.lastActivity = Date.now();
      state.nudged = false;
    }, { passive: true });
  });

  el("nudge-buy").addEventListener("click", async () => {
    if (state.nudgeItem) await setInterest(state.nudgeItem.asin, "buy");
    hideNudge();
  });
  el("nudge-dismiss").addEventListener("click", hideNudge);

  setInterval(checkIdle, CHECK_MS);

  // ------------------------------------------------------------ start

  async function init() {
    let saved = "light";
    try { saved = localStorage.getItem("rp-theme") || "light"; } catch (e) { /* ignore */ }
    applyTheme(saved);
    try {
      const cities = (await api("/api/cities/")).cities.filter((c) => c.covered);
      el("city").innerHTML = cities.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("");
      if (!cities.length) el("city").innerHTML = '<option value="">No city has data today</option>';
    } catch (err) {
      el("city").innerHTML = '<option value="">Unavailable</option>';
    }
    try {
      const categories = (await api("/api/categories/")).categories;
      el("category").innerHTML = categories.map((c) => `<option value="${esc(c.name)}">${esc(c.name)}</option>`).join("");
    } catch (err) {
      el("category").innerHTML = '<option value="">Unavailable</option>';
    }
    loadThreads();
    showHome();
  }

  init();
})();
