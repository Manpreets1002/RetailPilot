// "Ask RetailPilot" chat dropdown. Opens from the RetailPilot button and closes on outside click, Escape or ">".
// Each page sets its role, context and greeting through the #assistant-config JSON block.
// Answers stream in as they are generated. With history enabled, previous chats are listed and reopened.
(function () {
  "use strict";

  const configEl = document.getElementById("assistant-config");
  if (!configEl) return;
  const config = JSON.parse(configEl.textContent);

  const el = (id) => document.getElementById(id);
  const dock = el("rp-dock");
  const panel = el("rp-panel");
  const toggle = el("rp-toggle");
  const messagesBox = el("rp-messages");
  const input = el("rp-input");
  const pendingNote = el("rp-pending");
  const threadSelect = el("rp-threads");
  const storageKey = `rp-active-thread-${config.role}`;
  let history = [];
  let threadId = null;
  let pending = null;
  let busy = false;

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Escape first, then apply the few markdown forms the assistant uses: bold, italics, links, bullets, paragraphs.
  function inline(text) {
    return text
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  // Handles headings, bullets, paragraphs and pipe tables. Blank lines between table rows are tolerated.
  function markdown(text) {
    const lines = esc(text).split(/\r?\n/);
    const out = [];
    let inList = false;
    const closeList = () => {
      if (inList) { out.push("</ul>"); inList = false; }
    };
    const isRow = (l) => l.startsWith("|");
    const cells = (row) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();

      if (isRow(line)) {
        let sep = i + 1;
        while (sep < lines.length && !lines[sep].trim()) sep++;
        if (sep < lines.length && /^\|[\s|:-]+$/.test(lines[sep].trim())) {
          closeList();
          const head = cells(line).map((c) => `<th>${inline(c)}</th>`).join("");
          const rows = [];
          let last = sep;
          for (let k = sep + 1; k < lines.length; k++) {
            const row = lines[k].trim();
            if (!row) continue;
            if (!isRow(row)) break;
            rows.push(`<tr>${cells(row).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`);
            last = k;
          }
          out.push(`<div class="rp-table"><table><thead><tr>${head}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`);
          i = last;
          continue;
        }
      }

      const heading = /^#{1,6}\s+(.*)$/.exec(line);
      if (heading) {
        closeList();
        out.push(`<h4>${inline(heading[1])}</h4>`);
        continue;
      }

      const bullet = /^[*-]\s+(.*)$/.exec(line);
      if (bullet) {
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push(`<li>${inline(bullet[1])}</li>`);
        continue;
      }
      closeList();
      if (line) out.push(`<p>${inline(line)}</p>`);
    }
    closeList();
    return out.join("");
  }

  // While the answer has not started, show a fetching label with blinking dots.
  function bubbleHtml(message) {
    if (message.role !== "assistant") return esc(message.content);
    if (message.streaming && !message.content) {
      return '<span class="thinking-label">RetailPilot is fetching your answer</span>' +
        '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>';
    }
    return markdown(message.content);
  }

  function greetingMessages() {
    return config.greeting ? [{ role: "assistant", content: config.greeting }] : [];
  }

  function render() {
    messagesBox.innerHTML = history
      .map((m) => `<div class="msg ${m.role}${m.streaming ? " streaming" : ""}">${bubbleHtml(m)}</div>`)
      .join("");
    messagesBox.scrollTop = messagesBox.scrollHeight;
    pendingNote.hidden = !pending;
    pendingNote.textContent = pending ? "RetailPilot asks: " + pending : "";
    input.placeholder = pending ? "Answer RetailPilot (Enter to send)" : "Ask a question (Enter to send, Shift+Enter for a new line)";
  }

  function setCollapsed(collapsed) {
    panel.hidden = collapsed;
    toggle.setAttribute("aria-expanded", String(!collapsed));
    // The panel was hidden while messages arrived, so jump to the latest one when it opens.
    if (!collapsed) messagesBox.scrollTop = messagesBox.scrollHeight;
  }

  function rememberThread(id) {
    try {
      if (id) localStorage.setItem(storageKey, id);
      else localStorage.removeItem(storageKey);
    } catch (e) { /* storage may be blocked; the chat then restarts on reload */ }
  }

  function startNewChat() {
    threadId = null;
    pending = null;
    history = greetingMessages();
    rememberThread(null);
    if (threadSelect) threadSelect.value = "";
    render();
  }

  async function openThread(id) {
    const response = await fetch(`/api/threads/${encodeURIComponent(id)}/messages/`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Could not load that chat.");
    const messages = data.messages || [];
    threadId = id;
    pending = null;
    // Internal context added before the first message is not shown again.
    const visible = (text) => text
      .replace(/^My delivery city is [^.]*\.\s*/, "")
      .replace(/^I am looking at [^\n]*\n\n/, "")
      .replace(/^I am comparing [\s\S]*?\n\n/, "");
    history = messages.map((m) => ({
      role: m.author === "user" ? "user" : "assistant",
      content: m.author === "user" ? visible(m.content) : m.content,
    }));
    const last = messages[messages.length - 1];
    if (last && last.kind === "question") pending = last.content;
    rememberThread(id);
    if (threadSelect) threadSelect.value = id;
    render();
  }

  async function loadThreadList(selectId) {
    const response = await fetch("/api/threads/");
    const data = await response.json();
    const threads = (data.threads || []).filter((t) => !t.role || t.role === config.role);
    threadSelect.innerHTML = '<option value="">Previous chats</option>' + threads
      .map((t) => `<option value="${esc(t.thread_id)}">${esc((t.title || "Untitled chat").slice(0, 44))}</option>`)
      .join("");
    if (selectId && threads.some((t) => t.thread_id === selectId)) threadSelect.value = selectId;
    return threads;
  }

  async function restoreLastChat() {
    let saved = null;
    try { saved = localStorage.getItem(storageKey); } catch (e) { /* ignore */ }
    const threads = await loadThreadList(saved);
    const target = threads.find((t) => t.thread_id === saved) || threads[0];
    if (target) await openThread(target.thread_id);
    else startNewChat();
  }

  // Reads the server-sent events from the backend and passes each one to onEvent.
  async function streamRequest(url, body, onEvent) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Request failed");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        for (const line of frame.split("\n")) {
          if (line.startsWith("data: ")) onEvent(JSON.parse(line.slice(6)));
        }
        end = buffer.indexOf("\n\n");
      }
    }
  }

  function handleEvent(event, reply) {
    switch (event.type) {
      case "start":
        threadId = event.thread_id;
        rememberThread(threadId);
        break;
      case "token":
        reply.content += event.text;
        break;
      case "reset":
        reply.content = "";
        break;
      case "question":
        pending = event.question;
        reply.content = event.question;
        setCollapsed(false);
        break;
      case "done":
        pending = null;
        reply.content = event.reply || reply.content;
        break;
      default:
        break;
    }
    render();
  }

  async function send(extraContext) {
    const text = input.value.trim();
    if (!text || busy) return;
    input.value = "";
    setCollapsed(false);
    history.push({ role: "user", content: text });
    const reply = { role: "assistant", content: "", streaming: true };
    history.push(reply);
    busy = true;
    render();

    try {
      const onEvent = (event) => handleEvent(event, reply);
      if (pending) {
        await streamRequest(`/api/chat/${encodeURIComponent(threadId)}/answer/stream/`, { answer: text }, onEvent);
      } else {
        // The page context (for example, which product is open) goes in with the first message only.
        const context = extraContext || (!threadId && config.context ? config.context : null);
        const message = context ? `${context}\n\n${text}` : text;
        const body = { message, thread_id: threadId, role: config.role };
        const citySelect = config.citySelect && el(config.citySelect);
        if (citySelect) body.city = citySelect.value;
        await streamRequest("/api/chat/stream/", body, onEvent);
      }
      reply.streaming = false;
      if (config.history) loadThreadList(threadId).catch(() => {});
    } catch (err) {
      reply.content = "Sorry, I could not answer: " + err.message;
      reply.streaming = false;
    } finally {
      busy = false;
      render();
    }
  }

  el("rp-form").addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });

  // Enter sends; Shift+Enter inserts a new line.
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });

  toggle.addEventListener("click", () => setCollapsed(!panel.hidden));

  // Drag the left edge to choose a width (remembered), or use the expand button to fill the screen and back.
  const resizeBtn = el("rp-resize");
  const grip = el("rp-grip");
  const widthKey = "rp-width";
  const minWidth = 320;
  let customWidth = null;
  let expanded = false;
  try { customWidth = Number(localStorage.getItem(widthKey)) || null; } catch (e) { /* storage may be blocked */ }

  const isWide = () => window.innerWidth > 760;
  const clampWidth = (px) => Math.round(Math.max(minWidth, Math.min(px, window.innerWidth - 40)));

  function applyWidth() {
    const wide = isWide();
    panel.classList.toggle("rp-expanded", expanded && wide);
    panel.style.width = !expanded && customWidth && wide ? `${customWidth}px` : "";
    const label = expanded ? "Restore RetailPilot size" : "Expand RetailPilot";
    resizeBtn.setAttribute("aria-pressed", String(expanded));
    resizeBtn.setAttribute("aria-label", label);
    resizeBtn.title = label;
  }

  resizeBtn.addEventListener("click", () => {
    expanded = !expanded;
    applyWidth();
  });

  grip.addEventListener("pointerdown", (event) => {
    if (!isWide()) return;
    event.preventDefault();
    expanded = false;
    grip.setPointerCapture(event.pointerId);
    grip.classList.add("rp-dragging");
    applyWidth();
  });

  grip.addEventListener("pointermove", (event) => {
    if (!grip.hasPointerCapture(event.pointerId)) return;
    customWidth = clampWidth(window.innerWidth - event.clientX);
    applyWidth();
  });

  const endDrag = (event) => {
    if (!grip.hasPointerCapture(event.pointerId)) return;
    grip.releasePointerCapture(event.pointerId);
    grip.classList.remove("rp-dragging");
    try { localStorage.setItem(widthKey, String(customWidth)); } catch (e) { /* ignore */ }
  };
  grip.addEventListener("pointerup", endDrag);
  grip.addEventListener("pointercancel", endDrag);

  window.addEventListener("resize", applyWidth);
  applyWidth();

  el("rp-close").addEventListener("click", () => {
    setCollapsed(true);
    toggle.focus();
  });

  // pointerdown rather than click, so a product's "Ask RetailPilot" button can still reopen the panel after closing it.
  document.addEventListener("pointerdown", (event) => {
    if (!panel.hidden && !dock.contains(event.target)) setCollapsed(true);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      setCollapsed(true);
      toggle.focus();
    }
  });

  // Product "Ask RetailPilot" buttons on the shop page send their question with the product as context.
  document.addEventListener("rp:ask", (event) => {
    input.value = event.detail.question;
    send(event.detail.context);
  });

  setCollapsed(true);

  if (config.history && threadSelect) {
    el("rp-history").hidden = false;
    threadSelect.addEventListener("change", () => {
      if (threadSelect.value) openThread(threadSelect.value).catch((err) => alert(err.message));
    });
    el("rp-new").addEventListener("click", startNewChat);
    restoreLastChat().catch(() => startNewChat());
  } else {
    startNewChat();
  }
})();
