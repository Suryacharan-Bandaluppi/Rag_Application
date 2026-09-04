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

  const activeModeBadge = document.getElementById("activeModeBadge");
  const hitRateVal = document.getElementById("hitRateVal");
  const hitRateLabel = document.getElementById("hitRateLabel");
  const hitRateSub = document.getElementById("hitRateSub");
  const latencyVal = document.getElementById("latencyVal");
  const latencySub = document.getElementById("latencySub");
  const engineModeVal = document.getElementById("engineModeVal");
  const engineModeSub = document.getElementById("engineModeSub");

  let currentUseReranker = true;

  async function fetchSystemMode() {
    try {
      const res = await fetch("/api/mode");
      if (res.ok) {
        const data = await res.json();
        currentUseReranker = data.use_reranker;

        if (activeModeBadge) {
          activeModeBadge.textContent = data.use_reranker ? "Enhanced Mode (100.0%)" : "Baseline Mode (66.7%)";
          activeModeBadge.style.backgroundColor = data.use_reranker ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)";
          activeModeBadge.style.borderColor = data.use_reranker ? "#10b981" : "#ef4444";
          activeModeBadge.style.color = data.use_reranker ? "#10b981" : "#ef4444";
        }

        if (hitRateVal) {
          hitRateVal.textContent = data.hit_rate;
          hitRateVal.className = data.use_reranker ? "metric-value text-green" : "metric-value";
        }
        if (hitRateLabel) hitRateLabel.textContent = `Hit-Rate@3 (${data.use_reranker ? "Enhanced" : "Baseline"})`;
        if (hitRateSub) hitRateSub.textContent = data.description;
        if (latencyVal) latencyVal.textContent = data.p50_latency;
        if (latencySub) latencySub.textContent = data.use_reranker ? "Virtually Zero Overhead" : "Pure Vector Cosine Search";
        if (engineModeVal) engineModeVal.textContent = data.use_reranker ? "Cross-Encoder" : "Dense Vector";
        if (engineModeSub) engineModeSub.textContent = data.mode_name;
      }
    } catch (err) {
      console.error("Failed to fetch system mode:", err);
    }
  }

  // Poll system mode on page load and every 2 seconds
  fetchSystemMode();
  setInterval(fetchSystemMode, 2000);

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

  // View Tab Switching
  const tabChatBtn = document.getElementById("tabChatBtn");
  const tabInspectBtn = document.getElementById("tabInspectBtn");
  const inspectContainer = document.getElementById("inspectContainer");
  const chatMessagesEl = document.getElementById("chatMessages");
  const chatInputAreaEl = document.querySelector(".chat-input-area");

  if (tabChatBtn && tabInspectBtn) {
    tabChatBtn.addEventListener("click", () => {
      tabChatBtn.classList.add("active");
      tabInspectBtn.classList.remove("active");
      inspectContainer.classList.add("hidden");
      chatMessagesEl.classList.remove("hidden");
      chatInputAreaEl.classList.remove("hidden");
    });

    tabInspectBtn.addEventListener("click", () => {
      tabInspectBtn.classList.add("active");
      tabChatBtn.classList.remove("active");
      inspectContainer.classList.remove("hidden");
      chatMessagesEl.classList.add("hidden");
      chatInputAreaEl.classList.add("hidden");
    });
  }

  // Diagnostic Inspection Form Logic
  const inspectForm = document.getElementById("inspectForm");
  const inspectQueryInput = document.getElementById("inspectQueryInput");
  const inspectBtn = document.getElementById("inspectBtn");
  const inspectResults = document.getElementById("inspectResults");

  if (inspectForm) {
    inspectForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const q = inspectQueryInput.value.trim();
      if (q) runInspection(q);
    });

    document.addEventListener("click", (e) => {
      if (e.target.classList.contains("golden-chip")) {
        const q = e.target.getAttribute("data-q");
        if (q) {
          inspectQueryInput.value = q;
          runInspection(q);
        }
      }
    });
  }

  async function runInspection(query) {
    inspectBtn.disabled = true;
    inspectResults.className = "inspect-results-card";
    inspectResults.innerHTML = "<p><em>Retrieving top-3 chunks & analyzing failure mode...</em></p>";
    inspectResults.classList.remove("hidden");

    try {
      const res = await fetch("/api/inspect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query, top_k: 3 })
      });

      const data = await res.json();
      if (!res.ok) {
        inspectResults.innerHTML = `<p class="text-danger">Error: ${escapeHtml(data.detail || "Server error")}</p>`;
        return;
      }

      let tagClass = "tag-ok";
      let tagText = "✅ OK (Success)";
      if (data.failure_tag === "R") {
        tagClass = "tag-r";
        tagText = "❌ R (Retrieval Failure)";
      } else if (data.failure_tag === "G") {
        tagClass = "tag-g";
        tagText = "⚠️ G (Generation Failure)";
      }

      let chunksHtml = "";
      if (data.top_3_chunks && data.top_3_chunks.length > 0) {
        chunksHtml = data.top_3_chunks.map((c, i) => `
          <div class="chunk-inspect-box">
            <div class="chunk-header">
              <span>Top Chunk #${i+1} [ID: ${escapeHtml(c.id || "N/A")}]</span>
              <span>Score: ${(c.score ?? 0).toFixed(3)} | Source: ${escapeHtml(c.source || "Unknown")}</span>
            </div>
            <p>${escapeHtml(c.text || "")}</p>
          </div>
        `).join("");
      } else {
        chunksHtml = "<p><em>No candidate chunks returned.</em></p>";
      }

      const modeText = data.use_reranker ? "Cross-Encoder Reranked" : "Pure Dense Vector Cosine";

      inspectResults.innerHTML = `
        <div class="result-tag-row">
          <div>
            <strong>Diagnostic Inspection Result (${escapeHtml(modeText)})</strong>
            <p class="section-desc">Query: "${escapeHtml(data.query)}"</p>
          </div>
          <span class="tag-badge ${tagClass}">${tagText}</span>
        </div>

        <div>
          <h4>1-Line Evidence Proof:</h4>
          <p class="section-desc" style="color: #a5b4fc; font-weight: 500;">${escapeHtml(data.evidence || "N/A")}</p>
        </div>

        <div>
          <h4>Top-3 Retrieved Chunks (${escapeHtml(modeText)}):</h4>
          ${chunksHtml}
        </div>

        <div style="border-top: 1px solid var(--border-color); padding-top: 12px;">
          <h4>Generated LLM Answer:</h4>
          <p style="margin-top: 6px;">${formatMarkdown(data.answer || "")}</p>
        </div>
      `;
    } catch (err) {
      inspectResults.innerHTML = `<p class="text-danger">Network Error: ${escapeHtml(err.message)}</p>`;
    } finally {
      inspectBtn.disabled = false;
    }
  }
});
