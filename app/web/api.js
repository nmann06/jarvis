// Talks to the panel server (docs/panel-api.md). Errors carry the server's plain-words message.

export class ApiError extends Error {
  constructor(message, status = 0, network = false) {
    super(message);
    this.status = status;
    this.network = network;
  }
}

let onNetworkFail = () => {};
let onUnauthorized = () => {};
export function setHandlers(net, unauth) { onNetworkFail = net; onUnauthorized = unauth; }

export async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    onNetworkFail();
    throw new ApiError("Can't reach Jarvis Panel. Check that the Jarvis machine is on and Tailscale is connected.", 0, true);
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError((data && data.error) || "Not signed in. Open the panel from the Jarvis Panel app.", 401);
  }
  if (!res.ok) {
    throw new ApiError((data && data.error) || `The server answered ${res.status}.`, res.status);
  }
  return data;
}

export const get = (p) => api("GET", p);
export const post = (p, b = {}) => api("POST", p, b);
export const del = (p) => api("DELETE", p);
