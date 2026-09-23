const $ = (id) => document.getElementById(id);

const live = $("live");
const form = $("task-form");
const startButton = $("start");
const stopButton = $("stop");
const primaryActions = document.querySelector(".primary-actions");
const freshButton = $("fresh");
const resultDetails = $("result-details");
const sessionResultsDialog = $("session-results-dialog");
const closeResults = $("close-results");
const browserStage = $("browser-stage");
const fullscreenButton = $("browser-fullscreen");
const runState = document.querySelector(".run-state");
const browserEmpty = $("browser-empty");
const voiceDock = $("voice-dock");
const voiceToggle = $("voice-toggle");
const voiceInline = $("voice-inline");
const browserInput = $("browser-input");
const browserVoice = $("browser-voice");
const voiceState = $("voice-state");
const voiceTranscript = $("voice-transcript");
const themeToggle = $("theme-toggle");
const themeLabel = $("theme-label");
let currentStatus = "idle";
let sessionCanContinue = false;
let browserInputEnabled = false;
let statusGeneration = 0;
let voiceRecognizer = null;
let voiceActive = false;
let voiceBuffer = "";
let voiceFinalizeTimer = null;
const voiceQueue = [];

function renderThemeToggle() {
  const light = document.documentElement.dataset.theme === "light";
  themeLabel.textContent = light ? "Dark" : "Light";
  themeToggle.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
  themeToggle.setAttribute("aria-pressed", String(light));
  document.querySelector('meta[name="theme-color"]').content = light ? "#f2f7f7" : "#090d11";
}

function toggleTheme() {
  const light = document.documentElement.dataset.theme === "light";
  if (light) {
    delete document.documentElement.dataset.theme;
    localStorage.setItem("rove-theme", "dark");
  } else {
    document.documentElement.dataset.theme = "light";
    localStorage.setItem("rove-theme", "light");
  }
  renderThemeToggle();
  notifyLiveTheme();
}

function resetInitialScroll() {
  if (location.hash === "#workbench") history.replaceState(null, "", `${location.pathname}${location.search}`);
  requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo(0, 0)));
}

window.addEventListener("pageshow", resetInitialScroll);
window.addEventListener("load", resetInitialScroll);
setTimeout(resetInitialScroll, 120);

function liveTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function notifyLiveTheme() {
  const theme = liveTheme();
  live.contentWindow?.postMessage({ type: "rove-theme", theme }, "*");
  notifyLiveInput();
  fetch("/api/theme", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ theme }),
  }).catch(() => {});
}

function notifyLiveInput() {
  live.contentWindow?.postMessage({ type: "rove-input", enabled: browserInputEnabled }, "*");
}

const liveProtocol = location.protocol === "https:" ? "https" : "http";
live.src = `${liveProtocol}://${location.hostname}:6080/rove.html?host=${encodeURIComponent(location.hostname)}&port=6080&path=websockify&scale=true&view_only=true&theme=${liveTheme()}`;
live.addEventListener("load", notifyLiveTheme);

function toggleBrowserInput() {
  browserInputEnabled = !browserInputEnabled;
  browserInput.classList.toggle("is-enabled", browserInputEnabled);
  browserInput.setAttribute("aria-pressed", String(browserInputEnabled));
  browserInput.setAttribute("aria-label", browserInputEnabled ? "Disable browser input" : "Enable browser input");
  notifyLiveInput();
}

const stateCopy = {
  idle: ["Ready for a task", "The browser is waiting for an instruction."],
  starting: ["Opening the browser", "Preparing a live session for this task."],
  running: ["Working through the page", "The agent is navigating the live browser."],
  extracting: ["Reading the result", "Checking the final page for a verified answer."],
  stopping: ["Closing the session", "Finishing the current browser action."],
  stopped: ["Task stopped", "The session is ready for another instruction."],
  done: ["Task complete", "The browser finished the instruction."],
  answered: ["Answer ready", "Rove found a verified result on the final page."],
  error: ["Run needs attention", "The browser could not finish this instruction."],
};

