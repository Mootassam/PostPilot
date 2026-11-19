# PostPilot — Social Media Scheduler

Plan, create and publish to every social network from one local Windows app.

## Networks

| Network | How it connects | What you can post |
|---|---|---|
| Facebook Pages | Meta login (your Meta app) or token | text, link, 1–10 photos, video, first comment |
| Instagram (Business/Creator) | same Meta login | photo, carousel (up to 10), Reels, alt text, first comment |
| X (Twitter) | your 4 API keys (Free plan works) | text, up to 4 images, video, auto-threads |
| LinkedIn (profile) | LinkedIn login or token | text, link card, images (multi), video |
| Threads | token | text, images, carousel, auto-threads |
| YouTube | Google login | video uploads with title/privacy |
| Pinterest | Pinterest login or token | pins with board, title, link |
| Telegram channels | bot token | text, photos, albums, video |
| Bluesky | app password | text with clickable links/#tags, 4 images, threads |
| Mastodon | access token | text, 4 images/video, threads |
| Discord | webhook | text + files |
| TikTok, WhatsApp, Snapchat… | Reminder mode | notification with caption ready to copy + media to download |

Each connector uses the network's **official API** with keys you create. The step-by-step guide is shown in the app.

## Features

- **Composer**: pick accounts, write once or customize per network, live previews for every network, character counters, auto-threads for long text, media (drag-drop, reorder, alt text, library), link + UTM tracking, first comment, emoji, hashtag groups, templates.
- **AI assistant**: write from an idea, improve, rewrite in a tone, shorten, hashtags, translate, adapt for each network, brainstorm ideas. It uses Claude (your Anthropic key, `claude-opus-5-5` with automatic server-side fallback) or free local Ollama.
- **Scheduling**: post now, pick a time (with best-time suggestions), add to queue (time slots per account), recurring/evergreen posts, drafts.
- **Calendar**: month and week views, drag-and-drop rescheduling.
- **Publishes by itself (PC can be off)**: scheduled YouTube videos and Facebook Page posts are uploaded early and the network makes them public at the exact time (Settings → on by default). Other networks' APIs don't allow this.
- **Stats everywhere**: views, likes, comments (and shares where the network reports them) on Posts, Dashboard and Analytics; video covers generated automatically (ffmpeg).
- **Reliability**: parallel publishing (one post at a time per account), automatic retries with backoff for network errors, clear error messages, expired-login detection, token auto-renewal (Threads, Pinterest, YouTube), crash-safe queue.
- **Media**: automatic per-network resizing/conversion (e.g. Instagram aspect ratio, Bluesky ≤ 1 MB). Instagram/Threads media is served through a temporary secure Cloudflare link only while posting.
- **Ideas board** (kanban), **Media library**, **Analytics** (likes, comments, shares, views per network, top posts, best-time heat map).
- **Automation**: RSS/blog auto-posting, CSV bulk scheduling, recurring posts.
- **Brands/workspaces** for clients, backup and restore, dark mode, encrypted logins (Windows DPAPI).

## Run

- `Start.bat` → http://localhost:5088 (`Start-Demo.bat` = fake demo network)
- Installer: `powershell -ExecutionPolicy Bypass -File build.ps1` → `installer_output\PostPilot-Setup.exe`
- Tests: `python -m pytest tests -q` (fake networks, no internet)

OAuth redirect address to register in developer apps: `http://localhost:5088/oauth/callback`
