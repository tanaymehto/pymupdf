const runBtn          = document.getElementById("runBtn");
const downloadBtn     = document.getElementById("downloadBtn");
const fileInput       = document.getElementById("pdfFile");
const courtSelect     = document.getElementById("courtSelect");
const methodSelect    = document.getElementById("methodSelect");
const maxPagesInput   = document.getElementById("maxPages");
const output          = document.getElementById("jsonOutput");
const statusEl        = document.getElementById("status");
const timeVal         = document.getElementById("timeVal");
const rowVal          = document.getElementById("rowVal");
const caseVal         = document.getElementById("caseVal");
const structuredCaseVal = document.getElementById("structuredCaseVal");
const caseGrid        = document.getElementById("caseGrid");
const logPanel        = document.getElementById("logPanel");
const logPre          = document.getElementById("logPre");
const logTitle        = document.getElementById("logTitle");
const logBadge        = document.getElementById("logBadge");

let lastResponse = null;
let logLineCount = 0;

// ---- Log panel helpers ----
function clearLog() {
  logPre.innerHTML = "";
  logLineCount = 0;
  logBadge.style.display = "none";
  logTitle.innerHTML = "Live Progress Log";
}

function appendLog(message) {
  const span = document.createElement("span");
  const low = message.toLowerCase();
  if (low.includes("download") || low.includes("loading") || low.includes("model")) {
    span.className = "log-line-dl";
  } else if (low.includes("page") || low.includes("processing") || low.includes("parsing")) {
    span.className = "log-line-pg";
  } else if (low.includes("error") || low.includes("fail") || low.includes("traceback")) {
    span.className = "log-line-err";
  }
  span.textContent = message;
  logPre.appendChild(span);
  logPre.appendChild(document.createTextNode("\n"));
  // Auto-scroll
  logPre.scrollTop = logPre.scrollHeight;
  logLineCount++;
  logBadge.textContent = logLineCount + " line" + (logLineCount === 1 ? "" : "s");
  logBadge.style.display = "";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderCaseExplorer(cases) {
  caseGrid.innerHTML = "";
  cases.forEach((c, idx) => {
    const srNo = c.serial || (idx + 1);
    const card = `
      <article class="case-card">
        <div class="case-head">
          <div class="case-no">${escapeHtml(c.case_no || "-")}</div>
          <span class="badge">${escapeHtml(c.case_type || "NA")}</span>
        </div>
        <div class="case-meta">Sr&nbsp;${escapeHtml(String(srNo))}&nbsp;&nbsp;|&nbsp;&nbsp;Page&nbsp;${escapeHtml(String(c.page || "-"))}</div>
        <div class="kv"><span class="lbl">Petitioner:</span>${escapeHtml(c.petitioner || "Not available in source")}</div>
        <div class="kv"><span class="lbl">Respondent:</span>${escapeHtml(c.respondent || "Not available in source")}</div>
        <div class="kv"><span class="lbl">Advocates:</span>${escapeHtml(c.advocates || "Not available in source")}</div>
      </article>
    `;
    caseGrid.insertAdjacentHTML("beforeend", card);
  });
}

function downloadJSON(data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "cases.json";
  a.click();
  URL.revokeObjectURL(url);
}

downloadBtn.addEventListener("click", () => {
  if (!lastResponse) {
    statusEl.textContent = "Run extraction first to download JSON.";
    return;
  }
  downloadJSON(lastResponse);
});

runBtn.addEventListener("click", async () => {
  const file     = fileInput.files[0];
  const court    = courtSelect.value;
  const method   = methodSelect.value;
  let maxPages = (maxPagesInput.value || "").trim();

  if (!file) { statusEl.textContent = "Please choose a PDF file."; return; }

  const form = new FormData();
  form.append("file",   file);
  form.append("court",  court);
  form.append("method", method);

  if (maxPages) form.append("max_pages", maxPages);

  // Reset UI
  clearLog();
  caseGrid.innerHTML = "";
  output.textContent = "{}";
  timeVal.textContent = rowVal.textContent = caseVal.textContent = structuredCaseVal.textContent = "—";
  lastResponse = null;

  // Open log panel + show spinner in status
  logPanel.open = true;
  statusEl.innerHTML = `<span class="spinner"></span>&nbsp; Running <b>${escapeHtml(method)}</b>…`;
  runBtn.disabled = true;

  try {
    const resp = await fetch("/extract-stream", { method: "POST", body: form });

    if (!resp.ok) {
      // Non-200 from the validation path (before streaming starts)
      const data = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
      statusEl.textContent = data.error || "Extraction failed.";
      return;
    }

    // Parse SSE stream from a POST response using ReadableStream
    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE delimits events with \n\n
      const events = buffer.split("\n\n");
      buffer = events.pop();            // keep incomplete trailing event

      for (const raw of events) {
        const dataLine = raw.split("\n").find(l => l.startsWith("data:"));
        if (!dataLine) continue;
        let msg;
        try { msg = JSON.parse(dataLine.slice(5).trim()); }
        catch { continue; }

        if (msg.type === "log") {
          appendLog(msg.message);

        } else if (msg.type === "heartbeat") {
          // keep-alive – no visible action needed

        } else if (msg.type === "error") {
          statusEl.textContent = "Error: " + msg.message;
          appendLog("ERROR: " + msg.message);

        } else if (msg.type === "result") {
          const data = msg.data;
          timeVal.textContent           = data.extraction_time;
          rowVal.textContent            = data.number_of_rows;
          caseVal.textContent           = data.number_of_case_numbers_detected;
          structuredCaseVal.textContent = data.number_of_cases;
          renderCaseExplorer(data.cases || []);
          // Show cases-only in the output box (rows excluded to keep it readable)
          const display = { ...data };
          delete display.cases;  // cases already shown in card grid above
          output.textContent = JSON.stringify({ summary: display, cases: data.cases }, null, 2);
          lastResponse = data;  // Download JSON uses full data including cases
          const warning = data.warning ? ` Warning: ${data.warning}` : "";
          if (data.warning) {
            appendLog(`WARNING: ${data.warning}`);
          }
          statusEl.textContent = `${method} completed — ${data.number_of_cases} cases in ${data.extraction_time}s.${warning}`;
        }
      }
    }

  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  } finally {
    runBtn.disabled = false;
  }
});