function safeStatus(status) {
  return stateCopy[status] ? status : "idle";
}

function renderFullscreenButton() {
  const active = document.fullscreenElement === browserStage;
  fullscreenButton.setAttribute("aria-pressed", String(active));
  fullscreenButton.setAttribute("aria-label", active ? "Exit fullscreen browser view" : "Enter fullscreen browser view");
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await browserStage.requestFullscreen();
  } catch (_) {
    fullscreenButton.setAttribute("aria-label", "Fullscreen is unavailable");
  }
}

function voiceIsBusy() {
  return ["starting", "running", "extracting", "stopping"].includes(currentStatus);
}

function maybeRunVoiceQueue() {
  if (!voiceActive || voiceIsBusy() || !voiceQueue.length) return;
  const command = voiceQueue.shift();
  $("goal").value = command;
  voiceState.textContent = "Sending";
  voiceTranscript.textContent = command;
  submitTask(sessionCanContinue && ["done", "answered", "blocked", "stopped", "error"].includes(currentStatus));
}

function queueVoiceCommand(command) {
  if (!command) return;
  voiceQueue.push(command);
  voiceState.textContent = voiceIsBusy() ? "Queued" : "Command ready";
  voiceTranscript.textContent = command;
  maybeRunVoiceQueue();
}

function invalidateStatusPolls() {
  statusGeneration += 1;
  return statusGeneration;
}

function setVoiceActive(active) {
  voiceActive = active;
  voiceDock.hidden = !active;
  voiceDock.classList.toggle("is-listening", active);
  voiceInline.classList.toggle("is-listening", active);
  browserVoice.classList.toggle("is-listening", active);
  voiceToggle.setAttribute("aria-pressed", String(active));
  voiceInline.setAttribute("aria-pressed", String(active));
  browserVoice.setAttribute("aria-pressed", String(active));
  voiceInline.setAttribute("aria-label", active ? "Disable voice commands" : "Enable voice commands");
  browserVoice.setAttribute("aria-label", active ? "Disable voice commands" : "Enable voice commands");
  voiceState.textContent = active ? "Listening" : "Voice off";
  voiceTranscript.textContent = active ? "Say a command" : "Tap the mic to speak a command";
  if (!active) {
    voiceBuffer = "";
    voiceQueue.length = 0;
    clearTimeout(voiceFinalizeTimer);
    try {
      voiceRecognizer?.stop();
    } catch (_) {
      // Recognition may already have ended after a permission error.
    }
  } else {
    try {
      voiceRecognizer?.start();
    } catch (_) {
      // Recognition is already starting; its onstart callback will update the pill.
    }
  }
}

function initVoice() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    voiceToggle.disabled = true;
    voiceInline.disabled = true;
    browserVoice.disabled = true;
    voiceState.textContent = "Voice unavailable";
    voiceTranscript.textContent = "Use Chrome or Chromium for voice commands";
    return;
  }

  voiceRecognizer = new Recognition();
  voiceRecognizer.continuous = true;
  voiceRecognizer.interimResults = true;
  voiceRecognizer.lang = "en-US";
  voiceRecognizer.maxAlternatives = 1;

  voiceRecognizer.onstart = () => {
    if (voiceActive) voiceState.textContent = "Listening";
  };

  voiceRecognizer.onresult = (event) => {
    let finalText = "";
    let interimText = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0].transcript;
      if (event.results[index].isFinal) finalText += transcript;
      else interimText += transcript;
    }

    if (finalText.trim()) {
      voiceBuffer = `${voiceBuffer} ${finalText}`.trim();
      clearTimeout(voiceFinalizeTimer);
      voiceFinalizeTimer = setTimeout(() => {
        const command = voiceBuffer.trim();
        voiceBuffer = "";
        queueVoiceCommand(command);
      }, 850);
    }

    const visibleText = `${voiceBuffer} ${interimText}`.trim();
    voiceTranscript.textContent = visibleText || "Listening for a command";
  };

  voiceRecognizer.onerror = (event) => {
    if (["not-allowed", "service-not-allowed"].includes(event.error)) {
      setVoiceActive(false);
      voiceState.textContent = "Mic permission needed";
      voiceTranscript.textContent = "Allow microphone access to use voice commands";
    }
  };

  voiceRecognizer.onend = () => {
    if (!voiceActive) return;
    setTimeout(() => {
      if (voiceActive) {
        try {
          voiceRecognizer.start();
        } catch (_) {
          // Chromium can briefly report that recognition is still active.
        }
      }
    }, 180);
  };

  voiceToggle.addEventListener("click", () => setVoiceActive(!voiceActive));
  voiceInline.addEventListener("click", () => setVoiceActive(!voiceActive));
  browserVoice.addEventListener("click", () => setVoiceActive(!voiceActive));
}

