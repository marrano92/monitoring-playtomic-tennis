// Cloudflare Worker: transparent relay to playtomic.com public endpoints.
// GitHub Actions' datacenter IPs get a 403 from Playtomic's CloudFront WAF;
// this forwards the same request from Cloudflare's egress instead.
// Usage: hit this Worker with the exact playtomic.com path+query, e.g.
//   https://<worker>.workers.dev/api/clubs/availability?tenant_id=...&date=...&sport_id=...
const ALLOWED = ["/api/clubs/availability", "/clubs/"];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!ALLOWED.some((p) => url.pathname.startsWith(p))) {
      return new Response("forbidden path", { status: 403 });
    }
    // ponytail: shared-token gate, only enforced when RELAY_TOKEN secret is set
    if (env.RELAY_TOKEN && request.headers.get("X-Relay-Token") !== env.RELAY_TOKEN) {
      return new Response("unauthorized", { status: 401 });
    }
    url.hostname = "playtomic.com";
    url.protocol = "https:";
    url.port = "";
    const upstream = await fetch(url.toString(), {
      headers: {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept": "application/json,text/html",
        "Accept-Language": "it-IT,it;q=0.9",
      },
    });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "application/json",
      },
    });
  },
};
