(function () {
  "use strict";

  const panels = document.querySelectorAll("[data-panel]");
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
      panels.forEach((p) => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
    });
  });

  if (window.Plotly && typeof TREND !== "undefined" && TREND && document.getElementById("trend")) {
    Plotly.newPlot("trend", TREND.data, TREND.layout, { responsive: true, displayModeBar: false });
  }
  if (window.Plotly && typeof HEATMAP !== "undefined" && HEATMAP && document.getElementById("heatmap")) {
    Plotly.newPlot("heatmap", HEATMAP.data, HEATMAP.layout, { responsive: true, displayModeBar: false });
  }

  const chatPanel = document.getElementById("assistant");
  if (!chatPanel) return;
  const context = chatPanel.dataset.context || "";
  const messagesBox = document.getElementById("retailer-messages");
  const form = document.getElementById("retailer-form");
  const input = document.getElementById("retailer-input");
  let threadId = null;
  let pending = null;
  const history = [];

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function esc(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function render() {
    messagesBox.innerHTML = history.map((m) => `<div class="msg ${m.role}">${esc(m.content)}</div>`).join("");
    messagesBox.scrollTop = messagesBox.scrollHeight;
    input.placeholder = pending ? "Answer RetailPilot" : "Ask about this product, a price or your margin";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    history.push({ role: "user", content: text });
    render();
    try {
      const body = pending
        ? { answer: text }
        : { message: `${context}\n\n${text}`, thread_id: threadId, role: "retailer" };
      const url = pending ? `/api/chat/${encodeURIComponent(threadId)}/answer/` : "/api/chat/";
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Request failed");
      threadId = data.thread_id;
      pending = data.question || null;
      history.push({ role: "assistant", content: data.question || data.reply || "" });
    } catch (err) {
      history.push({ role: "assistant", content: "Sorry, I could not answer: " + err.message });
    }
    render();
  });
})();