function formatAction(item) {
  return item.text ? `${item.action || item.kind || "Action"} — ${item.text}` : item.action || item.kind || "Browser action";
}

function renderLog(steps) {
  const log = $("log");
  const items = (steps || []).slice(-12);
  $("action-count").textContent = `${items.length} ${items.length === 1 ? "action" : "actions"}`;
  $("latest-action").textContent = items.length ? formatAction(items[items.length - 1]) : "No actions yet";
  log.replaceChildren();

  if (!items.length) {
    const empty = document.createElement("li");
    empty.className = "empty-log";
    empty.textContent = "No actions yet.";
    log.appendChild(empty);
    return;
  }

  items.forEach((item, index) => {
    const entry = document.createElement("li");
    entry.className = "action-entry";
    const number = document.createElement("span");
    number.className = "action-number";
    number.textContent = String(item.step || index + 1).padStart(2, "0");
    const copy = document.createElement("span");
    copy.className = "action-copy";
    copy.textContent = formatAction(item);
    entry.append(number, copy);
    log.appendChild(entry);
  });
}

function renderResult(result) {
  if (!result) {
    $("result-kind").textContent = "WAITING";
    $("result-summary").textContent = "Complete a task to see the verified result here.";
    $("result-facts").replaceChildren();
    $("result-items").replaceChildren();
    $("result-source").hidden = true;
    return;
  }

  $("result-kind").textContent = result.kind === "answer" ? "ANSWER" : result.kind.replace("_", " ").toUpperCase();
  $("result-summary").textContent = result.summary || "No verified result was returned.";
  const facts = $("result-facts");
  facts.replaceChildren();
  Object.entries(result.facts || {}).forEach(([label, value]) => {
    const item = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label.replaceAll("_", " ");
    detail.textContent = value;
    item.append(term, detail);
    facts.appendChild(item);
  });

  const items = $("result-items");
  items.replaceChildren();
  (Array.isArray(result.items) ? result.items : []).forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    items.appendChild(item);
  });

  const source = $("result-source");
  const sourceUrl = typeof result.source_url === "string" && /^https?:\/\//i.test(result.source_url) ? result.source_url : "";
  source.hidden = !sourceUrl;
  if (sourceUrl) source.href = sourceUrl;
}

