const ORIGIN = "https://troca-ae-sis-pro.onrender.com";
const WAKE_PATH = "/api/health";
const WAKE_MARKERS = [
  "Application loading",
  "SERVICE WAKING UP",
  "Render",
];

function isWakeResponse(response, body) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("text/html")) return false;
  return WAKE_MARKERS.some((marker) => body.includes(marker));
}

function buildOriginRequest(request, targetUrl) {
  const headers = new Headers(request.headers);
  headers.set("host", new URL(ORIGIN).host);
  headers.set("x-forwarded-host", new URL(request.url).host);
  headers.set("x-forwarded-proto", "https");

  return new Request(targetUrl, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    redirect: "manual",
  });
}

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function wakeOrigin() {
  const healthUrl = new URL(WAKE_PATH, ORIGIN);
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      const response = await fetch(healthUrl, {
        headers: { "cache-control": "no-cache" },
      });
      if (response.ok) return true;
    } catch (_) {
      // Render can refuse while waking. The next attempt usually succeeds.
    }
    await sleep(2500);
  }
  return false;
}

function withProxyHeaders(response) {
  const headers = new Headers(response.headers);
  headers.set("x-troca-ae-edge", "cloudflare-worker");

  if (response.status >= 500) {
    headers.set("cache-control", "no-store");
  }

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function proxy(request) {
  const requestUrl = new URL(request.url);
  const targetUrl = new URL(requestUrl.pathname + requestUrl.search, ORIGIN);

  if (requestUrl.pathname === "/cf-health") {
    return Response.json({
      ok: true,
      edge: "cloudflare",
      origin: ORIGIN,
    });
  }

  const firstResponse = await fetch(buildOriginRequest(request, targetUrl));

  if (request.method !== "GET") {
    return withProxyHeaders(firstResponse);
  }

  const cloned = firstResponse.clone();
  const body = await cloned.text().catch(() => "");
  if (!isWakeResponse(firstResponse, body)) {
    return withProxyHeaders(firstResponse);
  }

  await wakeOrigin();
  const secondResponse = await fetch(buildOriginRequest(request, targetUrl));
  return withProxyHeaders(secondResponse);
}

export default {
  fetch(request) {
    return proxy(request);
  },
};
