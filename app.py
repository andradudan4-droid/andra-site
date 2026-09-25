"""
Andra Dudan — AI assistants for local businesses
=================================================
A modern marketing site with a live sales assistant built in. The assistant
answers questions, qualifies the visitor, and emails the lead straight to you.

Environment variables to set in Render:
  GROQ_API_KEY     - your Groq key (the assistant's brain)
  RESEND_API_KEY   - your Resend key (sends the lead emails)
  NOTIFY_TO        - where leads go (defaults to andradudan4@gmail.com)
"""

from flask import Flask, request, jsonify, session, Response
import os
import re
import uuid
import html
import hashlib
import hmac
import time
import threading
import requests
from groq import Groq

app = Flask(__name__)
app.secret_key = (
    os.environ.get("SECRET_KEY")
    or hashlib.sha256((os.environ.get("GROQ_API_KEY") or os.environ.get("RESEND_API_KEY") or "frontdesk-fallback-secret").encode()).hexdigest()
)
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

_ip_hits = {}
_session_ts = {}
_lead_timers = {}


def _ip_rate_limited(ip, limit=18, window=60):
    now = time.time()
    hits = [t for t in _ip_hits.get(ip, []) if now - t < window]
    if len(hits) >= limit:
        _ip_hits[ip] = hits
        return True
    hits.append(now)
    _ip_hits[ip] = hits
    return False


def _prune_sessions(max_sessions=400, ttl=7200):
    now = time.time()
    stale = [k for k, ts in list(_session_ts.items()) if now - ts > ttl]
    for k in stale:
        _session_ts.pop(k, None)
        conversations.pop(k, None)
    if len(conversations) > max_sessions:
        oldest = sorted(_session_ts.items(), key=lambda kv: kv[1])[: len(conversations) - max_sessions]
        for k, _ in oldest:
            _session_ts.pop(k, None)
            conversations.pop(k, None)


def _schedule_lead_fallback(sid, delay=90):
    old = _lead_timers.pop(sid, None)
    if old:
        old.cancel()

    def _fire():
        _lead_timers.pop(sid, None)
        convo = conversations.get(sid)
        if convo and sid not in notified and has_contact_info(convo):
            notified.add(sid)
            send_lead_email(list(convo))

    t = threading.Timer(delay, _fire)
    t.daemon = True
    _lead_timers[sid] = t
    t.start()


@app.after_request
def _add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
NOTIFY_TO = os.environ.get("NOTIFY_TO", "andradudan4@gmail.com")
# The "from" address for lead emails. Until you verify a domain in Resend, the
# shared test address only delivers to your own Resend account email. Once you
# verify frontdesk.org.uk in Resend, set MAIL_FROM env var to
# "Frontdesk <leads@frontdesk.org.uk>" and leads can go to any address.
MAIL_FROM = os.environ.get("MAIL_FROM", "Frontdesk <onboarding@resend.dev>")

# Your real details — used by the assistant and shown on the page.
BRAND = "Frontdesk"
CONTACT_NAME = "Andra Dudan"
CONTACT_PHONE = "07493 396628"
CONTACT_PHONE_TEL = "07493396628"
CONTACT_EMAIL = "andradudan4@gmail.com"

# Paste your  payment link here (the combined £200 setup + £30/month link).
# Until it's set, the "Get started" buttons open the chat instead.
STRIPE_LINK = "https://buy.stripe.com/28E00j9K232X61m7q3bjW08"

# ---------------------------------------------------------------------------
#  Lead detection + organised lead email
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+44\s?|0)\d(?:[\s-]?\d){8,11}")


def _customer_text(convo):
    return " ".join(m["content"] for m in convo if m.get("role") == "user")


def find_email(convo):
    m = EMAIL_RE.search(_customer_text(convo))
    return m.group(0) if m else None


def find_phone(convo):
    for c in PHONE_RE.findall(_customer_text(convo)):
        d = re.sub(r"\D", "", c)
        if 10 <= len(d) <= 13:
            return c.strip()
    return None


def has_contact_info(convo):
    return bool(find_email(convo) or find_phone(convo))


CLOSING_RE = re.compile(
    r"\b(no longer interested|not interested|no thanks|no thank you|"
    r"that'?s all|that'?s it|that'?s everything|nothing else|all good|"
    r"that'?s great thank|thanks that'?s|goodbye|bye for now|no more|"
    r"i'?m good|im good|sounds good thanks)\b",
    re.I,
)


