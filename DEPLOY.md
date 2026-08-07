# Deploying Nexus Know as an internal team tool

Total cost: **~$6-7/month**. This gets you a hosted, always-on version of the app with
real login in front of it (not the role dropdown), reachable at a URL you control,
without opening any firewall ports on your server.

Stack: one small VPS running the app in Docker → Cloudflare Tunnel (free) exposes it
→ Cloudflare Access (free for ≤50 users) gates it behind real authentication.

---

## 0. Test locally first (do this before touching a server)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed.
From the project root:

```
docker compose build
docker compose up
```

Open `http://localhost:8501`. If Chat/Upload/Governance all work the same as when you
ran it with `uvicorn`/`streamlit` directly, you're ready to deploy. If something breaks,
fix it locally — debugging on the server is much slower.

Stop it with `Ctrl+C`, then `docker compose down`.

---

## 1. Get a VPS

Any of these work; pick based on what's easiest for you to sign up for:
- [DigitalOcean](https://www.digitalocean.com/) — Droplet, **2 GB RAM / 1 vCPU, ~$12/mo**
- [Hetzner](https://www.hetzner.com/cloud/) — similar spec, usually cheaper (~€5-6/mo)

Use **Ubuntu 22.04 or 24.04**. Don't go below 2 GB RAM — the embedding model
(`sentence-transformers`) and `unstructured`'s PDF layout models need the headroom;
a 1 GB box will likely get OOM-killed on the first document upload.

Note the server's IP address once it's created.

---

## 2. Install Docker on the server

SSH in (`ssh root@your-server-ip`), then:

```bash
curl -fsSL https://get.docker.com | sh
```

That's the official Docker install script — works on both Ubuntu versions above.

---

## 3. Get your code onto the server

Easiest: push this project to a **private** GitHub repo, then on the server:

```bash
git clone https://github.com/your-username/enterprise-rag.git
cd enterprise-rag
```

(No GitHub account handy? `scp -r` the folder from your machine instead:
`scp -r ./enterprise-rag-final root@your-server-ip:/root/enterprise-rag`)

---

## 4. Set your real environment variables on the server

```bash
cp .env.example .env
nano .env
```

Paste in your real `GROQ_API_KEY`. Save (`Ctrl+O`, Enter, `Ctrl+X` in nano).

---

## 5. Build and run

```bash
docker compose up -d --build
```

`-d` runs it in the background. Check it's healthy:

```bash
docker compose ps
docker compose logs -f backend    # Ctrl+C to stop watching logs
```

At this point the app is running on the server, reachable at
`http://your-server-ip:8501` — but that's an unauthenticated public URL. Don't share
that link around; the next steps fix that.

---

## 6. Expose it securely with Cloudflare Tunnel

This gets you a real HTTPS URL without opening any ports on the server.

1. Sign up at [Cloudflare](https://dash.cloudflare.com/sign-up) (free) and add a domain
   — if you don't have one, buy a cheap `.com` (~$10/year) through Cloudflare Registrar
   or any registrar and point its nameservers at Cloudflare.
2. In the dashboard: **Zero Trust → Networks → Tunnels → Create a tunnel** → choose
   **Cloudflared** → name it (e.g. `nexus-know`).
3. It gives you a `TUNNEL_TOKEN`. Add it to your server's `.env`:
   ```
   CLOUDFLARE_TUNNEL_TOKEN=your-token-here
   ```
4. In `docker-compose.yml`, uncomment the `cloudflared` service block.
5. In the Cloudflare dashboard, under the tunnel's **Public Hostname** settings, add a
   route: your subdomain (e.g. `nexus.yourcompany.com`) → service `http://frontend:8501`.
6. Redeploy:
   ```bash
   docker compose up -d --build
   ```
7. **Now remove the port mapping** in `docker-compose.yml`'s `frontend` service
   (delete the `ports: - "8501:8501"` line) and redeploy once more — the tunnel is the
   only way in now, so that public port isn't needed.

Visit `https://nexus.yourcompany.com` — it should load over HTTPS automatically.

---

## 7. Add real login with Cloudflare Access

Right now anyone with the URL can reach it. This step gates it behind actual auth.

1. Dashboard: **Zero Trust → Access → Applications → Add an application** → **Self-hosted**.
2. Domain: `nexus.yourcompany.com` (the one you just set up).
3. Add a policy — simplest option: **Allow** access to a list of specific email addresses
   (your team's emails), or **Allow** any email ending in `@yourcompany.com`.
4. Save.

Now visiting the URL prompts a one-time login code sent to the person's email before
they ever see the app. This is real authentication sitting in front of the whole thing
— including the role dropdown, which now just controls RBAC *within* an already-verified
user, the way it should.

---

## Updating the app later

```bash
git pull                       # or re-upload changed files
docker compose up -d --build
```

Your data (ChromaDB + audit log) persists across this because it's in Docker volumes,
not inside the containers.

---

## What this setup does *not* give you yet

Worth knowing so you don't assume more than you have:
- **Audit log is still SQLite.** Fine for a small team; if usage grows, revisit moving
  it to Postgres (see the README's "Next steps").
- **No automated backups.** The Docker volumes live on one VPS disk. If you care about
  not losing uploaded documents, set up a simple cron job to snapshot the volumes, or
  use your cloud provider's snapshot feature.
- **Cloudflare Access controls who can open the app — it does not replace the RBAC
  tag system inside it.** Both layers matter: Access answers "is this a real employee,"
  RBAC answers "which documents can this employee's role see."