function renderResultHistory(history) {
  const entries = Array.isArray(history) ? history : [];
  const list = $("session-results-list");
  resultDetails.disabled = entries.length === 0;
  resultDetails.querySelector("[data-result-count]").textContent = String(entries.length).padStart(2, "0");
  list.replaceChildren();

  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "session-results-empty";
    empty.textContent = "No completed results in this session yet.";
    list.appendChild(empty);
    return;
  }

  entries.slice().reverse().forEach((entry) => {
    const result = entry.result || {};
    const card = document.createElement("article");
    card.className = "session-result";

    const topline = document.createElement("div");
    topline.className = "session-result-topline";
    const number = document.createElement("span");
    number.textContent = `Result ${String(entry.index || 0).padStart(2, "0")}`;
    const kind = document.createElement("span");
    kind.className = "session-result-kind";
    kind.textContent = result.kind === "answer" ? "ANSWER" : (result.kind || "result").replaceAll("_", " ").toUpperCase();
    topline.append(number, kind);

    const goal = document.createElement("p");
    goal.className = "session-result-goal";
    goal.textContent = entry.goal || "No instruction recorded.";
    const summary = document.createElement("p");
    summary.className = "session-result-summary";
    summary.textContent = result.summary || "No summary was returned.";
    card.append(topline, goal, summary);

    const facts = document.createElement("dl");
    facts.className = "session-result-facts";
    Object.entries(result.facts || {}).forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "session-result-fact";
      const term = document.createElement("dt");
      term.textContent = label.replaceAll("_", " ");
      const detail = document.createElement("dd");
      detail.textContent = value;
      row.append(term, detail);
      facts.appendChild(row);
    });
    if (facts.childElementCount) card.appendChild(facts);

    const items = Array.isArray(result.items) ? result.items : [];
    if (items.length) {
      const itemList = document.createElement("ol");
      itemList.className = "session-result-items";
      items.forEach((value) => {
        const item = document.createElement("li");
        item.textContent = value;
        itemList.appendChild(item);
      });
      card.appendChild(itemList);
    }

    const sourceUrl = typeof result.source_url === "string" && /^https?:\/\//i.test(result.source_url) ? result.source_url : "";
    if (sourceUrl) {
      const source = document.createElement("a");
      source.className = "session-result-source";
      source.href = sourceUrl;
      source.target = "_blank";
      source.rel = "noreferrer";
      source.append("Open source ");
      const arrow = document.createElement("span");
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "↗";
      source.appendChild(arrow);
      card.appendChild(source);
    }
    list.appendChild(card);
  });
}

function render(snapshot) {
  const status = safeStatus(snapshot.status);
  currentStatus = status;
  const [title, detail] = stateCopy[status];
  const isBusy = ["starting", "running", "extracting", "stopping"].includes(status);
  const canContinue = ["done", "answered", "blocked", "stopped", "error"].includes(status)
    && Boolean(snapshot.goal)
    && Boolean(snapshot.can_continue);
  sessionCanContinue = canContinue;
  const hasSession = Boolean(snapshot.url) || isBusy || ["done", "answered", "blocked", "error", "stopped"].includes(status);

  $("state").textContent = title;
  $("state-detail").textContent = detail;
  $("state-card-value").textContent = status;
  $("browser-status").textContent = status === "extracting" ? "reading" : isBusy ? "connected" : ["done", "answered"].includes(status) ? "complete" : "standby";
  $("browser-title").textContent = status === "extracting" ? "Reading the result" : isBusy ? "Session in progress" : ["done", "answered"].includes(status) ? "Session complete" : "Waiting for a session";
  $("session-url").textContent = snapshot.url || "No active page";
  $("frame-address").textContent = snapshot.url || "about:blank";
  if (snapshot.url && status !== "idle" && document.activeElement !== $("url")) $("url").value = snapshot.url;
  $("context-value").textContent = snapshot.goal || "No task loaded";
  $("error").hidden = !snapshot.error;
  $("error").textContent = snapshot.error || "";
  runState.dataset.state = status;
  browserEmpty.hidden = hasSession;
  startButton.disabled = isBusy;
  primaryActions.classList.toggle("has-stop", isBusy);
  stopButton.hidden = !isBusy;
  stopButton.disabled = status === "stopping";
  freshButton.disabled = isBusy;
  startButton.querySelector("span:first-child").textContent = status === "extracting" ? "Reading result" : isBusy ? "Task running" : canContinue ? "Continue task" : "Run task";
  renderLog(snapshot.steps);
  renderResult(snapshot.result);
  renderResultHistory(snapshot.result_history);
  maybeRunVoiceQueue();
}

