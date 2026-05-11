# BFD — Railway deploy

Pure cloud deploy. No home node, no residential proxy. Works because
the only two delivery platforms now are:

- **Wolt** — public JSON API, returns the same data from any IP
- **Uber Eats** — Playwright with cookie-consent dismiss; we've verified
  it works from datacenter IPs (Cloudflare doesn't gate the feed page)

## Steps

```bash
railway login                                     # browser flow
cd ~/BFD
railway init                                      # create or link a project
railway up                                        # build + deploy the Dockerfile
railway variables --set BFD_DATA_DIR=/data        # SQLite path
railway volume add --mount-path /data --size 1    # persistent disk
railway redeploy
railway domain                                    # → public URL
```

Bootstrap the BFS list once:

```bash
curl -X POST "$(railway domain)/api/bfs/refresh"
```

## BFS list cron (once a month)

The BFS list of recommended places changes maybe a few times a month;
no need to scrape it on every request. Set up a Railway cron that hits
the refresh endpoint:

1. In the Railway dashboard, add a new **Cron** service in the same project.
2. Schedule: `0 4 1 * *` (04:00 UTC on the 1st of every month).
3. Command:
   ```sh
   curl -X POST -fSs https://$RAILWAY_PUBLIC_DOMAIN/api/bfs/refresh
   ```
   (use the service's variable name for the deployed domain, or hard-code).

Alternative: keep BFS refresh manual and run the curl yourself when you
remember. The list rarely changes.

## Why open/closed status is always fresh

- **Wolt** has `PLATFORM_TTL_SEC["wolt"] = 0` → every /api/check hits the
  Wolt JSON API live (~1 s). Open/closed reflects reality at request
  time.
- **Uber Eats** has `PLATFORM_TTL_SEC["ubereats"] = 60` → coalesces
  repeat requests for the same address within a minute, but is short
  enough that "currently open" stays fresh. UE Playwright uses a warm
  Chromium kept across requests (`bfd.browser_pool`) to bring per-call
  latency from ~12 s down to ~5–7 s.

If you want different policies, edit `bfd/config.py:PLATFORM_TTL_SEC`.

## Image

`Dockerfile` is based on `mcr.microsoft.com/playwright/python:v1.49.0-jammy`
which bundles a working Chromium plus the OS libs Chromium needs. Image
is ~1.7 GB but cold-starts in <10 s on Railway.

## Volume

The `BFD_DATA_DIR` env var (default `data/` in source tree) is the
writable directory for SQLite. On Railway, mount a volume at `/data`
and set `BFD_DATA_DIR=/data` so the BFS list and caches survive
restarts and redeploys.

## Troubleshooting

- Uber Eats adapter consistently fails → Uber may have changed its
  layout. Adapter logs `AdapterUnavailable` and the rest keeps working
  with just Wolt.
- BFS scrape never ran → hit `POST /api/bfs/refresh`. First-time scrape
  also fetches ~448 individual pages for long descriptions (~30 s).
