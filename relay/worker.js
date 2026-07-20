// Cloudflare Worker: transparent relay to playtomic.com's public availability
// endpoint. GitHub Actions' datacenter IPs (and plain non-browser clients) get a
// 403 from Playtomic's CloudFront WAF; this forwards the request from
// Cloudflare's egress instead, which the WAF accepts.
// The optional Cookie header (pt_auth_access_token=...) is forwarded upstream, so
// a logged-in monitor gets the member availability view (further-out days) from
// the same endpoint. api.playtomic.io (login/refresh) is intentionally NOT
// proxied: its stricter WAF 403s Cloudflare's egress too, so it is unreachable.
const ALLOWED = ["/api/clubs/availability"];

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
    const headers = {
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      "Accept": "application/json,text/html",
      "Accept-Language": "it-IT,it;q=0.9",
    };
    const cookie = request.headers.get("Cookie");
    if (cookie) headers["Cookie"] = cookie; // logged-in member view
    const upstream = await fetch(url.toString(), { headers });
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("Content-Type") || "application/json",
      },
    });
  },
};