async function submitTask(continuation) {
  const url = $("url").value.trim();
  const goal = $("goal").value.trim();
  if (!goal || (!continuation && !url)) return;
  invalidateStatusPolls();

  const previous = {
    url: continuation ? $("session-url").textContent : url,
    goal,
    steps: continuation ? [] : [],
    result: null,
  };
  startButton.disabled = true;
  freshButton.disabled = true;
  render({ status: "starting", ...previous });
  try {
    const response = await fetch(continuation ? "/api/tasks/continue" : "/api/tasks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(continuation ? { goal } : { url, goal }),
    });
    const payload = await response.json();
    if (!response.ok) {
      render({ status: "error", error: payload.error, ...previous });
      return;
    }
    render(payload);
  } catch (_) {
    render({ status: "error", error: "The task service is unavailable.", ...previous });
  }
}

async function stopTask() {
  voiceQueue.length = 0;
  voiceBuffer = "";
  clearTimeout(voiceFinalizeTimer);
  invalidateStatusPolls();
  stopButton.disabled = true;
  try {
    const response = await fetch("/api/tasks/stop", { method: "POST" });
    const payload = await response.json();
    if (!response.ok) {
      render({ status: "error", error: payload.error });
      return;
    }
    render(payload);
  } catch (_) {
    render({ status: "error", error: "The task service is unavailable." });
  }
}

async function start(event) {
  event.preventDefault();
  await submitTask(sessionCanContinue && ["done", "answered", "blocked", "stopped", "error"].includes(currentStatus));
}

async function startFresh() {
  voiceQueue.length = 0;
  voiceBuffer = "";
  clearTimeout(voiceFinalizeTimer);
  const requestGeneration = invalidateStatusPolls();
  freshButton.disabled = true;
  try {
    const response = await fetch("/api/sessions", { method: "POST" });
    const payload = await response.json();
    if (requestGeneration !== statusGeneration) return;
    if (!response.ok) {
      render({ status: "error", error: payload.error });
      return;
    }
    $("url").value = "https://google.com";
    $("goal").value = "";
    render(payload);
  } catch (_) {
    render({ status: "error", error: "The session service is unavailable." });
  } finally {
    freshButton.disabled = false;
  }
}

function initMotion() {
  if (!window.gsap || !window.ScrollTrigger || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  gsap.registerPlugin(ScrollTrigger);
  gsap.from(".reveal", { opacity: 0, y: 28, duration: 0.8, stagger: 0.08, ease: "power3.out" });
  gsap.from(".bento-card", {
    scrollTrigger: { trigger: ".bento", start: "top 78%" },
    opacity: 0,
    y: 32,
    scale: 0.96,
    duration: 0.75,
    stagger: 0.08,
    ease: "power3.out",
  });
}

form.addEventListener("submit", start);
freshButton.addEventListener("click", startFresh);
themeToggle.addEventListener("click", toggleTheme);
browserInput.addEventListener("click", toggleBrowserInput);
stopButton.addEventListener("click", stopTask);
fullscreenButton.addEventListener("click", toggleFullscreen);
document.addEventListener("fullscreenchange", renderFullscreenButton);
resultDetails.addEventListener("click", () => sessionResultsDialog.showModal());
closeResults.addEventListener("click", () => sessionResultsDialog.close());
sessionResultsDialog.addEventListener("click", (event) => {
  if (event.target === sessionResultsDialog) sessionResultsDialog.close();
});
document.querySelectorAll('a[href="#workbench"]').forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    $("workbench").scrollIntoView({ behavior: "smooth", block: "start" });
    history.replaceState(null, "", `${location.pathname}${location.search}`);
  });
});
render({ status: "idle", steps: [], result: null });
initVoice();
renderFullscreenButton();
renderThemeToggle();
notifyLiveTheme();
initMotion();

setInterval(async () => {
  const requestGeneration = statusGeneration;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (response.ok && requestGeneration === statusGeneration) render(await response.json());
  } catch (_) {
    // The next poll will restore the live state.
  }
}, 1000);
