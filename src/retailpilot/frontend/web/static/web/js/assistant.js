// Floating "Ask RetailPilot" chat. Shared by the retailer dashboard and the customer shop.
// Each page sets its role, context and greeting through the #assistant-config JSON block.
(function () {
  "use strict";

  const configEl = document.getElementById("assistant-config");
  if (!configEl) return;
  const config = JSON.parse(configEl.textContent);

  const el = (id) => document.getElementById(id);
  const panel = el("rp-panel");
  const messagesBox = el("rp-messages");
  const input = el("rp-input");
  const pendingNote = el("rp-pending");
  const history = config.greeting ? [{ role: "assistant", content: config.greeting }] : [];
  let threadId = null;
  let pending = null;

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
    pendingNote.hidden = !pending;
    pendingNote.textContent = pending ? "RetailPilot asks: " + pending : "";
    input.placeholder = pending ? "Answer RetailPilot" : "Ask a question";
  }

  function setOpen(open) {
    panel.hidden = !open;
    if (open) {
      render();
      input.focus();
    }
  }

  el("rp-fab").addEventListener("click", () => setOpen(panel.hidden));
  el("rp-close").addEventListener("click", () => setOpen(false));

  el("rp-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    history.push({ role: "user", content: text });
    render();

    try {
      let url;
      let body;
      if (pending) {
        url = `/api/chat/${encodeURIComponent(threadId)}/answer/`;
        body = { answer: text };
      } else {
        url = "/api/chat/";
        // The page context (for example, which product is open) goes in with the first message only.
        const message = !threadId && config.context ? `${config.context}\n\n${text}` : text;
        body = { message, thread_id: threadId, role: config.role };
        const citySelect = config.citySelect && el(config.citySelect);
        if (citySelect) body.city = citySelect.value;
      }
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
