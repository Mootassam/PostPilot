"""AI writing assistant: Claude (Anthropic API key) → local Ollama (free, offline) → simple built-in helpers.

Tasks: write a post from an idea, rewrite in a tone, shorten, hashtags, translate, adapt for a network,
and brainstorm post ideas.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta

import requests

import store

MODEL = "claude-opus-5-5"
SYSTEM = ("You are an expert social media copywriter. You write natural, engaging posts that sound human, follow each "
          "network's style and limits, and never invent facts, prices, or claims the user didn't give. "
          "Reply with ONLY the requested text — no preamble, no quotes, no explanations.")

NETWORK_STYLE = {
    "x": "X/Twitter: punchy, max 280 characters, 1-2 hashtags max.",
    "linkedin": "LinkedIn: professional, insightful, short paragraphs, a hook in the first line, 3-5 hashtags at the end.",
    "instagram": "Instagram: friendly, emojis welcome, strong first line, call to action, 5-15 relevant hashtags at the end.",
    "facebook": "Facebook: conversational, 1-3 short paragraphs, a question to drive comments.",
    "threads": "Threads: casual and conversational, max 500 characters.",
    "bluesky": "Bluesky: casual, max 300 characters.",
    "mastodon": "Mastodon: friendly, no hype, max 500 characters, CamelCase hashtags.",
    "tiktok": "TikTok caption: short, fun, trending tone, 3-5 hashtags.",
    "youtube": "YouTube description: first 2 lines matter most, include keywords, then details.",
    "pinterest": "Pinterest description: keyword-rich, helpful, max 500 characters.",
    "telegram": "Telegram channel post: clear, scannable, emojis as bullets.",
}

STOP = set("""a an the and or but if then so to of in on at by for with from up about into over after is are was were be been being
this that these those it its we you your our they their i me my he she his her them us not no yes can will just get got
de la le les des du un une et ou en au aux pour par sur dans avec est sont que qui ce cette ces il elle nous vous ils
el los las y o con por para del al es son que una uno""".split())


class AIError(Exception):
    pass


def provider() -> str:
    p = store.get("ai_provider") or "auto"
    if p == "off":
        return "off"
    if p in ("anthropic", "auto") and store.get_secret("anthropic_key"):
        return "anthropic"
    if p in ("ollama", "auto") and ollama_ok():
        return "ollama"
    return "basic"


def ollama_ok() -> bool:
    try:
        return requests.get("http://127.0.0.1:11434/api/tags", timeout=1.5).ok
    except requests.RequestException:
        return False


def _prompt(task: str, text: str, network: str, tone: str, lang: str, extra: str) -> str:
    style = NETWORK_STYLE.get(network, "")
    if task == "write":
        return f"Write a social media post about: {text}\nTone: {tone or 'engaging'}.\n{style}\n{extra}"
    if task == "rewrite":
        return f"Rewrite this post in a {tone or 'more engaging'} tone. Keep the meaning, links and facts.\n{style}\n\nPOST:\n{text}"
    if task == "shorten":
        return f"Make this post shorter and punchier while keeping links and key facts.\n{style}\n\nPOST:\n{text}"
    if task == "hashtags":
        return (f"Suggest 12 relevant hashtags for this post{(' on ' + network) if network else ''}. Mix popular and niche. "
                f"Reply with the hashtags only, space separated.\n\nPOST:\n{text}")
    if task == "translate":
        return f"Translate this post into {lang or 'English'}. Keep emojis, links, @mentions and the tone. Hashtags may be localized.\n\nPOST:\n{text}"
    if task == "adapt":
        return f"Adapt this post for {network}. {style}\nKeep links.\n\nPOST:\n{text}"
    if task == "ideas":
        return (f"Give 10 specific, varied social media post ideas for: {text}. Mix tips, questions, behind-the-scenes, "
                "offers and stories. One idea per line, no numbering.")
    if task == "first_comment":
        return f"Write a short first comment for this post (e.g. extra hashtags or a call to action).\n\nPOST:\n{text}"
    if task == "reply":
        return ("Write a short, warm, helpful reply to this comment/message from a customer, in the same language they used. "
                "Answer their question if the post contains the answer; never invent prices, dates or promises — if the info is missing, "
                "invite them to send a private message. No hashtags. Max 2 sentences.\n\n"
                f"OUR POST (context):\n{extra or '(not available)'}\n\nTHEIR MESSAGE:\n{text}")
    raise AIError("Unknown AI task.")


def _anthropic(prompt: str, effort: str = "low", schema: dict | None = None, system: str = SYSTEM, timeout: float = 90.0) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=store.get_secret("anthropic_key"), timeout=timeout, max_retries=2)
    output_config = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        raise AIError("Your Anthropic API key is not valid — check it in Settings → AI.") from None
    except anthropic.PermissionDeniedError:
        raise AIError("This Anthropic API key isn't allowed to use the model.") from None
    except anthropic.RateLimitError:
        raise AIError("The AI is busy (rate limit) — try again in a minute.") from None
    except anthropic.APIConnectionError:
        raise AIError("Can't reach the AI service — check your internet connection.") from None
    except anthropic.APIStatusError as e:
        raise AIError(f"AI error ({e.status_code}): {e.message}") from None
    if resp.stop_reason == "refusal":
        raise AIError("The AI declined this request — try rephrasing it.")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise AIError("The AI returned an empty answer — try again.")
    return text


def _ollama(prompt: str, json_mode: bool = False, system: str = SYSTEM) -> str:
    try:
        r = requests.post("http://127.0.0.1:11434/api/generate", timeout=600 if json_mode else 180, json={
            "model": store.get("ollama_model") or "llama3.1", "system": system, "prompt": prompt, "stream": False,
            **({"format": "json"} if json_mode else {})})
    except requests.RequestException as e:
        raise AIError(f"Ollama is not answering ({e}).") from None
    if r.status_code == 404:
        raise AIError(f"Ollama model “{store.get('ollama_model')}” is not installed — run: ollama pull {store.get('ollama_model')}")
    if not r.ok:
        raise AIError(f"Ollama error: {r.text[:200]}")
    return (r.json().get("response") or "").strip()


# ------------------------------------------------------------------ built-in fallbacks (no AI configured)
def keywords(text: str, n: int = 12) -> list[str]:
    words = [w.lower() for w in re.findall(r"[^\W\d_]{4,}", re.sub(r"https?://\S+|[@#]\w+", " ", text))]
    return [w for w, _ in Counter(w for w in words if w not in STOP).most_common(n)]


def _basic(task: str, text: str, network: str) -> str:
    if task == "hashtags":
        tags = ["#" + w for w in keywords(text, 10)]
        return " ".join(tags) or "#marketing #business"
    if task == "reply":
        low = text.lower()
        if "?" in text or any(w in low for w in ("price", "how much", "combien", "prix", "available", "deliver")):
            return "Thanks for your question! 😊 Send us a private message and we'll give you all the details right away."
        return "Thank you so much for your message! 🙏"
    if task == "shorten":
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        out = ""
        limit = {"x": 280, "bluesky": 300, "threads": 500, "mastodon": 500}.get(network, 280)
        for s in sentences:
            if len(out) + len(s) + 1 > limit:
                break
            out = (out + " " + s).strip()
        return out or text[:limit]
    raise AIError("Connect an AI first: add a Claude API key, or install Ollama (free) — Settings → AI assistant.")


def run(task: str, text: str, network: str = "", tone: str = "", lang: str = "", extra: str = "") -> dict:
    if not (text or "").strip():
        raise AIError("Write something first (or describe your idea).")
    p = provider()
    if p == "off":
        raise AIError("The AI assistant is turned off in Settings.")
    prompt = _prompt(task, text.strip(), network, tone, lang, extra)
    if p == "anthropic":
        out = _anthropic(prompt)
    elif p == "ollama":
        out = _ollama(prompt)
    else:
        out = _basic(task, text, network)
    out = out.strip().strip('"').strip()
    result = {"text": out, "provider": p}
    if task == "ideas":
        result["items"] = [re.sub(r"^[\-\d.)\s•*]+", "", ln).strip() for ln in out.splitlines() if ln.strip()][:12]
    return result


# ------------------------------------------------------------------ AI content calendar
PLAN_SCHEMA = {
    "type": "object",
    "properties": {"posts": {"type": "array", "items": {
        "type": "object",
        "properties": {"day": {"type": "integer"}, "pillar": {"type": "string"}, "topic": {"type": "string"},
                       "caption": {"type": "string"}, "hashtags": {"type": "string"}, "media_idea": {"type": "string"},
                       "format": {"type": "string", "enum": ["photo", "carousel", "video", "text"]}},
        "required": ["day", "pillar", "topic", "caption", "hashtags", "media_idea", "format"],
        "additionalProperties": False}}},
    "required": ["posts"],
    "additionalProperties": False,
}
PLAN_SYSTEM = ("You are a senior social media strategist. You build realistic, varied content calendars for small businesses: a healthy "
               "mix of value (tips, how-tos), trust (behind the scenes, team, testimonials), engagement (questions, polls) and "
               "promotion (offers, launches) — never more than 1 in 4 posts purely promotional. Captions are ready to publish, human, "
               "specific to the business, and never invent prices, discounts, awards or facts that the brief doesn't give.")
PILLARS = [("Tip", "💡 Quick tip from {biz}: [share one useful tip for {aud}].\n\nSave this post for later! 👇"),
           ("Behind the scenes", "👀 Behind the scenes at {biz}: [what happens before your customers see the result].\n\nWould you like to see more of this?"),
           ("Question", "Quick question for you 👇\n\n[ask {aud} something about their preferences]\n\nTell us in the comments!"),
           ("Offer", "🎁 [Your offer] at {biz}!\n\n[Why it's worth it] — [deadline].\n\n👉 [how to order / contact]"),
           ("Testimonial", "⭐ What our customers say:\n\n“[short review]” — [name]\n\nThank you for trusting {biz}! ❤️"),
           ("How-to", "How to [solve a common problem for {aud}] in 3 steps:\n\n1️⃣ [step]\n2️⃣ [step]\n3️⃣ [step]\n\nQuestions? Ask below!"),
           ("Fun / trend", "😄 [a light, fun post about {biz} or a current trend]\n\nTag a friend who needs to see this!")]


def _spread(n: int, days: int) -> list[int]:
    if n <= 0:
        return []
    return [min(days - 1, int(i * days / n)) for i in range(n)]


def plan(brief: dict) -> dict:
    """{"items": [...], "provider": ...} — a content calendar from a short brief."""
    biz = (brief.get("business") or "").strip()
    if not biz:
        raise AIError("Describe your business first (what you sell and to whom).")
    weeks = max(1, min(8, int(brief.get("weeks") or 2)))
    per_week = max(1, min(21, int(brief.get("per_week") or 4)))
    days, n = weeks * 7, weeks * per_week
    start = datetime.fromtimestamp(int(brief.get("start") or datetime.now().timestamp())).replace(hour=0, minute=0, second=0, microsecond=0)
    lang = brief.get("language") or "English"
    nets = ", ".join(brief.get("networks") or []) or "Facebook, Instagram"
    p = provider()
    if p == "off":
        raise AIError("The AI assistant is turned off in Settings.")
    if p in ("anthropic", "ollama"):
        day_names = ", ".join(f"{i}={(start + timedelta(days=i)).strftime('%a %d %b')}" for i in range(days))
        prompt = (f"Create a content calendar of exactly {n} posts over {days} days ({per_week} posts per week).\n"
                  f"BUSINESS: {biz}\nAUDIENCE: {brief.get('audience') or 'their customers'}\nGOALS: {brief.get('goals') or 'more engagement and customers'}\n"
                  f"TONE: {brief.get('tone') or 'friendly and professional'}\nNETWORKS: {nets}\n"
                  f"SPECIAL DATES / OFFERS / NEWS: {brief.get('events') or 'none given'}\n"
                  f"LANGUAGE: write every caption, topic and hashtag in {lang}.\n"
                  f"DAYS: day numbers are 0..{days - 1}: {day_names}. Spread posts evenly; match special dates to their day.\n"
                  "For each post: day, pillar (Tip, Behind the scenes, Question, Offer, Testimonial, How-to, Story, Product, Fun…), "
                  "topic (max 8 words), caption (ready to publish, with a hook and a call to action, NO hashtags in it), "
                  "hashtags (5-10, space separated), media_idea (the photo/video to shoot or use), format.")
        if p == "anthropic":
            raw = _anthropic(prompt, effort="medium", schema=PLAN_SCHEMA, system=PLAN_SYSTEM, timeout=240.0)
        else:
            raw = _ollama(prompt + '\nReply ONLY with JSON: {"posts":[{"day":0,"pillar":"","topic":"","caption":"","hashtags":"","media_idea":"","format":"photo"}]}',
                          json_mode=True, system=PLAN_SYSTEM)
        try:
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            posts = data.get("posts") or []
        except (ValueError, AttributeError):
            raise AIError("The AI answer could not be read — please try again.") from None
    else:
        aud = brief.get("audience") or "your customers"
        name = re.split(r"\s[—–-]\s|[,:(]", biz)[0].strip()[:40] or biz[:40]
        posts = []
        for i, d in enumerate(_spread(n, days)):
            pillar, tpl = PILLARS[i % len(PILLARS)]
            posts.append({"day": d, "pillar": pillar, "topic": pillar, "caption": tpl.format(biz=name, aud=aud),
                          "hashtags": " ".join("#" + w for w in keywords(biz + " " + aud, 6)), "media_idea": "[photo or short video]",
                          "format": "photo"})
    items = []
    for x in posts:
        try:
            d = max(0, min(days - 1, int(x.get("day", 0))))
        except (TypeError, ValueError):
            d = 0
        cap = str(x.get("caption") or "").strip()
        if not cap:
            continue
        tags = " ".join(t if t.startswith("#") else "#" + t for t in str(x.get("hashtags") or "").replace(",", " ").split() if t.strip("#"))
        items.append({"day": d, "date": int((start + timedelta(days=d)).timestamp()), "pillar": str(x.get("pillar") or "")[:40],
                      "topic": str(x.get("topic") or "")[:120], "caption": cap, "hashtags": tags[:400],
                      "media_idea": str(x.get("media_idea") or "")[:300], "format": str(x.get("format") or "photo")})
    if not items:
        raise AIError("The AI returned no posts — try again with a little more detail.")
    items.sort(key=lambda x: x["day"])
    return {"items": items, "provider": p}
