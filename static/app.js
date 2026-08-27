document.addEventListener("DOMContentLoaded", () => {
  // DOM Elements
  const dropZone = document.getElementById("dropZone");
  const fileInput = document.getElementById("fileInput");
  const uploadStatus = document.getElementById("uploadStatus");
  const chunkSizeSelect = document.getElementById("chunkSizeSelect");
  const topKSelect = document.getElementById("topKSelect");
  const totalChunksVal = document.getElementById("totalChunksVal");
  const storeTypeVal = document.getElementById("storeTypeVal");
  const clearDbBtn = document.getElementById("clearDbBtn");

  const chatMessages = document.getElementById("chatMessages");
  const chatForm = document.getElementById("chatForm");
  const queryInput = document.getElementById("queryInput");
  const sendBtn = document.getElementById("sendBtn");

  // Load Initial DB Stats
  fetchStats();

  // Drag & Drop Handlers
  dropZone.addEventListener("click", () => fileInput.click());

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragover");
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) {
      handleFileUpload(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files.length > 0) {
      handleFileUpload(e.target.files[0]);
    }
  });

  // Upload File to Backend API
  async function handleFileUpload(file) {
    const formData = new FormData();
    formData.append("file", file);

    const chunkSize = chunkSizeSelect.value;
    showStatus("Uploading and vectorizing document...", "info");

    try {
      const res = await fetch(`/api/upload?chunk_size=${chunkSize}`, {
        method: "POST",
        body: formData,
      });

      const data = await res.json();
      if (res.ok) {
        showStatus(`Indexed ${data.chunks_indexed} chunks from "${file.name}"`, "success");
        fetchStats();
      } else {
        showStatus(`Upload failed: ${data.detail || "Error processing document"}`, "error");
      }
    } catch (err) {
      showStatus(`Network error: ${err.message}`, "error");
    } finally {
      fileInput.value = "";
    }
  }

  function showStatus(message, type) {
    uploadStatus.textContent = message;
    uploadStatus.className = `upload-status ${type}`;
    uploadStatus.classList.remove("hidden");
    setTimeout(() => {
      if (type === "success") uploadStatus.classList.add("hidden");
    }, 4000);
  }

  // Fetch Vector DB Stats
  async function fetchStats() {
    try {
      const res = await fetch("/api/stats");
      if (res.ok) {
        const data = await res.json();
        totalChunksVal.textContent = data.total_chunks ?? 0;
        storeTypeVal.textContent = data.type ? data.type.toUpperCase() : "InMemory";
      }
    } catch (err) {
      console.error("Failed to load vector store stats:", err);
    }
  }

  // Clear Vector Store
  clearDbBtn.addEventListener("click", async () => {
    if (!confirm("Are you sure you want to clear all document vectors?")) return;
    try {
      const res = await fetch("/api/clear", { method: "DELETE" });
      if (res.ok) {
        alert("Knowledgebase vector store cleared!");
        fetchStats();
      }
    } catch (err) {
      alert("Error clearing vector store: " + err.message);
    }
  });

  // Sample Query Chips Handler
  document.addEventListener("click", (e) => {
    if (e.target.classList.contains("chip-btn")) {
      const queryText = e.target.getAttribute("data-query");
      if (queryText) {
        queryInput.value = queryText;
        submitQuery(queryText);
      }
    }
  });

  // Submit Query to Backend RAG API
  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = queryInput.value.trim();
    if (!query) return;
    submitQuery(query);
  });

  async function submitQuery(query) {
    appendMessage(query, "user");
    queryInput.value = "";
    sendBtn.disabled = true;

    const typingId = appendTypingIndicator();

    try {
      const topK = parseInt(topKSelect.value) || 5;
      const res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query, top_k: topK }),
      });

      const data = await res.json();
      removeMessage(typingId);

      if (res.ok) {
        appendAssistantMessage(data.answer, data.sources || []);
      } else {
        appendAssistantMessage(`⚠️ Request failed: ${data.detail || "Server error"}`, []);
      }
    } catch (err) {
      removeMessage(typingId);
      appendAssistantMessage(`⚠️ Network error: ${err.message}`, []);
    } finally {
      sendBtn.disabled = false;
    }
  }

  function appendMessage(text, sender) {
    const msgDiv = document.createElement("div");
    msgDiv.className = `message ${sender}-message`;
    msgDiv.innerHTML = `
      <div class="message-avatar">${sender === "user" ? "👤" : "🤖"}</div>
      <div class="message-content"><p>${escapeHtml(text)}</p></div>
    `;
    chatMessages.appendChild(msgDiv);
    scrollToBottom();
    return msgDiv;
  }

  function appendAssistantMessage(answer, sources) {
    const msgDiv = document.createElement("div");
    msgDiv.className = "message assistant-message";

    const isOutOfContext = answer.includes("I don't know based on the provided");

    let sourcesHtml = "";
    if (sources && sources.length > 0) {
      sourcesHtml = `
        <div class="sources-container">
          <span class="sources-label">Sources:</span>
          ${sources.map((s) => `<span class="source-badge">📄 ${escapeHtml(s)}</span>`).join("")}
        </div>
      `;
    }

    let bodyHtml = `<p>${formatMarkdown(answer)}</p>`;
    if (isOutOfContext) {
      bodyHtml = `
        <div class="out-of-context-warning">
          <strong>⚠️ Out of Context Guardrail Triggered</strong>
          <p>${escapeHtml(answer)}</p>
        </div>
      `;
    }

    msgDiv.innerHTML = `
      <div class="message-avatar">🤖</div>
      <div class="message-content">
        ${bodyHtml}
        ${sourcesHtml}
      </div>
    `;

    chatMessages.appendChild(msgDiv);
    scrollToBottom();
  }

  function appendTypingIndicator() {
    const id = "typing-" + Date.now();
    const msgDiv = document.createElement("div");
    msgDiv.id = id;
    msgDiv.className = "message assistant-message";
    msgDiv.innerHTML = `
      <div class="message-avatar">🤖</div>
      <div class="message-content"><p><em>Searching documents & generating answer...</em></p></div>
    `;
    chatMessages.appendChild(msgDiv);
    scrollToBottom();
    return id;
  }

  function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
  }

  function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatMarkdown(text) {
    return escapeHtml(text)
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }
});
