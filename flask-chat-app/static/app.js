// --- tiny helpers shared by all pages ---

const Auth = {
  set(token, user) {
    localStorage.setItem('jwt', token);
    localStorage.setItem('user', JSON.stringify(user || null));
  },
  token() { return localStorage.getItem('jwt'); },
  hasToken() { return !!localStorage.getItem('jwt'); },
  user() {
    try { return JSON.parse(localStorage.getItem('user') || 'null'); }
    catch { return null; }
  },
  clear() {
    localStorage.removeItem('jwt');
    localStorage.removeItem('user');
  }
};

// Guard on dashboard
function guardAuthOrRedirect() {
  if (!Auth.hasToken()) location.replace('/');
}

// Fetch wrapper that matches your Flask APIs
async function api(url, { method = 'GET', auth = false, body = null, headers = {} } = {}) {
  const h = {
    'Content-Type': 'application/json',
    ...headers
  };
  if (auth) h['Authorization'] = `Bearer ${Auth.token()}`;

  const res = await fetch(url, { method, headers: h, body });
  const isJson = res.headers.get('content-type')?.includes('application/json');
  const data = isJson ? await res.json().catch(() => ({})) : {};

  if (!res.ok) {
    const msg = (data && (data.error || data.msg)) || res.statusText || 'Request failed';
    throw new Error(msg);
  }
  return data;
}

// Minimal toast
let toastTimer = null;
function toast(message, isError = false) {
  const el = document.getElementById('toast');
  if (!el) return alert(message);
  el.textContent = message;
  el.classList.toggle('err', !!isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2500);
}

// Escape HTML to avoid XSS in messages
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'
  }[c]));
}

// Chat page shared state
const ChatState = {
  activeUser: null
};
