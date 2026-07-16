const state = {
  pack: null,
  activeTab: "caption",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function setMessage(text, isError = false) {
  const message = $("#formMessage");
  message.textContent = text;
  message.classList.toggle("error", isError);
}

function formToPayload(form) {
  return Object.fromEntries(new FormData(form).entries());
}

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.reason || payload.error || payload.detail || "Request failed.");
  }
  return payload;
}

function renderRuntime(data) {
  const dot = $("#readyDot");
  dot.classList.toggle("ready", Boolean(data.ready || data.ok));
  $("#readyText").textContent = data.ready ? "Ready" : "Runtime available";
  $("#modelText").textContent = `Model: ${data.openai_model || "not set"}`;
  $("#modeText").textContent = `Publish: ${data.mock_publish === "true" ? "mock" : "live/export"}`;
}

async function loadRuntime() {
  try {
    const response = await fetch("/campaign/ready");
    renderRuntime(await response.json());
  } catch {
    $("#readyText").textContent = "Runtime offline";
  }
}

async function loadDashboard() {
  try {
    const data = await jsonFetch("/api/dashboard");
    const dashboard = data.dashboard || {};
    $("#metricCampaigns").textContent = dashboard.campaigns || 0;
    $("#metricPosts").textContent = dashboard.posts_prepared || 0;
    $("#metricReach").textContent = dashboard.estimated_reach || 0;
    $("#metricQuality").textContent = dashboard.average_quality_score || 0;
  } catch {
    $("#metricCampaigns").textContent = "0";
  }
}

function card(title, body) {
  const el = document.createElement("article");
  el.className = "card";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const text = document.createElement("pre");
  text.textContent = body || "Not generated yet.";
  el.append(heading, text);
  return el;
}

function chip(label, tone = "") {
  const el = document.createElement("span");
  el.className = `chip ${tone}`.trim();
  el.textContent = label;
  return el;
}

function renderFlyer(pack) {
  const frame = $("#flyerPreview");
  frame.innerHTML = "";
  frame.className = "";
  if (pack?.flyer?.artifact_url) {
    const img = document.createElement("img");
    img.alt = "Generated campaign flyer";
    img.src = pack.flyer.artifact_url;
    frame.append(img);
  } else {
    frame.className = "empty-preview";
    frame.textContent = "Generated flyer appears here.";
  }
}

function renderSdgs(pack) {
  const holder = $("#sdgChips");
  holder.innerHTML = "";
  const sdgs = pack?.intelligence?.sdg_badges || [];
  if (!sdgs.length) {
    holder.append(chip("No SDG classified", "amber"));
    return;
  }
  sdgs.slice(0, 3).forEach((label, index) => holder.append(chip(label, index === 0 ? "teal" : "")));
}

function renderTab() {
  const stack = $("#outputStack");
  stack.innerHTML = "";
  const pack = state.pack;
  if (!pack) {
    stack.append(card("Waiting for campaign", "Generate a pack to view captions, impact, variants, and report."));
    return;
  }
  const captions = pack.caption_pack || {};
  const intelligence = pack.intelligence || {};
  if (state.activeTab === "caption") {
    stack.append(card("Instagram", captions.instagram));
    stack.append(card("LinkedIn", captions.linkedin));
    stack.append(card("Sinhala", intelligence.multilingual_captions?.sinhala));
    stack.append(card("Tamil", intelligence.multilingual_captions?.tamil));
  }
  if (state.activeTab === "impact") {
    const quality = intelligence.quality || {};
    const goals = (intelligence.impact_goals || [])
      .map((goal) => `${goal.metric}: ${goal.target} (${goal.why})`)
      .join("\n");
    const compliance = intelligence.compliance?.issues?.join("\n") || intelligence.compliance?.status || "ok";
    stack.append(card("Quality Score", `${quality.score || "N/A"} (${quality.grade || "N/A"})`));
    stack.append(card("Impact Goals", goals));
    stack.append(card("Accessibility", JSON.stringify(intelligence.accessibility || {}, null, 2)));
    stack.append(card("Compliance", compliance));
  }
  if (state.activeTab === "variants") {
    stack.append(card("Audience Variants", JSON.stringify(intelligence.audience_variants || {}, null, 2)));
    stack.append(card("Platform Variants", JSON.stringify(intelligence.platform_variants || {}, null, 2)));
    stack.append(card("Optimized CTAs", (intelligence.optimized_ctas || []).join("\n")));
  }
  if (state.activeTab === "report") {
    const report = pack.impact_report || {};
    const text = report.artifact_url ? `Markdown report: ${report.artifact_url}` : "Generate a report from Telegram or API.";
    stack.append(card("Submission Report", text));
    stack.append(card("Campaign IDs", `Event: ${pack.event_id}\nCampaign: ${pack.campaign_id}`));
  }
}

function renderPack(pack) {
  state.pack = pack;
  renderFlyer(pack);
  renderSdgs(pack);
  renderTab();
}

async function handleGenerate(event) {
  event.preventDefault();
  const button = $("#generateButton");
  button.disabled = true;
  setMessage("Generating campaign pack...");
  try {
    const pack = await jsonFetch("/api/quick-pack", {
      method: "POST",
      body: JSON.stringify(formToPayload(event.currentTarget)),
    });
    renderPack(pack);
    setMessage(`Pack ready: ${pack.event_id} / ${pack.campaign_id}`);
    await loadDashboard();
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function bindTabs() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      state.activeTab = tab.dataset.tab;
      $$(".tab").forEach((item) => item.classList.toggle("active", item === tab));
      renderTab();
    });
  });
}

$("#packForm").addEventListener("submit", handleGenerate);
$("#refreshDashboard").addEventListener("click", loadDashboard);
bindTabs();
loadRuntime();
loadDashboard();
renderTab();
