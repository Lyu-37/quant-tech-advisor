"""Discord webhook publisher.

Two output paths:
  - send_daily_brief(): legacy plain-text + optional file attachment
  - send_embed():       structured embed (preferred for daily scanner)

Webhook setup (one-time):
  Server > Channel > Settings > Integrations > Webhooks > New Webhook
      $env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import urllib.request
import urllib.error


DISCORD_MSG_LIMIT = 1900   # 2000 char cap, leave buffer for code fences

# Discord blocks default Python urllib User-Agent via Cloudflare (error 1010).
# Need to look like a real client — Discord's own bot convention works.
USER_AGENT = "QuantAdvisor (https://github.com/local, 1.0)"


def _post(url: str, payload: dict, files: list[Path] | None = None) -> bool:
    """Single Discord POST. Returns True on 2xx response."""
    try:
        if files:
            # multipart upload for attaching the full report
            boundary = "----quant-advisor-boundary-9d3f"
            body = []
            body.append(f"--{boundary}".encode())
            body.append(b'Content-Disposition: form-data; name="payload_json"')
            body.append(b"Content-Type: application/json")
            body.append(b"")
            body.append(json.dumps(payload).encode("utf-8"))
            for i, f in enumerate(files):
                body.append(f"--{boundary}".encode())
                body.append(
                    f'Content-Disposition: form-data; name="files[{i}]"; '
                    f'filename="{f.name}"'.encode()
                )
                body.append(b"Content-Type: text/markdown")
                body.append(b"")
                body.append(f.read_bytes())
            body.append(f"--{boundary}--".encode())
            data = b"\r\n".join(body)
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": USER_AGENT,
                },
            )
        else:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        print(f"  ! Discord push HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        return False
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  ! Discord push failed: {e}")
        return False


def _chunk(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    """Split text on paragraph boundaries to fit Discord's limit."""
    out = []
    current = ""
    for para in text.split("\n\n"):
        # If single paragraph too big, hard-split
        if len(para) > limit:
            if current:
                out.append(current)
                current = ""
            for i in range(0, len(para), limit):
                out.append(para[i:i + limit])
            continue
        if len(current) + len(para) + 2 > limit:
            out.append(current)
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current:
        out.append(current)
    return out


def send_daily_brief(
    summary_text: str,
    report_md_path: Path | None = None,
    webhook_url: str | None = None,
) -> bool:
    """Push the daily summary to Discord; attach full report file if provided.

    Args:
        summary_text: short summary to display as the message body
        report_md_path: optional path to full markdown report to attach
        webhook_url: webhook URL; if None, reads DISCORD_WEBHOOK_URL env var

    Returns: True on full success, False if any send failed.
    """
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("  ! DISCORD_WEBHOOK_URL not set — skipping Discord push")
        return False

    chunks = _chunk(summary_text)
    ok_all = True

    # Send all but the last chunk as plain messages
    for chunk in chunks[:-1]:
        ok = _post(url, {"content": chunk, "username": "Quant Advisor"})
        ok_all = ok_all and ok

    # Last chunk: attach the full report file if provided
    files = []
    if report_md_path and report_md_path.exists():
        files = [report_md_path]
    final_payload = {"content": chunks[-1], "username": "Quant Advisor"}
    ok = _post(url, final_payload, files=files if files else None)
    ok_all = ok_all and ok

    if ok_all:
        print("  Discord push succeeded.")
    return ok_all


# ---------- Embed-based push (preferred) ----------

# Discord color palette (decimal RGB)
COLOR_GREEN  = 0x57F287   # bullish / strong
COLOR_YELLOW = 0xFEE75C   # neutral / mixed
COLOR_RED    = 0xED4245   # bearish / warning
COLOR_BLUE   = 0x5865F2   # informational

DISCORD_FIELD_LIMIT = 1024
DISCORD_DESC_LIMIT  = 4096
DISCORD_EMBED_LIMIT = 6000   # sum of all text in one embed


def score_to_color(composite_0_100: float) -> int:
    """Pick embed bar color from composite score."""
    if composite_0_100 >= 65:
        return COLOR_GREEN
    if composite_0_100 >= 40:
        return COLOR_YELLOW
    return COLOR_RED


def _truncate(text: str, limit: int, suffix: str = "...") -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)] + suffix


def send_embed(
    *,
    title: str,
    description: str = "",
    fields: list[dict] | None = None,
    color: int = COLOR_BLUE,
    footer: str = "",
    webhook_url: str | None = None,
) -> bool:
    """Send a single Discord embed via webhook.

    Each field dict: {"name": str, "value": str, "inline": bool (optional)}

    Truncates text to fit Discord limits silently. If a single embed would
    exceed 6000 total chars, fields beyond the budget are dropped.
    """
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("  ! DISCORD_WEBHOOK_URL not set — skipping Discord embed")
        return False

    safe_fields = []
    running = len(title) + len(description) + len(footer)
    for f in fields or []:
        name = _truncate(str(f.get("name", "")), 256)
        value = _truncate(str(f.get("value", "")), DISCORD_FIELD_LIMIT)
        cost = len(name) + len(value)
        if running + cost > DISCORD_EMBED_LIMIT - 100:
            break
        safe_fields.append({"name": name, "value": value,
                            "inline": bool(f.get("inline", False))})
        running += cost

    embed = {
        "title": _truncate(title, 256),
        "description": _truncate(description, DISCORD_DESC_LIMIT),
        "color": color,
        "fields": safe_fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if footer:
        embed["footer"] = {"text": _truncate(footer, 2048)}

    payload = {"embeds": [embed], "username": "Quant Advisor"}
    ok = _post(url, payload)
    if ok:
        print(f"  Discord embed pushed ({len(safe_fields)} fields, "
              f"{running} chars).")
    return ok
