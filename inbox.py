"""Unified inbox storage: save fetched comments/mentions/DMs without duplicates, tag what needs attention."""

from __future__ import annotations

import re

import store

QUESTION = re.compile(r"\?|\b(price|how much|cost|available|deliver|shipping|when|where|open|book|order|combien|prix|disponible|livraison|"
                      r"cuánto|precio|bkam|بكم|كم|متى|أين|wie viel|preis)\b", re.I)
COMPLAINT = re.compile(r"\b(bad|worst|terrible|awful|scam|fake|refund|angry|disappointed|never again|not happy|unhappy|late|broken|"
                       r"rude|problem|issue|nul|arnaque|déçu|remboursement|problème|retard|estafa|malo|سيء|نصاب)\b|😡|😠|👎", re.I)
POSITIVE = re.compile(r"\b(love|great|amazing|awesome|perfect|thanks|thank you|merci|top|excellent|beautiful|bravo|gracias|شكرا|رائع)\b|"
                      r"😍|❤️|🔥|👏|🙏|💯", re.I)


def tag_of(text: str) -> str:
    t = text or ""
    if COMPLAINT.search(t):
        return "complaint"
    if QUESTION.search(t):
        return "question"
    if POSITIVE.search(t):
        return "positive"
    return ""


def save(account: dict, items: list[dict]) -> list[dict]:
    """Insert new items (dedupe on account + remote id). Returns the ones that are new and not written by us."""
    now = store.now()
    fresh = []
    for it in items:
        if not it.get("remote_id"):
            continue
        status = "mine" if it.get("mine") else "new"
        cur = store.update(
            "INSERT OR IGNORE INTO inbox(account_id,network,kind,remote_id,reply_to,thread_key,post_target_id,post_text,post_url,author_id,"
            "author_name,author_handle,author_avatar,text,url,created_at,status,tag,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (account["id"], account["network"], it["kind"], it["remote_id"], it.get("reply_to", ""), it.get("thread_key", ""),
             it.get("post_target_id"), it.get("post_text", ""), it.get("post_url", ""), it.get("author_id", ""), it.get("author_name", ""),
             it.get("author_handle", ""), it.get("author_avatar", ""), it.get("text", ""), it.get("url", ""), it.get("created_at") or now,
             status, "" if it.get("mine") else tag_of(it.get("text", "")), now))
        if cur and not it.get("mine"):
            fresh.append(it)
    return fresh