def _looks_like_closing(text):
    return bool(CLOSING_RE.search(text or ""))


def _transcript(convo):
    out = []
    for m in convo:
        if m["role"] == "user":
            out.append(f"Visitor: {m['content']}")
        elif m["role"] == "assistant":
            out.append(f"Assistant: {m['content']}")
    return "\n\n".join(out)


LEAD_SUMMARY_PROMPT = """You are turning a website chat into a clean sales lead
for Andra, who sells AI chat assistants to local businesses. Read the
conversation and output EXACTLY these labelled lines and nothing else. Fill each
from what the visitor actually said; write "Not specified" if unknown. Keep each
line short.

Name:
Business / trade:
Has a website?:
What they want help with:
How they handle enquiries now:
Best contact:
Other notes:"""


def summarise_lead(convo):
    try:
        r = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": LEAD_SUMMARY_PROMPT},
                {"role": "user", "content": _transcript(convo)},
            ],
            max_tokens=250,
            temperature=0.2,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        print(f"Lead summary failed: {e}")
        return None


def _parse_summary(structured):
    """Turn the model's labelled summary lines into a dict keyed by lowercase label."""
    out = {}
    if not structured:
        return out
    for line in structured.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _lead_fields(convo):
    """Tidy, ordered lead fields - reliable regex first, AI summary for the rest."""
    s = _parse_summary(summarise_lead(convo))

    def pick(*keys):
        for k in keys:
            v = s.get(k)
            if v and v.lower() not in ("not specified", "not provided", "n/a", "none", "-"):
                return v
        return None

    return {
        "Name": pick("name"),
        "Business / trade": pick("business / trade", "business", "trade"),
        "Phone": find_phone(convo),
        "Email": find_email(convo),
        "Has a website?": pick("has a website?", "has a website", "website"),
        "Wants help with": pick("what they want help with", "wants help with"),
        "Handles enquiries now": pick("how they handle enquiries now", "handles enquiries now"),
        "Best contact": pick("best contact"),
        "Notes": pick("other notes", "notes"),
    }


def _row(label, value):
    if not value:
        return ""
    return (
        '<tr>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #efe9df;color:#8a8378;'
        f'font-size:13px;white-space:nowrap;vertical-align:top;width:150px">{html.escape(label)}</td>'
        f'<td style="padding:10px 16px;border-bottom:1px solid #efe9df;color:#17201d;'
        f'font-size:14px;font-weight:600">{html.escape(str(value))}</td>'
        '</tr>'
    )


def _transcript_html(convo):
    rows = []
    for m in convo:
        if m["role"] == "user":
            who, color, bg = "Visitor", "#2449f5", "#f4f4ef"
        elif m["role"] == "assistant":
            who, color, bg = "Frontdesk Assistant", "#7c5cff", "#ffffff"
        else:
            continue
        text = html.escape(m["content"]).replace("\n", "<br>")
        rows.append(
            f'<div style="margin:0 0 12px">'
            f'<div style="font-size:11px;letter-spacing:.05em;text-transform:uppercase;'
            f'color:{color};font-weight:700;margin-bottom:4px">{who}</div>'
            f'<div style="background:{bg};border:1px solid #ece6da;border-radius:10px;'
            f'padding:11px 14px;font-size:14px;color:#2a2630;line-height:1.5">{text}</div>'
            f'</div>'
        )
    return "".join(rows)


def _lead_email_html(fields, convo):
    rows = "".join(_row(k, v) for k, v in fields.items())
    return (
        '<!DOCTYPE html><html><body style="margin:0;background:#efe9df;padding:24px;'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        '<div style="max-width:620px;margin:0 auto;background:#fff;border-radius:16px;'
        'overflow:hidden;box-shadow:0 2px 14px rgba(21,19,29,.10)">'
        '<div style="background:#17201d;padding:26px 30px">'
        '<div style="color:#2449f5;font-size:12px;letter-spacing:.2em;text-transform:uppercase;'
        'font-weight:700">Frontdesk</div>'
        '<div style="color:#fff;font-size:22px;font-weight:800;margin-top:5px;letter-spacing:-.01em">'
        'New lead from your website</div></div>'
        '<div style="padding:26px 30px">'
        '<p style="margin:0 0 20px;font-size:14px;color:#6c6760">'
        'Captured by your website assistant - here\'s who to follow up with:</p>'
        '<table style="width:100%;border-collapse:collapse;border:1px solid #efe9df;'
        f'border-radius:8px;overflow:hidden;margin-bottom:28px">{rows}</table>'
        '<div style="font-size:12px;letter-spacing:.05em;text-transform:uppercase;'
        'color:#a59e92;font-weight:700;margin-bottom:14px">Full conversation</div>'
        f'{_transcript_html(convo)}'
        '</div>'
        '<div style="background:#faf7f1;padding:16px 30px;border-top:1px solid #efe9df;'
        'font-size:12px;color:#a59e92">Sent automatically by your Frontdesk website assistant.</div>'
        '</div></body></html>'
    )


