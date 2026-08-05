const BACKEND_URL = "https://telegram-job-tracker-zgbq.onrender.com";

const feedEl = document.getElementById("feed");
const statusEl = document.getElementById("status");
const searchEl = document.getElementById("search");
const notifyBtn = document.getElementById("notify-btn");

let currentQuery = "";
let debounceTimer;

function timeAgo(iso) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function renderJobs(jobs) {
  feedEl.innerHTML = "";
  if (!jobs.length) {
    feedEl.innerHTML = `<li class="empty">No dispatches yet — the wire is quiet.</li>`;
    return;
  }
  const total = jobs.length;
  jobs.forEach((job, i) => {
    const dispatchNo = String(total - i).padStart(3, "0");
    const mins = Math.floor((Date.now() - new Date(job.posted_at).getTime()) / 60000);
    const isNew = mins < 15;

    const li = document.createElement("li");
    li.className = "job";
    li.innerHTML = `
      <div class="job-head">
        <span class="wire-no">NO. ${dispatchNo}</span>
        <span>${timeAgo(job.posted_at)}${isNew ? '<span class="new-tag">NEW</span>' : ""}</span>
      </div>
      <div class="job-meta">SOURCE: ${job.channel}</div>
      <div class="job-text"></div>
      ${job.link ? `<a class="job-link" href="${job.link}" target="_blank" rel="noopener">OPEN IN TELEGRAM &rarr;</a>` : ""}
    `;
    li.querySelector(".job-text").textContent = job.text; // safe text insert
    feedEl.appendChild(li);
  });
}

async function loadJobs(q = "") {
  try {
    statusEl.textContent = "Reading the wire…";
    const res = await fetch(`${BACKEND_URL}/jobs?q=${encodeURIComponent(q)}`);
    const jobs = await res.json();
    renderJobs(jobs);
    statusEl.textContent = `${jobs.length} dispatch${jobs.length === 1 ? "" : "es"} on file · last checked ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    statusEl.textContent = "Couldn't reach the backend — check BACKEND_URL in app.js.";
    console.error(err);
  }
}

searchEl.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  currentQuery = searchEl.value.trim();
  debounceTimer = setTimeout(() => loadJobs(currentQuery), 400);
});

setInterval(() => loadJobs(currentQuery), 30000);
loadJobs();

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

async function enableNotifications() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    alert("Push notifications aren't supported in this browser.");
    return;
  }
  const reg = await navigator.serviceWorker.ready;
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    statusEl.textContent = "Notifications blocked — you can still search manually.";
    return;
  }

  const { key } = await (await fetch(`${BACKEND_URL}/vapid-public-key`)).json();
  const subscription = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(key),
  });

  const keywords = currentQuery ? [currentQuery] : [];
  await fetch(`${BACKEND_URL}/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ push_subscription: subscription.toJSON(), keywords }),
  });

  notifyBtn.textContent = "Subscribed";
  notifyBtn.disabled = true;
}

notifyBtn.addEventListener("click", enableNotifications);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js");
}
