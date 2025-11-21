// -------------------------------------------------------------
// GLOBAL LOGOUT FUNCTION (WORKING ALWAYS)
// -------------------------------------------------------------
function AppLogout() {
  localStorage.removeItem('jwt');
  localStorage.removeItem('user');
  window.location.replace('/');
}

// -------------------------------------------------------------
const Auth = {
  set(token, user) {
    localStorage.setItem("jwt", token);
    localStorage.setItem("user", JSON.stringify(user));
  },
  token() { return localStorage.getItem("jwt"); },
  user() {
    try { return JSON.parse(localStorage.getItem("user") || "null"); }
    catch { return null; }
  },
  clear() {
    localStorage.removeItem("jwt");
    localStorage.removeItem("user");
  }
};

// -------------------------------------------------------------
// API helper (shows real server errors)
// -------------------------------------------------------------
async function api(url, { method = "GET", auth = false, body = null } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth) headers["Authorization"] = "Bearer " + Auth.token();

  let res;
  try {
    res = await fetch(url, { method, headers, body });
  } catch (e) {
    throw new Error("Network error: " + (e?.message || e));
  }

  const ct = res.headers.get("content-type") || "";
  const isJson = ct.includes("application/json");
  const payload = isJson ? await res.json().catch(() => ({})) : {};
  const text = isJson ? "" : await res.text().catch(() => "");

  if (!res.ok) {
    const msg = payload.error || payload.msg || text || `${res.status} ${res.statusText}`;
    const err = new Error(msg);
    err.status = res.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

// -------------------------------------------------------------
// Protect page
// -------------------------------------------------------------
const me = Auth.user();
if (!me || !Auth.token()) {
  window.location.replace("/");
}

// DOM
const welcome = document.getElementById("welcome");
welcome.textContent = "Welcome, " + me.username;

const logoutBtn = document.getElementById("logout");
logoutBtn.addEventListener("click", AppLogout);

// -------------------------------------------------------------
// STATE (declare BEFORE socket listeners!)
// -------------------------------------------------------------
const state = {
  openWith: null,
  friends: [],
  lastMsgs: {}
};

// Track optimistic messages so we can dedupe when server echoes back
const pendingByClientId = new Map(); // client_id -> DOM node

// ELEMENTS
const searchInput = document.getElementById("searchInput");
const searchBtn = document.getElementById("searchBtn");
const searchResults = document.getElementById("searchResults");

const friendsList = document.getElementById("friendsList");
const chatTitle = document.getElementById("chatTitle");
const chatSub = document.getElementById("chatSub");
const chatArea = document.getElementById("chatArea");
const composer = document.getElementById("composer");
const messageInput = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const friendMeta = document.getElementById("friendMeta");

// -------------------------------------------------------------
// APPEND MESSAGE (declare BEFORE socket listeners!)
// Returns the created message DOM node
// -------------------------------------------------------------
function appendMessage(from, text, time, opts = {}) {
  const div = document.createElement("div");
  div.className = "message " + (from === me.username ? "from-me" : "from-them");
  if (opts.clientId) {
    div.dataset.clientId = opts.clientId; // mark optimistic bubble
  }
  const when = time ? new Date(time) : new Date();
  div.innerHTML = `
    <div style="font-size:13px;margin-bottom:6px;">
      <strong>${escapeHtml(from)}</strong>
      <span class="muted" style="font-size:11px;" data-role="msg-time">${when.toLocaleString()}</span>
      ${opts.pending ? `<span class="muted" data-role="msg-status"> • sending…</span>` : ``}
    </div>
    <div>${escapeHtml(text)}</div>
  `;
  chatArea.appendChild(div);
  return div;
}

// Small helper to update an existing optimistic bubble when server confirms
function finalizeOptimisticBubble(node, serverPayload) {
  try {
    const timeEl = node.querySelector('[data-role="msg-time"]');
    const statusEl = node.querySelector('[data-role="msg-status"]');
    if (timeEl && serverPayload.time) {
      const when = new Date(serverPayload.time);
      timeEl.textContent = when.toLocaleString();
    }
    if (statusEl) {
      statusEl.remove(); // remove "sending…" once confirmed
    }
    node.dataset.messageId = serverPayload.id;
  } catch (_) {}
}

// -------------------------------------------------------------
// SEARCH
// -------------------------------------------------------------
searchBtn.addEventListener("click", () => renderSearch(searchInput.value));
searchInput.addEventListener("keyup", (e) => { if (e.key === "Enter") renderSearch(searchInput.value); });

async function renderSearch(q = "") {
  searchResults.innerHTML = "";

  try {
    const results = await api(`/api/search?q=${encodeURIComponent(q)}`, { auth: true });

    // Be tolerant if friend/requests fails
    let requests = { incoming: [], outgoing: [] };
    try {
      requests = await api("/api/friend/requests", { auth: true });
    } catch (e) {
      console.warn("friend/requests failed:", e);
    }

    const incoming = new Set((requests.incoming || []).map(x => x.from));
    const outgoing = new Set((requests.outgoing || []).map(x => x.to));
    const myFriends = new Set((state.friends || []).map(x => x.username));

    if (!results.length) {
      searchResults.innerHTML = '<div class="empty">No users found.</div>';
      return;
    }

    results.forEach(u => {
      if (u.username === me.username) return; // don’t show myself

      const row = document.createElement('div');
      row.className = 'user-row';
      row.innerHTML = `
        <div>
          <div class="user-name">${u.username}</div>
          <div class="muted">${u.email}</div>
        </div>
      `;

      const actions = document.createElement('div');
      if (myFriends.has(u.username)) {
        const b = document.createElement('button');
        b.className = 'disabled';
        b.textContent = 'Friends';
        actions.appendChild(b);
      } else if (incoming.has(u.username)) {
        const b = document.createElement('button');
        b.className = 'accept-btn';
        b.textContent = 'Accept';
        b.onclick = () => acceptRequest(u.username);
        actions.appendChild(b);
      } else if (outgoing.has(u.username)) {
        const b = document.createElement('button');
        b.className = 'disabled';
        b.textContent = 'Request sent';
        actions.appendChild(b);
      } else {
        const b = document.createElement('button');
        b.className = 'request-btn';
        b.textContent = 'Send Request';
        b.onclick = () => sendRequest(u.username);
        actions.appendChild(b);
      }

      row.appendChild(actions);
      searchResults.appendChild(row);
    });
  } catch (err) {
    searchResults.innerHTML = `<div class="empty">${err.message || 'Request failed'}</div>`;
  }
}

// -------------------------------------------------------------
// FRIEND REQUESTS
// -------------------------------------------------------------
async function sendRequest(toUser) {
  const row = [...document.querySelectorAll('.user-row')]
    .find(r => r.querySelector('.user-name')?.textContent === toUser);
  const btn = row?.querySelector('.request-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Sending...'; }

  try {
    const res = await api("/api/friend/request", {
      method: "POST", auth: true,
      body: JSON.stringify({ to: toUser })
    });
    if (res.msg === "accepted") {
      alert(`You and ${toUser} are now friends!`);
      await loadFriends();
    } else {
      alert("Friend request sent to " + toUser);
    }
    await renderSearch(searchInput.value);
  } catch (err) {
    alert(err.message || "Failed to send request");
    if (btn) { btn.disabled = false; btn.textContent = 'Send Request'; }
  }
}

async function acceptRequest(fromUser) {
  try {
    await api("/api/friend/accept", {
      method: "POST", auth: true,
      body: JSON.stringify({ from: fromUser })
    });
    alert("Friend added");
    await loadFriends();
    await renderSearch(searchInput.value);
  } catch (err) {
    alert(err.message);
  }
}

// -------------------------------------------------------------
// FRIENDS LIST
// -------------------------------------------------------------
async function loadFriends() {
  try {
    state.friends = await api("/api/friends", { auth: true });
    renderFriends();
  } catch {
    friendsList.innerHTML = "<div class='empty'>Could not load.</div>";
  }
}

function renderFriends() {
  friendsList.innerHTML = "";

  if (!state.friends.length) {
    friendsList.innerHTML = "<div class='empty'>No friends yet.</div>";
    return;
  }

  state.friends.forEach((f) => {
    const row = document.createElement("div");
    row.className = "friend-row";

    const left = document.createElement("div");
    left.innerHTML = `
      <div class="user-name">${f.username}</div>
      <div class="muted">Chat</div>
    `;
    left.style.cursor = "pointer";
    left.onclick = () => openChat(f.username);

    const right = document.createElement("div");
    const btn = document.createElement("button");
    btn.textContent = "Open";
    btn.onclick = () => openChat(f.username);
    right.appendChild(btn);

    row.appendChild(left);
    row.appendChild(right);
    friendsList.appendChild(row);
  });
}

// -------------------------------------------------------------
// OPEN CHAT
// -------------------------------------------------------------
async function openChat(friend) {
  state.openWith = friend;
  chatTitle.textContent = friend;
  chatSub.textContent = `Chatting with ${friend}`;
  composer.style.display = "flex";

  friendMeta.innerHTML = `
    <button onclick="clearChat('${friend}')">Clear Chat</button>
    <button style="margin-left:8px;" onclick="removeFriend('${friend}')">Remove Friend</button>
  `;

  await loadChat(friend);
}

async function loadChat(friend) {
  chatArea.innerHTML = "";

  try {
    const msgs = await api(`/api/messages/${friend}`, { auth: true });

    if (!msgs.length) {
      chatArea.innerHTML = "<div class='empty'>No messages yet.</div>";
      return;
    }

    msgs.forEach((m) => {
      appendMessage(
        m.from_user === me.id ? me.username : friend,
        m.text,
        m.created_at
      );
    });

    chatArea.scrollTop = chatArea.scrollHeight;
  } catch (err) {
    chatArea.innerHTML = `<div class="empty">${err.message}</div>`;
  }
}

// -------------------------------------------------------------
// SEND / CLEAR / REMOVE
// -------------------------------------------------------------
sendBtn.addEventListener("click", sendMessage);
messageInput.addEventListener("keyup", (e) => { if (e.key === "Enter") sendMessage(); });

function genClientId() {
  // Use UUID when available; fallback to timestamp-random
  if (window.crypto?.randomUUID) return crypto.randomUUID();
  return String(Date.now()) + "-" + Math.random().toString(16).slice(2);
}

function sendMessage() {
  const txt = messageInput.value.trim();
  if (!txt || !state.openWith) return;

  // 1) Create an optimistic bubble with a client-side id
  const clientId = genClientId();
  const node = appendMessage(me.username, txt, new Date().toISOString(), { pending: true, clientId });
  chatArea.scrollTop = chatArea.scrollHeight;
  pendingByClientId.set(clientId, node);

  // 2) Send over the socket with the client_id for dedupe
  socket.emit("send_message", {
    to: state.openWith,
    text: txt,
    token: Auth.token(),
    client_id: clientId
  });

  messageInput.value = "";
}

function clearChat(friend) {
  socket.emit("clear_chat", { with: friend, token: Auth.token() });
}

async function removeFriend(friend) {
  try {
    await api("/api/friend/remove", {
      method: "POST", auth: true,
      body: JSON.stringify({ with: friend })
    });

    if (state.openWith === friend) {
      state.openWith = null;
      chatTitle.textContent = "Select a friend to chat";
      chatSub.textContent = "No chat open";
      composer.style.display = "none";
      chatArea.innerHTML = "<div class='empty'>Open a friend to chat</div>";
    }

    await loadFriends();                       // refresh left list
    await renderSearch(searchInput.value);     // refresh search so button turns to "Send Request"
  } catch (e) {
    alert(e.message || "Failed to remove friend");
  }
}

// -------------------------------------------------------------
function escapeHtml(t) {
  return (t + "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// -------------------------------------------------------------
// SOCKET — create AFTER state & functions are defined
// -------------------------------------------------------------
const socket = io({
  auth: { token: Auth.token() },
  transports: ['websocket', 'polling'],
  reconnection: true
});

socket.on('connect', () => console.log('Connected to socket'));
socket.on('connect_error', (err) => console.error('Socket connect error:', (err && err.message) || err));

socket.on("new_message", (payload) => {
  // Only render if this chat is open (either direction)
  if (!(state.openWith === payload.from || state.openWith === payload.to)) return;

  // If this is our own optimistic message, finalize instead of duplicating
  if (payload.client_id && pendingByClientId.has(payload.client_id)) {
    const node = pendingByClientId.get(payload.client_id);
    finalizeOptimisticBubble(node, payload);
    pendingByClientId.delete(payload.client_id);
    chatArea.scrollTop = chatArea.scrollHeight;
    return;
  }

  // Otherwise, it's a new incoming (or historical echo) — append normally
  const who = payload.from;
  appendMessage(who, payload.text, payload.time || payload.created_at);
  chatArea.scrollTop = chatArea.scrollHeight;
});

// NEW: handle server "chat_cleared"
socket.on("chat_cleared", (data) => {
  if (state.openWith && (state.openWith === data.by || state.openWith === data.with)) {
    chatArea.innerHTML = "<div class='empty'>Chat cleared.</div>";
  }
});

// -------------------------------------------------------------
loadFriends();
renderSearch("");