def send_lead_email(convo):
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set, skipping lead email")
        return

    fields = _lead_fields(convo)
    transcript = _transcript(convo)

    # Plain-text fallback for clients that won't render HTML.
    text_lines = ["NEW LEAD - Frontdesk", "===================="]
    for k, v in fields.items():
        text_lines.append(f"{k}: {v or 'Not specified'}")
    text_lines += ["====================", "", "Full conversation:", "", transcript]
    text_body = "\n".join(text_lines)

    html_body = _lead_email_html(fields, convo)
    phone = fields["Phone"] or "no number yet"

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                # Configurable via the MAIL_FROM env var (see top of file).
                "from": MAIL_FROM,
                "to": [NOTIFY_TO],
                "subject": f"New lead from your website - {phone}",
                "text": text_body,
                "html": html_body,
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"Resend error: {resp.status_code} {resp.text}")
        else:
            print(f"Resend OK {resp.status_code}: lead -> {NOTIFY_TO}")
    except Exception as e:
        print(f"Failed to send lead email: {e}")


# ---------------------------------------------------------------------------
#  The sales assistant's brain
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = f"""
You are the friendly, sharp assistant on the website of {BRAND}, a service by
{CONTACT_NAME} that builds AI chat assistants for local businesses (hairdressers,
barbers, builders, plumbers, electricians, decorators, dog groomers, beauticians,
garages and more) around Portsmouth and beyond. You ARE a live example of what
{BRAND} builds, so be genuinely good - warm, confident, concise, with personality.

WHAT {BRAND.upper()} OFFERS:
- A smart assistant that lives on a business's website (and can sit on their
  Google profile link too). It answers customer questions instantly 24/7,
  qualifies them, and sends every lead straight to the owner's inbox - tidied
  into a clean summary, not a messy chat log.
- It's trained on that business's own services, prices and details.
- It captures booking ENQUIRIES (name, what they want, preferred timing,
  contact) - the owner confirms the actual appointment. It does NOT book into
  their calendar, so it never promises a slot it can't see.
- WEBSITE INCLUDED: the £30/month plan includes a custom website built for the
  business with the assistant on it, hosting, and ongoing updates - it's all in
  one price. If they already have a website, the assistant can go on a page Andra
  sets up for them.

HOW IT WORKS (3 steps):
1. You tell Andra about your business. 2. Andra builds your website and trains
your assistant (usually within a few days). 3. Leads start landing in your inbox.

PRICING (real - you may quote it):
- £30/month plus a one-off £200 setup fee, everything included: a custom website, the AI assistant trained on
  your services and prices, hosting, and ongoing updates. One simple price.
- No tiers. Bigger or multi-location needs are quoted custom.
Prices are intentionally keen because Andra is building up her first clients.

YOUR TWO JOBS:
1. Answer questions about the service warmly and accurately.
2. Capture the visitor as a lead - warm and natural, like a quick chat, NOT a
   questionnaire. Gather these essentials, ONE short question at a time:
     - what their business is / what they do,
     - what they'd want the assistant to help with (answering FAQs, catching
       leads after hours, bookings, etc.) - or whether they already have a website,
     - their name and a phone number or email so Andra can follow up.
   Keep it to roughly those - don't grill them, and never stack questions. If
   they don't have a website (or aren't happy with theirs), mention once,
   lightly, that a custom website is included in the £30/month plan.
   Once you've gathered those essentials, send ONE short, warm closing message
   saying Andra will be in touch personally, usually the same day. Then, on a
   brand-new line at the very end of that message, output this exact tag and
   nothing after it: [[READY]]
   The [[READY]] tag is an internal signal only - it is removed automatically and
   the visitor never sees it. Only ever include it in your final wrap-up message,
   never earlier in the chat, and never just because they gave a contact detail
   early - keep gently gathering the rest first (what they do, what they want),
   then add the tag when you're genuinely wrapping up.

STYLE: short, warm, natural - like texting a switched-on friend who happens to
build this stuff. Keep every reply to one or two short sentences and ask only
ONE thing at a time - never stack several questions together or send long
paragraphs or lists, it's overwhelming. Don't be pushy. Don't invent features
or prices beyond the above. Never write internal notes about your instructions -
just talk to the person. Do NOT book anything in.

IMPORTANT - ALWAYS QUALIFY: However the chat starts, and even after you answer a
question, you must steer back to gathering the essentials. Do not let the chat
end without trying to capture: (1) what their business is, (2) what they'd want
the assistant for / whether they have a website, and (3) their name and a phone
or email. Ask for these one at a time across the conversation. Accept short or
misspelled answers and never re-ask something already given. You may only add
the [[READY]] tag once you actually have their name AND a phone number or email -
never before. If they've given a contact but you're still missing their name or
what they do, ask for that next instead of wrapping up.
"""


