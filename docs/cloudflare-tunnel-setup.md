# Cloudflare Tunnel Setup

Cloudflare Tunnel provides secure ingress to the ai-router stack without exposing any ports to the public internet. All traffic flows outbound from `cloudflared` to Cloudflare's edge, then inbound to your configured public hostname.

## Prerequisites

- A Cloudflare account with a domain added (e.g., `absolutegeek.net`)
- Access to the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/)

## 1. Create the Tunnel

1. In the Zero Trust dashboard, go to **Networks > Tunnels**
2. Click **Create a tunnel**
3. Select **Cloudflared** as the connector type
4. Name the tunnel (e.g., `ai-router-lab`)
5. On the connector installation page, copy the **tunnel token** (the long string after `--token`)
6. Paste the token into `.secrets`:

```
CF_TUNNEL_TOKEN=eyJhIjoiNjQ1...your-token-here
```

## 2. Configure Public Hostname

On the tunnel's **Public Hostname** tab, add a route:

| Field | Value |
|-------|-------|
| Subdomain | `lab` |
| Domain | `absolutegeek.net` |
| Type | HTTP |
| URL | `traefik:80` |

This routes `lab.absolutegeek.net` to the Traefik reverse proxy inside the Docker network. Traefik then routes requests to the appropriate backend based on its label-based rules.

## 3. Zero Trust Access Policy (Recommended)

Without an access policy, anyone who knows your hostname can reach the API. Add a Zero Trust application to enforce authentication before traffic reaches your stack.

1. Go to **Access > Applications** in the Zero Trust dashboard
2. Click **Add an application** > **Self-hosted**
3. Configure:

| Field | Value |
|-------|-------|
| Application name | `AI Router` |
| Session duration | 24 hours (adjust to preference) |
| Subdomain | `lab` |
| Domain | `absolutegeek.net` |

4. Add a policy (e.g., **Allow** with an email-based rule):
   - Policy name: `Owner access`
   - Action: **Allow**
   - Include rule: **Emails** — add your email address

This means anyone accessing `lab.absolutegeek.net` must authenticate via Cloudflare's login page before any request reaches Traefik.

## 4. Start the Stack

```bash
make up
```

`cloudflared` starts alongside Traefik and will connect to Cloudflare's edge automatically. Check its logs:

```bash
docker logs cloudflared
```

You should see `Connection registered` messages indicating the tunnel is active.

## 5. Verify

```bash
curl https://lab.absolutegeek.net/health
```

If a Zero Trust policy is active, this will return a 302 redirect to the Cloudflare login page (expected). After authenticating in a browser, the `/health` endpoint should return the ai-router health response.

## Architecture

```
Internet → Cloudflare Edge (Zero Trust auth)
                ↓ (encrypted tunnel)
         cloudflared container
                ↓ (Docker network: ai-network)
         traefik:80
                ↓ (label-based routing)
         ai-router / vllm-router / vllm-primary
```

The `cloudflared` container makes outbound-only connections — no inbound ports are required on the host. Once host ports are locked down (Change 2), the only way to reach the stack externally is through the tunnel.

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| `cloudflared` exits immediately | `CF_TUNNEL_TOKEN` is missing or empty in `.secrets` |
| `ERR Failed to connect` in logs | Tunnel was deleted in the dashboard, or token is for a different tunnel |
| 502 Bad Gateway on public hostname | Traefik is not running, or the public hostname URL is misconfigured (should be `traefik:80`, not `localhost:80`) |
| Requests hang | Check that `cloudflared` is on the `ai-network` Docker network |