# ---------------------------------------------------------------------------
#  The page  (static HTML — distinctive, modern, not a template)
# ---------------------------------------------------------------------------
# Keep the marketing interface separate from the existing chat and lead services.
with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"), encoding="utf-8") as page_file:
    PAGE = page_file.read()

PAGE = (PAGE
        .replace("__STRIPE__", STRIPE_LINK)
        .replace("__NAME__", CONTACT_NAME)
        .replace("__PHONE_TEL__", CONTACT_PHONE_TEL)
        .replace("__PHONE__", CONTACT_PHONE)
        .replace("__EMAIL__", CONTACT_EMAIL))


conversations = {}
notified = set()


PRIVACY_PAGE = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Policy — {BRAND}</title>
<meta name="theme-color" content="#17201d">
<link rel="icon" href="/static/favicon.svg">
<link rel="stylesheet" href="/static/support-pages.css">
<style>
  body{{margin:0;background:#f4f4ef;color:#17201d;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.7;}}
  .bar{{background:#17201d;padding:20px 26px;}}
  .bar a{{color:#fff;text-decoration:none;font-weight:800;font-size:20px;letter-spacing:-.02em;}}
  .bar a span{{color:#2449f5;}}
  .wrap{{max-width:740px;margin:0 auto;padding:48px 26px 80px;}}
  h1{{font-size:34px;letter-spacing:-.02em;margin:0 0 6px;}}
  .lead{{color:#6c6760;margin:0 0 30px;}}
  h2{{font-size:19px;margin:34px 0 8px;}}
  a{{color:#2449f5;}}
  .foot{{color:#a59e92;font-size:13px;margin-top:50px;border-top:1px solid #e4ded2;padding-top:20px;}}
</style></head><body>
<div class="bar"><a href="/">Front<span>desk</span></a></div>
<div class="wrap">
  <h1>Privacy Policy</h1>
  <p class="lead">How {BRAND} ({CONTACT_NAME}) looks after the information you share with us.</p>

  <p>This policy explains what we collect when you contact us through this website, why we collect it, and your rights over it. {CONTACT_NAME}, trading as {BRAND}, is the data controller.</p>

  <h2>What we collect</h2>
  <p>When you use the chat assistant or get in touch, we collect only what you choose to give us &mdash; typically your name, phone number or email, the kind of business you run, and what you&rsquo;d like help with.</p>

  <h2>Why we collect it &amp; our lawful basis</h2>
  <p>We use your details solely to reply to your enquiry, discuss how we could help, and follow up about our service. Our lawful basis is taking steps at your request before entering into a contract, and our legitimate interest in responding to enquiries.</p>

  <h2>Who we share it with</h2>
  <p>We don&rsquo;t sell your data or use it for advertising. To run the website assistant, your messages are processed by our AI provider (Groq) to generate replies, and your enquiry is emailed to us through Resend. These providers process the information only to deliver that service. We may contact you by phone, text, WhatsApp or email to follow up.</p>

  <h2>How long we keep it</h2>
  <p>We keep enquiry details only as long as needed to deal with your enquiry and any work that follows, and for our normal business records, after which they are deleted.</p>

  <h2>Cookies</h2>
  <p>The site uses a single essential cookie to remember your chat session. We don&rsquo;t use advertising or tracking cookies, and our analytics are privacy-friendly and cookie-free.</p>

  <h2>Your rights</h2>
  <p>You can ask us to see, correct, or delete the information we hold about you, or to stop using it &mdash; just get in touch. You also have the right to complain to the UK&rsquo;s Information Commissioner&rsquo;s Office (ico.org.uk).</p>

  <h2>Contact</h2>
  <p>For anything about your data, email <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a> or call <a href="tel:{CONTACT_PHONE_TEL}">{CONTACT_PHONE}</a>.</p>

  <div class="foot">&copy; {BRAND} · AI assistants for local businesses · Portsmouth · <a href="/">Back to home</a></div>
</div></body></html>"""


_pay_btn = (f'<a class="pay-btn" href="{STRIPE_LINK}">Start my plan →</a>'
            if STRIPE_LINK else
            '<p style="color:#a59e92">Payment link not set up yet — please get in touch.</p>')

PAY_PAGE = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Customer payments — {BRAND}</title>
<meta name="theme-color" content="#17201d">
<link rel="icon" href="/static/favicon.svg">
<link rel="stylesheet" href="/static/support-pages.css">
<style>
  body{{margin:0;background:#f4f4ef;color:#17201d;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.7;}}
  .bar{{background:#17201d;padding:20px 26px;}}
  .bar a{{color:#fff;text-decoration:none;font-weight:800;font-size:20px;letter-spacing:-.02em;}}
  .bar a span{{color:#2449f5;}}
  .wrap{{max-width:560px;margin:0 auto;padding:48px 26px 80px;}}
  h1{{font-size:32px;letter-spacing:-.02em;margin:0 0 6px;}}
  .lead{{color:#6c6760;margin:0 0 30px;}}
  .card{{background:#fff;border:1px solid #e4ded2;border-radius:18px;padding:30px;box-shadow:0 10px 30px rgba(0,0,0,.05);}}
  .price{{font-size:40px;font-weight:800;letter-spacing:-.02em;}}
  .price span{{font-size:17px;font-weight:600;color:#6c6760;}}
  ul{{padding-left:20px;margin:18px 0 26px;}}
  li{{margin:7px 0;}}
  .pay-btn{{display:block;text-align:center;background:#2449f5;color:#fff;text-decoration:none;font-weight:700;font-size:17px;padding:16px;border-radius:12px;}}
  .pay-btn:hover{{background:#193aca;}}
  .note{{color:#6c6760;font-size:14px;margin-top:22px;text-align:center;}}
  .note a{{color:#2449f5;}}
  .foot{{color:#a59e92;font-size:13px;margin-top:40px;text-align:center;}}
  .foot a{{color:#a59e92;}}
</style></head><body>
<div class="bar"><a href="/">Front<span>desk</span></a></div>
<div class="wrap">
  <h1>Customer payments</h1>
  <p class="lead">Already set up with {BRAND}? Start your plan below — it takes a minute and renews automatically each month.</p>
  <div class="card">
    <div class="price">£30<span>/month</span></div>
    <div style="color:#6c6760;font-weight:600;margin:-4px 0 2px;">+ £200 one-off setup</div>
    <div style="color:#a59e92;font-size:14px;margin-bottom:6px;">£230 due today, then £30/month</div>
    <ul>
      <li>Your custom website &amp; AI assistant, kept live</li>
      <li>Hosting included</li>
      <li>Ongoing updates and tweaks</li>
      <li>Cancel anytime</li>
    </ul>
    {_pay_btn}
    <p class="note">Secure checkout by Stripe. You can pay by card.<br>By subscribing you agree to our <a href="/terms">Terms of Service</a>.</p>
  </div>
  <p class="note">New here and not set up yet? <a href="/">Start on the home page</a> — we'll have a quick chat and build your site first, then you come back here to pay.</p>
  <div class="foot">&copy; {BRAND} · Portsmouth · <a href="/">Back to home</a></div>
</div></body></html>"""


TERMS_PAGE = f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Terms of Service — {BRAND}</title>
<meta name="theme-color" content="#17201d">
<link rel="icon" href="/static/favicon.svg">
<link rel="stylesheet" href="/static/support-pages.css">
<style>
  body{{margin:0;background:#f4f4ef;color:#17201d;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.7;}}
  .bar{{background:#17201d;padding:20px 26px;}}
  .bar a{{color:#fff;text-decoration:none;font-weight:800;font-size:20px;letter-spacing:-.02em;}}
  .bar a span{{color:#2449f5;}}
  .wrap{{max-width:740px;margin:0 auto;padding:48px 26px 80px;}}
  h1{{font-size:34px;letter-spacing:-.02em;margin:0 0 6px;}}
  .lead{{color:#6c6760;margin:0 0 30px;}}
  h2{{font-size:19px;margin:34px 0 8px;}}
  a{{color:#2449f5;}}
  .foot{{color:#a59e92;font-size:13px;margin-top:50px;border-top:1px solid #e4ded2;padding-top:20px;}}
</style></head><body>
<div class="bar"><a href="/">Front<span>desk</span></a></div>
<div class="wrap">
  <h1>Terms of Service</h1>
  <p class="lead">The plain-English terms for using {BRAND}. By subscribing, you agree to these.</p>

  <p>These terms are between you (the customer) and {CONTACT_NAME}, trading as {BRAND} (&ldquo;we&rdquo;, &ldquo;us&rdquo;). They apply when you sign up for and use our website and AI assistant service.</p>

  <h2>1. What you get</h2>
  <p>A custom website with an AI assistant trained on your business, hosted by us, with the leads it captures sent to your inbox. We include reasonable ongoing updates and tweaks. Larger pieces of work &mdash; such as a full redesign, extra pages or new features beyond the original build &mdash; are quoted and agreed separately.</p>

  <h2>2. Price and payment</h2>
  <p>The service is a one-off setup fee of &pound;200 plus &pound;30 per month. Payments are taken securely through Stripe; your first payment is the setup fee plus your first month, and the &pound;30 monthly fee then recurs automatically until you cancel. Prices may change in future, but we&rsquo;ll always give you notice before any change affects you.</p>

  <h2>3. The setup fee</h2>
  <p>The &pound;200 setup fee covers the work of building and setting up your site and assistant. Because that work is done upfront, the setup fee is non-refundable once we&rsquo;ve started building.</p>

  <h2>4. Failed payments</h2>
  <p>If a monthly payment fails, Stripe will retry and let you know. If it stays unpaid, we may pause your website and assistant until payment is back up to date. We&rsquo;ll give you reasonable notice before pausing anything.</p>

  <h2>5. Cancelling</h2>
  <p>There&rsquo;s no lock-in &mdash; you can cancel anytime. Cancelling stops future monthly payments; we don&rsquo;t refund the current month. When your subscription ends, your website and assistant are taken down or paused, as they&rsquo;re hosted and maintained by us as part of the ongoing fee.</p>

  <h2>6. Hosting and ownership</h2>
  <p>While you&rsquo;re subscribed, we host and maintain your website, its code and the assistant. These remain ours and stay live for as long as your subscription is active. Your own content &mdash; your business name, logo, photos and text &mdash; remains yours.</p>

  <h2>7. Your domain</h2>
  <p>If you already own your domain name (e.g. yourbusiness.co.uk), it stays yours. If we register a domain on your behalf as part of the service, it remains under our account while you&rsquo;re subscribed; we&rsquo;ll discuss transferring it to you if you&rsquo;d like to take it with you.</p>

  <h2>8. Your content and conduct</h2>
  <p>You confirm that any logos, images, text or other material you give us are yours to use, or that you have permission to use them. The service must not be used for anything unlawful.</p>

  <h2>9. What the assistant does (and doesn&rsquo;t)</h2>
  <p>The AI assistant answers customer questions and captures enquiries to pass to you. It doesn&rsquo;t guarantee a particular number of leads or bookings, and it doesn&rsquo;t confirm appointments on your behalf &mdash; you decide which enquiries to take forward.</p>

  <h2>10. Availability and liability</h2>
  <p>We aim to keep your site and assistant running reliably, but we can&rsquo;t guarantee they&rsquo;ll be available 100% of the time (hosting providers and other services can have outages). To the extent the law allows, we&rsquo;re not liable for lost business, lost profits or other indirect losses arising from downtime or from the service. Nothing in these terms limits liability where it can&rsquo;t legally be limited.</p>

  <h2>11. Privacy</h2>
  <p>How we handle data is set out in our <a href="/privacy">Privacy Policy</a>.</p>

  <h2>12. Changes to these terms</h2>
  <p>We may update these terms from time to time. The version on this page is the current one, and we&rsquo;ll let you know if we make a significant change.</p>

  <h2>13. Governing law</h2>
  <p>These terms are governed by the law of England and Wales.</p>

  <h2>14. Contact</h2>
  <p>Questions about these terms? Email <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a> or call <a href="tel:{CONTACT_PHONE_TEL}">{CONTACT_PHONE}</a>.</p>

  <div class="foot">&copy; {BRAND} · AI assistants for local businesses · Portsmouth · <a href="/">Back to home</a></div>
</div></body></html>"""


@app.route("/")
def home():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return Response(PAGE, mimetype="text/html")


@app.route("/privacy")
def privacy():
    return Response(PRIVACY_PAGE, mimetype="text/html")


@app.route("/pay")
def pay():
    return Response(PAY_PAGE, mimetype="text/html")


@app.route("/terms")
def terms():
    return Response(TERMS_PAGE, mimetype="text/html")


@app.route("/sitemap.xml")
def sitemap():
    pages = ["/", "/pay", "/privacy", "/terms"]
    base = "https://frontdesk.org.uk"
    urls = "".join(f"<url><loc>{base}{p}</loc></url>" for p in pages)
    return Response(
        f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
        mimetype="application/xml",
    )


@app.route("/robots.txt")
def robots():
    return Response(
        "User-agent: *\nAllow: /\nSitemap: https://frontdesk.org.uk/sitemap.xml\n",
        mimetype="text/plain",
    )


@app.route("/_debug/email")
def debug_email():
    admin_token = (os.environ.get("ADMIN_TOKEN") or "").strip()
    supplied = (request.args.get("key") or request.headers.get("X-Admin-Token") or "").strip()
    if not admin_token or not supplied or not hmac.compare_digest(supplied, admin_token):
        return Response("not found", status=404)
    info = {
        "resend_key_set": bool(RESEND_API_KEY),
        "notify_to": NOTIFY_TO,
        "mail_from": MAIL_FROM,
    }
    if request.args.get("send") == "1":
        if not RESEND_API_KEY:
            info["send_result"] = {"skipped": "RESEND_API_KEY not set"}
        else:
            try:
                r = requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                    json={"from": MAIL_FROM, "to": [NOTIFY_TO],
                          "subject": "Frontdesk — test email",
                          "text": "If you can read this, lead emails are working."},
                    timeout=15)
                info["send_result"] = {"status": r.status_code, "body": r.text[:600]}
            except Exception as e:
                info["send_result"] = {"error": str(e)}
    return jsonify(info)


@app.route("/chat", methods=["POST"])
def chat():
    ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
    if _ip_rate_limited(ip):
        return jsonify({"reply": "Give me just a moment — you're sending messages a bit fast!"}), 429

    sid = session.get("sid") or str(uuid.uuid4())
    session["sid"] = sid
    _session_ts[sid] = time.time()
    _prune_sessions()
    if sid not in conversations:
        conversations[sid] = [{"role": "system", "content": SYSTEM_PROMPT}]
    convo = conversations[sid]
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()[:1500]
    if not user_msg:
        return jsonify({"reply": "How can I help with your website or AI assistant today?"})
    convo.append({"role": "user", "content": user_msg})

    try:
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=convo,
            max_tokens=160,
            temperature=0.4,
            timeout=20,
        )
        reply = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"Chat completion failed: {e}")
        convo.pop()
        return jsonify({"reply": "Sorry — I had a brief hiccup there. Could you send that again?"})

    # The assistant signals it has finished gathering the essentials with an
    # internal READY tag. Strip it so the visitor never sees it.
    lead_ready = bool(re.search(r"\[\[?\s*READY\s*\]?\]", reply, re.I))
    reply = re.sub(r"\[\[?\s*READY\s*\]?\]", "", reply).strip()
    if not reply:
        reply = ("Brilliant — that's everything I need. Andra will be in touch "
                 "personally, usually the same day.")
    convo.append({"role": "assistant", "content": reply})

    # Only email once the assistant has actually wrapped up (READY tag), so the
    # business type and what they want are captured - not the instant a contact
    # detail appears. Closing-phrase / long-chat / 90s inactivity are safety nets
    # so a lead is never lost. Sent at most once per visitor.
    if sid not in notified and has_contact_info(convo):
        if lead_ready or _looks_like_closing(user_msg) or len(convo) >= 8:
            notified.add(sid)
            old = _lead_timers.pop(sid, None)
            if old:
                old.cancel()
            threading.Thread(target=send_lead_email, args=(list(convo),), daemon=True).start()
        else:
            _schedule_lead_fallback(sid, delay=90)

    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5002)))
