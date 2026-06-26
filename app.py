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
import threading
import requests
from groq import Groq

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-later")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

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

# Paste your Stripe payment link here (the combined £45 setup + £30/month link).
# Until it's set, the "Get started" buttons open the chat instead.
STRIPE_LINK = "https://buy.stripe.com/6oUbJ19K26f989u6lZbjW03"

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
        f'<td style="padding:10px 16px;border-bottom:1px solid #efe9df;color:#15131d;'
        f'font-size:14px;font-weight:600">{html.escape(str(value))}</td>'
        '</tr>'
    )


def _transcript_html(convo):
    rows = []
    for m in convo:
        if m["role"] == "user":
            who, color, bg = "Visitor", "#ff5a3c", "#f7f3ec"
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
        '<div style="background:#15131d;padding:26px 30px">'
        '<div style="color:#ff5a3c;font-size:12px;letter-spacing:.2em;text-transform:uppercase;'
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
- £30/month plus a one-off £45 setup fee, everything included: a custom website, the AI assistant trained on
  your services and prices, hosting, and ongoing updates. One simple price.
- No tiers, no setup fee. Bigger or multi-location needs are quoted custom.
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
   lightly, that Andra can build one as a paid add-on on a custom quote.
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
PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Frontdesk — AI assistants that catch every customer</title>
<meta name="description" content="Smart AI chat assistants for local businesses. Answer customers 24/7, qualify them, and get every lead in your inbox.">
<meta name="theme-color" content="#15131d">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Frontdesk">
<meta property="og:title" content="Frontdesk — AI assistants that catch every customer">
<meta property="og:description" content="Smart AI chat assistants for local businesses. Answer customers 24/7, qualify them, and drop every lead in your inbox.">
<meta property="og:url" content="https://frontdesk.org.uk">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2315131d'/%3E%3Ctext x='16' y='23' font-family='Arial,sans-serif' font-size='20' font-weight='bold' fill='%23ff5a3c' text-anchor='middle'%3EF%3C/text%3E%3C/svg%3E">
<!-- Analytics: privacy-friendly, no cookies. Create a free account at plausible.io,
     add the domain "frontdesk.org.uk", and stats start flowing - no code change needed.
     Prefer Google Analytics? Give Claude your G-XXXX ID and it'll swap this out. -->
<script defer data-domain="frontdesk.org.uk" src="https://plausible.io/js/script.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700;12..96,800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#15131d; --ink2:#211d2e; --bg:#f7f3ec; --surface:#ffffff;
    --accent:#ff5a3c; --accent2:#7c5cff; --muted:#6c6760; --line:rgba(0,0,0,.09);
    --disp:'Bricolage Grotesque',-apple-system,sans-serif;
    --body:'Inter',-apple-system,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  html{scroll-behavior:smooth;}
  body{font-family:var(--body);color:var(--ink);background:var(--bg);line-height:1.6;-webkit-font-smoothing:antialiased;}
  a{color:inherit;text-decoration:none;}
  .wrap{max-width:1120px;margin:0 auto;padding:0 26px;}
  .disp{font-family:var(--disp);font-weight:800;letter-spacing:-.02em;line-height:1.02;}
  .accent{color:var(--accent);}
  .btn{display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;
       padding:15px 28px;border-radius:100px;font-weight:600;font-size:15px;border:none;cursor:pointer;
       transition:transform .15s ease, box-shadow .15s ease;box-shadow:0 8px 24px rgba(255,90,60,.32);}
  .btn:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(255,90,60,.42);}
  .btn.ghost{background:transparent;color:#fff;box-shadow:none;border:1px solid rgba(255,255,255,.28);}
  .btn.dark{background:var(--ink);box-shadow:0 8px 24px rgba(21,19,29,.25);}
  .eyebrow{font-size:12.5px;letter-spacing:.22em;text-transform:uppercase;font-weight:600;color:var(--accent);}

  /* nav */
  nav{position:sticky;top:0;z-index:50;background:rgba(21,19,29,.85);backdrop-filter:blur(12px);}
  nav .row{display:flex;align-items:center;justify-content:space-between;padding:16px 26px;max-width:1120px;margin:0 auto;}
  nav .logo{font-family:var(--disp);font-weight:800;font-size:21px;color:#fff;letter-spacing:-.02em;}
  nav .logo span{color:var(--accent);}
  nav .nl{display:flex;gap:30px;align-items:center;}
  nav .nl a{color:rgba(255,255,255,.72);font-size:14.5px;font-weight:500;}
  nav .nl a:hover{color:#fff;}
  nav .btn{padding:10px 20px;font-size:14px;}
  @media(max-width:760px){
    nav .nl a:not(.pay-link){display:none;}
    nav .row{padding:14px 16px;}
    nav .nl a.pay-link{font-size:13.5px;}
  }

  /* hero */
  .hero{background:var(--ink);color:#fff;position:relative;overflow:hidden;padding:88px 0 96px;}
  .hero::before{content:"";position:absolute;inset:0;pointer-events:none;
    background:radial-gradient(60% 50% at 78% 8%,rgba(124,92,255,.42),transparent 60%),
               radial-gradient(50% 45% at 12% 95%,rgba(255,90,60,.36),transparent 60%);}
  .hero .wrap{position:relative;display:grid;grid-template-columns:1.15fr .85fr;gap:54px;align-items:center;}
  .hero h1{font-size:clamp(40px,6vw,68px);margin:18px 0 22px;}
  .hero p.sub{font-size:19px;color:rgba(255,255,255,.78);max-width:520px;margin-bottom:32px;}
  .hero .cta{display:flex;gap:14px;flex-wrap:wrap;}
  .hero .pill{display:inline-flex;align-items:center;gap:9px;background:rgba(255,255,255,.08);
    border:1px solid rgba(255,255,255,.14);padding:7px 15px;border-radius:100px;font-size:13px;color:rgba(255,255,255,.85);}
  .dot{width:8px;height:8px;border-radius:50%;background:#36e07f;box-shadow:0 0 0 0 rgba(54,224,127,.6);animation:pulse 2s infinite;}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(54,224,127,.55);}70%{box-shadow:0 0 0 9px rgba(54,224,127,0);}100%{box-shadow:0 0 0 0 rgba(54,224,127,0);}}
  .rotor{display:inline-block;color:var(--accent);}

  /* hero chat preview */
  .preview{background:#fff;border-radius:20px;padding:16px;box-shadow:0 30px 70px rgba(0,0,0,.4);color:var(--ink);}
  .preview .ph{display:flex;align-items:center;gap:10px;padding:6px 6px 12px;border-bottom:1px solid var(--line);}
  .preview .av{width:34px;height:34px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent2));}
  .preview .ph b{font-family:var(--disp);font-size:15px;}
  .preview .ph small{color:var(--muted);font-size:12px;display:block;}
  .pm{margin:10px 4px;padding:10px 14px;border-radius:14px;font-size:14px;max-width:86%;}
  .pm.b{background:#f1ece3;}
  .pm.u{background:var(--ink);color:#fff;margin-left:auto;}

  /* marquee */
  .marquee{background:var(--ink2);color:rgba(255,255,255,.6);padding:15px 0;overflow:hidden;white-space:nowrap;}
  .marquee .track{display:inline-block;animation:scroll 26s linear infinite;font-family:var(--disp);font-weight:500;font-size:15px;letter-spacing:.02em;}
  .marquee .track span{margin:0 26px;}
  .marquee .track span::before{content:"✦";color:var(--accent);margin-right:26px;}
  @keyframes scroll{from{transform:translateX(0);}to{transform:translateX(-50%);}}

  /* sections */
  section{padding:96px 0;}
  .head{max-width:660px;margin:0 auto 56px;text-align:center;}
  .head h2{font-size:clamp(30px,4.4vw,46px);margin-top:12px;}
  .head p{color:var(--muted);font-size:18px;margin-top:14px;}

  .reveal{opacity:0;transform:translateY(22px);transition:opacity .6s ease,transform .6s ease;}
  .reveal.in{opacity:1;transform:none;}

  /* calculator */
  .calc{background:var(--ink);color:#fff;border-radius:26px;padding:46px;display:grid;grid-template-columns:1fr 1fr;gap:46px;align-items:center;position:relative;overflow:hidden;}
  .calc::before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(50% 60% at 90% 10%,rgba(124,92,255,.3),transparent 60%);}
  .calc .l,.calc .r{position:relative;}
  .calc h3{font-family:var(--disp);font-weight:800;font-size:28px;margin-bottom:8px;}
  .calc .field{margin:22px 0;}
  .calc label{display:flex;justify-content:space-between;font-size:14px;color:rgba(255,255,255,.8);margin-bottom:10px;}
  .calc label b{color:#fff;font-family:var(--disp);}
  .calc input[type=range]{width:100%;-webkit-appearance:none;height:6px;border-radius:6px;background:rgba(255,255,255,.16);outline:none;}
  .calc input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;border-radius:50%;background:var(--accent);cursor:pointer;box-shadow:0 4px 12px rgba(255,90,60,.5);}
  .calc .result{text-align:center;}
  .calc .big{font-family:var(--disp);font-weight:800;font-size:clamp(44px,7vw,70px);color:var(--accent);line-height:1;}
  .calc .result p{color:rgba(255,255,255,.78);margin-top:14px;font-size:15px;}
  @media(max-width:760px){.calc{grid-template-columns:1fr;padding:30px;}}

  /* steps */
  .steps{display:grid;grid-template-columns:repeat(3,1fr);gap:26px;counter-reset:s;}
  .step{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:34px 28px;}
  .step .n{counter-increment:s;font-family:var(--disp);font-weight:800;font-size:15px;width:42px;height:42px;border-radius:12px;
    background:var(--ink);color:#fff;display:flex;align-items:center;justify-content:center;margin-bottom:18px;}
  .step .n::before{content:"0" counter(s);}
  .step h3{font-family:var(--disp);font-size:21px;margin-bottom:8px;}
  .step p{color:var(--muted);font-size:15px;}
  @media(max-width:760px){.steps{grid-template-columns:1fr;}}

  /* features */
  .feat{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;}
  .fc{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:30px 26px;transition:transform .18s ease,border-color .18s ease;}
  .fc:hover{transform:translateY(-4px);border-color:var(--accent);}
  .fc .ic{font-size:24px;margin-bottom:14px;}
  .fc h3{font-family:var(--disp);font-size:19px;margin-bottom:7px;}
  .fc p{color:var(--muted);font-size:14.5px;}
  @media(max-width:760px){.feat{grid-template-columns:1fr;}}

  /* pricing */
  .pricing{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;align-items:stretch;}
  .plan{background:var(--surface);border:1px solid var(--line);border-radius:22px;padding:34px 30px;display:flex;flex-direction:column;}
  .plan.pop{background:var(--ink);color:#fff;border:none;transform:scale(1.04);box-shadow:0 24px 60px rgba(21,19,29,.3);}
  .plan .tag{font-size:12px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);}
  .plan .pop-tag{display:inline-block;background:var(--accent);color:#fff;font-size:11px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;padding:4px 11px;border-radius:100px;margin-bottom:14px;width:fit-content;}
  .plan .price{font-family:var(--disp);font-weight:800;font-size:46px;margin:14px 0 4px;}
  .plan .price span{font-size:16px;font-weight:500;color:var(--muted);}
  .plan.pop .price span{color:rgba(255,255,255,.6);}
  .plan ul{list-style:none;margin:22px 0;flex:1;}
  .plan li{padding:8px 0;font-size:14.5px;display:flex;gap:10px;}
  .plan li::before{content:"→";color:var(--accent);font-weight:700;}
  .plan .btn{justify-content:center;}
  @media(max-width:880px){.pricing{grid-template-columns:1fr;}.plan.pop{transform:none;}}

  /* faq */
  .faq{max-width:760px;margin:0 auto;}
  details{border-bottom:1px solid var(--line);padding:20px 0;}
  details summary{font-family:var(--disp);font-size:18px;cursor:pointer;list-style:none;display:flex;justify-content:space-between;}
  details summary::-webkit-details-marker{display:none;}
  details summary::after{content:"+";color:var(--accent);font-weight:700;}
  details[open] summary::after{content:"–";}
  details p{color:var(--muted);margin-top:12px;font-size:15px;}

  /* cta band */
  .cta-band{background:var(--ink);color:#fff;border-radius:28px;padding:60px;text-align:center;position:relative;overflow:hidden;}
  .cta-band::before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(60% 80% at 50% 120%,rgba(255,90,60,.4),transparent 60%);}
  .cta-band h2{font-size:clamp(30px,4.4vw,44px);position:relative;}
  .cta-band p{color:rgba(255,255,255,.78);margin:16px 0 28px;position:relative;}
  .cta-band .links{position:relative;margin-top:26px;font-size:15px;color:rgba(255,255,255,.7);}
  .cta-band .links a{color:#fff;}

  footer{padding:48px 26px;text-align:center;color:var(--muted);font-size:14px;}

  /* chat widget */
  #bub{position:fixed;bottom:22px;right:22px;width:64px;height:64px;border-radius:50%;
    background:var(--accent);display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:9999;
    box-shadow:0 10px 28px rgba(255,90,60,.45);transition:transform .15s;}
  #bub:hover{transform:scale(1.07);}
  #chat{position:fixed;bottom:100px;right:22px;width:380px;height:560px;background:#fff;border-radius:20px;
    box-shadow:0 24px 70px rgba(0,0,0,.35);display:none;flex-direction:column;overflow:hidden;z-index:9999;}
  #chat.open{display:flex;}
  #ch{background:var(--ink);color:#fff;padding:18px 20px;display:flex;justify-content:space-between;align-items:center;}
  #ch .t{font-family:var(--disp);font-weight:700;font-size:18px;}
  #ch small{color:rgba(255,255,255,.65);font-size:12px;}
  #ch .x{cursor:pointer;font-size:22px;opacity:.8;}
  #msgs{flex:1;overflow-y:auto;padding:18px;background:#faf7f2;}
  .m{margin:8px 0;padding:11px 15px;border-radius:15px;font-size:14.5px;max-width:84%;line-height:1.45;}
  .m.u{background:var(--ink);color:#fff;margin-left:auto;}
  .m.b{background:#ece6dc;color:#2a2622;}
  #irow{display:flex;gap:8px;padding:12px;border-top:1px solid var(--line);}
  #inp{flex:1;padding:12px 15px;border:1px solid var(--line);border-radius:100px;font-size:14.5px;outline:none;}
  #inp:focus{border-color:var(--accent);}
  #snd{border:none;background:var(--accent);color:#fff;width:44px;height:44px;border-radius:50%;cursor:pointer;font-size:17px;flex-shrink:0;}
  @media(max-width:600px){
    #chat{inset:0;width:100vw;height:100vh;height:100dvh;max-height:100dvh;border-radius:0;bottom:0;right:0;}
    #ch{padding-top:max(18px,env(safe-area-inset-top));}
    #msgs{min-height:0;-webkit-overflow-scrolling:touch;}
    #irow{padding:10px;padding-bottom:max(10px,env(safe-area-inset-bottom));}
    #inp{font-size:16px;}            /* 16px stops iOS auto-zooming on focus */
    #bub{bottom:18px;right:18px;}
  }

  /* mobile polish */
  @media(max-width:760px){
    body{overflow-x:hidden;}
    .wrap{padding:0 20px;}
    section{padding:56px 0;}
    .hero{padding:48px 0 58px;}
    .hero .wrap{grid-template-columns:1fr;gap:32px;}
    .hero h1{font-size:clamp(34px,10vw,50px);}
    .hero p.sub{font-size:16.5px;}
    .hero .cta{width:100%;}
    .hero .cta .btn{flex:1 1 100%;justify-content:center;}
    .head{margin-bottom:36px;}
    .head h2{font-size:clamp(27px,7.5vw,38px);}
    .head p{font-size:16px;}
    .calc{padding:26px;border-radius:20px;}
    .calc h3{font-size:23px;}
    .calc .big{font-size:clamp(40px,13vw,58px);}
    .cta-band{padding:40px 24px;border-radius:22px;}
    .plan.pop{transform:none;}
    nav .row{padding:14px 20px;}
    nav .logo{font-size:19px;}
  }

  /* proof / case study */
  .casegrid{display:grid;grid-template-columns:1.1fr .9fr;gap:24px;}
  .case{background:var(--surface);border:1px solid var(--line);border-radius:22px;padding:38px 34px;}
  .case.soft{background:var(--ink);color:#fff;display:flex;flex-direction:column;justify-content:center;position:relative;overflow:hidden;}
  .case.soft::before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(60% 70% at 100% 0,rgba(124,92,255,.32),transparent 60%);}
  .case-tag{font-size:12px;letter-spacing:.16em;text-transform:uppercase;font-weight:600;color:var(--accent);margin-bottom:12px;position:relative;}
  .case h3{font-family:var(--disp);font-size:28px;margin-bottom:12px;}
  .case p{color:var(--muted);font-size:15.5px;}
  .case.soft p{color:rgba(255,255,255,.82);position:relative;}
  .case-link{display:inline-block;margin-top:18px;color:var(--accent);font-weight:600;}
  .big-quote{font-family:var(--disp);font-weight:700;font-size:23px;line-height:1.32;color:#fff;}
  .muted-note{margin-top:16px;font-size:13px;color:rgba(255,255,255,.55);}
  @media(max-width:760px){.casegrid{grid-template-columns:1fr;}.case{padding:28px 24px;}}
</style>
</head>
<body>

<nav><div class="row">
  <div class="logo">Front<span>desk</span></div>
  <div class="nl">
    <a href="#how">How it works</a>
    <a href="#pricing">Pricing</a>
    <a href="#website">Websites</a>
    <a href="#faq">FAQ</a>
    <a class="pay-link" href="/pay">Existing customers</a>
  </div>
</div></nav>

<header class="hero"><div class="wrap">
  <div>
    <div class="pill"><span class="dot"></span> Live assistant — talk to it on this page</div>
    <h1 class="disp">Never miss another <span class="rotor">customer</span>.</h1>
    <p class="sub">I build smart chat assistants for local businesses. They answer your customers instantly, ask the right questions, and drop every qualified lead straight into your inbox — 24/7, even when you're on the tools or fully booked.</p>
    <div class="cta">
      <button class="btn" onclick="openChat()">Try the assistant</button>
    </div>
  </div>
  <div class="preview reveal">
    <div class="ph"><div class="av"></div><div><b>Customer enquiry</b><small>Today, 11:48pm</small></div></div>
    <div class="pm b">Hi! Do you do balayage and how much roughly?</div>
    <div class="pm u">We do — from £160. Want me to grab your details so we can book you in?</div>
    <div class="pm b">Yes please, I'm Mia 07700…</div>
    <div class="pm u" style="background:var(--accent);">Lead sent to owner's inbox ✓</div>
  </div>
</div></header>

<div class="marquee"><div class="track" id="track"></div></div>

<section id="problem"><div class="wrap">
  <div class="head reveal">
    <div class="eyebrow">The quiet money-leak</div>
    <h2 class="disp">You're losing work you never even hear about</h2>
    <p>Most enquiries come in after hours or when you're flat out. If no one replies fast, they move to the next business on Google. Your assistant catches every one — instantly.</p>
  </div>
  <div class="calc reveal">
    <div class="l">
      <h3>What's it costing you?</h3>
      <div class="field"><label>Enquiries you get a week <b id="ev">25</b></label>
        <input type="range" id="enq" min="5" max="100" value="25"></div>
      <div class="field"><label>Roughly how many slip through <b id="mv">8</b></label>
        <input type="range" id="miss" min="0" max="60" value="8"></div>
      <div class="field"><label>Average value of a job <b id="jv">£120</b></label>
        <input type="range" id="job" min="20" max="2000" step="10" value="120"></div>
    </div>
    <div class="r result">
      <div class="big" id="lost">£4,160</div>
      <p>potentially slipping away every month. The assistant catches those — website, assistant and all — for <b style="color:#fff">£30/month</b> (plus a one-off £45 setup).</p>
      <button class="btn" style="margin-top:18px" onclick="openChat()">Stop the leak →</button>
    </div>
  </div>
</div></section>

<section id="how" style="background:#fff;"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">Dead simple</div><h2 class="disp">Live in days, not months</h2></div>
  <div class="steps">
    <div class="step reveal"><div class="n"></div><h3>Tell me about your business</h3><p>A quick chat about what you do, your services and your prices. That's your bit done.</p></div>
    <div class="step reveal"><div class="n"></div><h3>I build &amp; install it</h3><p>I train your assistant on your business and add it to your site — usually within a few days.</p></div>
    <div class="step reveal"><div class="n"></div><h3>Leads land in your inbox</h3><p>Tidy, qualified leads arrive by email, ready for you to call back. No app, no faff.</p></div>
  </div>
</div></section>

<section id="features"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">Why it's different</div><h2 class="disp">Not a clunky chatbot. A proper front-of-house.</h2></div>
  <div class="feat">
    <div class="fc reveal"><div class="ic">🌙</div><h3>Always on</h3><p>Answers customers at 2am, on Sundays, and while you're fully booked — never a missed message.</p></div>
    <div class="fc reveal"><div class="ic">🎯</div><h3>Asks smart questions</h3><p>It qualifies people — what they want, budget, timing — so you're not chasing time-wasters.</p></div>
    <div class="fc reveal"><div class="ic">📥</div><h3>Clean leads, not chat logs</h3><p>Every enquiry arrives tidied into a neat summary with their details up top. Just call them back.</p></div>
    <div class="fc reveal"><div class="ic">🎨</div><h3>Trained &amp; branded for you</h3><p>It knows your services and prices, and is styled to match your business — feels like yours.</p></div>
    <div class="fc reveal"><div class="ic">🤝</div><h3>Honest by design</h3><p>It captures enquiries and lets you confirm — it never promises a slot it can't actually see.</p></div>
    <div class="fc reveal"><div class="ic">⚡</div><h3>No tech headache</h3><p>I set the whole thing up for you. You don't touch a line of code — you just get the leads.</p></div>
  </div>
</div></section>

<section id="proof"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">Proof it works</div><h2 class="disp">Already live on a real business</h2><p>Frontdesk isn't a mock-up — here's one working on a paying client's website right now.</p></div>
  <div class="casegrid">
    <div class="case reveal">
      <div class="case-tag">Live client · Portsmouth</div>
      <h3>AU Decorating</h3>
      <p>A painting &amp; decorating firm with a 10/10 Checkatrade rating. Their Frontdesk assistant answers enquiries 24/7, asks the right questions, and drops qualified leads — with the customer's job photos — straight into the owner's inbox.</p>
      <a class="case-link" href="https://www.au-decorating.com" target="_blank">See the assistant live on their site →</a>
    </div>
    <div class="case soft reveal">
      <div class="case-tag">The result</div>
      <p class="big-quote">Enquiries that used to arrive after hours now get answered instantly and land as tidy, ready-to-call leads — nothing slips through.</p>
      <p class="muted-note">More client stories coming as Frontdesk grows.</p>
    </div>
  </div>
</div></section>

<section id="pricing" style="background:#fff;"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">Simple pricing</div><h2 class="disp">One plan. Everything included.</h2><p>A one-off setup fee, then one low monthly price that covers the lot — no tiers, no surprises.</p></div>
  <div class="pricing" style="grid-template-columns:1fr;max-width:460px;margin:0 auto;">
    <div class="plan pop reveal"><span class="pop-tag">Everything included</span><div class="tag">Complete</div><div class="price">£30<span>/mo</span></div>
      <div style="color:#667;margin:-6px 0 4px;font-weight:600;">+ £45 one-off setup</div>
      <ul><li>Your own custom website</li><li>AI assistant trained on your services &amp; prices</li><li>Smart qualifying questions</li><li>Every lead straight to your inbox</li><li>Hosting included</li><li>I keep it updated for you</li></ul>
      <button class="btn" onclick="openChat()">Get started</button></div>
  </div>
  <p class="reveal" style="text-align:center;margin-top:18px;color:#667;">We have a quick chat and I build it for you first — you only set up payment once you're happy.</p>
</div></section>

<section id="website"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">No website? No problem</div><h2 class="disp">Need a website too? I can build that.</h2><p>The assistant can live on a simple page I make for you — or I can build you a proper website to put it on. A paid add-on, quoted to suit what you need.</p></div>
  <div class="feat">
    <div class="fc reveal"><div class="ic">✍️</div><h3>One page or full site</h3><p>From a smart single landing page to a few pages with a gallery — whatever fits your business.</p></div>
    <div class="fc reveal"><div class="ic">🎨</div><h3>Designed around you</h3><p>Clean, modern and branded to your trade — not a tired template.</p></div>
    <div class="fc reveal"><div class="ic">🤖</div><h3>Assistant built in</h3><p>Your Frontdesk assistant comes ready-installed, catching leads from day one.</p></div>
  </div>
  <div style="text-align:center;margin-top:36px;">
    <a class="btn dark" href="https://www.au-decorating.com" target="_blank">See a site I built →</a>
    <button class="btn" onclick="openChat()" style="margin-left:10px;">Ask about a website</button>
  </div>
</div></section>

<section id="faq"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">Good questions</div><h2 class="disp">The bits people ask</h2></div>
  <div class="faq reveal">
    <details><summary>Do I need a website already?</summary><p>Not necessarily — it can live on a simple page I set up, or link straight from your Google profile. If you've got a site, even better, it slots right in. And if you'd like a proper website of your own, I can build you one too as a paid add-on, quoted to suit what you need.</p></details>
    <details><summary>Will it book appointments into my calendar?</summary><p>It captures the enquiry — name, what they want, preferred timing and contact — and sends it to you to confirm. It won't promise a slot it can't see, which keeps customers happy. Full calendar booking is available as a custom add-on.</p></details>
    <details><summary>How long until it's live?</summary><p>Usually within a few days of our first chat — most businesses are up and running inside a week. Here's how it goes: we have a quick chat about your services, prices and the questions your customers tend to ask; I build and train your assistant on all of it; then I send you a private link to try it yourself and tell me anything you'd like changed. Once you're happy, I add it to your site (or a page I set up for you) and the leads start landing in your inbox. You're kept in the loop the whole way, and the setup is entirely on me — no tech work on your side.</p></details>
    <details><summary>What if I want to cancel?</summary><p>No lock-in — cancel anytime. While you're subscribed your website and assistant stay live and I keep them updated; if you stop, they're simply paused.</p></details>
    <details><summary>How much does it cost?</summary><p>A one-off £45 setup fee, then £30/month — everything included: your custom website, the AI assistant trained on your business, hosting, and ongoing updates. No tiers. Bigger or multi-location needs are quoted to suit.</p></details>
    <details><summary>What kind of businesses is it for?</summary><p>Local service businesses — hairdressers, barbers, builders, plumbers, electricians, decorators, dog groomers, beauticians, garages, cleaners and more. If customers message you with questions and you can't always reply straight away, it'll pay for itself.</p></details>
    <details><summary>Where do my leads go — is my data safe?</summary><p>Every enquiry lands straight in your email inbox, tidied into a clean summary. Your details and your customers' details are only ever used to follow up on enquiries — never sold or used for advertising.</p></details>
    <details><summary>Do I need to be techy?</summary><p>Not at all. I set the whole thing up for you — there's nothing to install and not a line of code to touch. You just get the leads.</p></details>
  </div>
</div></section>

<section><div class="wrap"><div class="cta-band reveal">
  <h2 class="disp">Let's catch the customers you're missing</h2>
  <p>Chat to the assistant on this page (yes, it's one of ours) or reach Andra directly.</p>
  <button class="btn" onclick="openChat()">Try the assistant</button>
  <div class="links">Want to see one on a real business? <a href="https://www.au-decorating.com" target="_blank" style="color:#ff8a6f;font-weight:600;">See it live on a client's site →</a></div>
  <div class="links">Or reach me: <a href="tel:__PHONE_TEL__">__PHONE__</a> · <a href="mailto:__EMAIL__">__EMAIL__</a></div>
</div></div></section>

<footer>© <span id="yr"></span> Frontdesk · AI assistants for local businesses · Portsmouth · by __NAME__ · <a href="/privacy" style="color:#7c5cff;">Privacy Policy</a> · <a href="/terms" style="color:#7c5cff;">Terms</a></footer>

<div id="bub" onclick="toggleChat()">
  <svg width="30" height="30" viewBox="0 0 24 24" fill="none"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
</div>
<div id="chat">
  <div id="ch"><div><div class="t">Frontdesk Assistant</div><small>Ask anything · usually replies instantly</small></div><div class="x" onclick="toggleChat()">&times;</div></div>
  <div id="msgs"></div>
  <div id="irow"><input id="inp" placeholder="Type a message…" onkeypress="if(event.key==='Enter')send()"><button id="snd" onclick="send()">➤</button></div>
</div>

<script>
  document.getElementById('yr').textContent = new Date().getFullYear();

  // rotating hero word
  var words=["customer","enquiry","booking","lead","quote"],wi=0;
  setInterval(function(){wi=(wi+1)%words.length;var r=document.querySelector('.rotor');r.style.opacity=0;setTimeout(function(){r.textContent=words[wi];r.style.opacity=1;},200);},2200);
  document.querySelector('.rotor').style.transition='opacity .2s';

  // marquee
  var trades=["Hairdressers","Barbers","Builders","Plumbers","Electricians","Decorators","Dog groomers","Beauticians","Garages","Cleaners","Landscapers","Tattoo studios"];
  var t=document.getElementById('track');var html=trades.map(function(x){return '<span>'+x+'</span>';}).join('');t.innerHTML=html+html;

  // reveal on scroll
  var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting)e.target.classList.add('in');});},{threshold:.12});
  document.querySelectorAll('.reveal').forEach(function(el){io.observe(el);});

  // calculator
  function money(n){return '£'+Math.round(n).toLocaleString();}
  function calc(){
    var enq=+document.getElementById('enq').value;
    var miss=Math.min(+document.getElementById('miss').value,enq);
    var job=+document.getElementById('job').value;
    document.getElementById('miss').max=enq;
    document.getElementById('ev').textContent=enq;
    document.getElementById('mv').textContent=miss;
    document.getElementById('jv').textContent=money(job);
    document.getElementById('lost').textContent=money(miss*job*4.33);
  }
  ['enq','miss','job'].forEach(function(id){document.getElementById(id).addEventListener('input',calc);});
  calc();

  // chat
  var started=false;
  var STRIPE_URL="__STRIPE__";
  function subscribe(){ if(STRIPE_URL){ window.open(STRIPE_URL,'_blank'); } else { openChat(); } }
  function isPhone(){return window.matchMedia('(max-width:600px)').matches;}
  function openChat(){var c=document.getElementById('chat');c.classList.add('open');document.body.style.overflow='hidden';if(!started){started=true;add("Hey! I'm the Frontdesk assistant — and a live demo of exactly what we'd build for you. Tell me a bit about your business and I'll show you how it could work. What do you do?",'b');}if(!isPhone())document.getElementById('inp').focus();}
  function toggleChat(){var c=document.getElementById('chat');if(c.classList.contains('open')){c.classList.remove('open');document.body.style.overflow='';}else{openChat();}}
  function add(txt,who){var m=document.getElementById('msgs');var d=document.createElement('div');d.className='m '+who;d.textContent=txt;m.appendChild(d);m.scrollTop=m.scrollHeight;}
  async function send(){var i=document.getElementById('inp');var v=i.value.trim();if(!v)return;add(v,'u');i.value='';
    try{var r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:v}),credentials:'same-origin'});
      var d=await r.json();add(d.reply,'b');}catch(e){add("Sorry — something glitched. You can also reach Andra directly at __EMAIL__.",'b');}}
</script>
</body>
</html>
"""

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
<meta name="theme-color" content="#15131d">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2315131d'/%3E%3Ctext x='16' y='23' font-family='Arial,sans-serif' font-size='20' font-weight='bold' fill='%23ff5a3c' text-anchor='middle'%3EF%3C/text%3E%3C/svg%3E">
<style>
  body{{margin:0;background:#f7f3ec;color:#15131d;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.7;}}
  .bar{{background:#15131d;padding:20px 26px;}}
  .bar a{{color:#fff;text-decoration:none;font-weight:800;font-size:20px;letter-spacing:-.02em;}}
  .bar a span{{color:#ff5a3c;}}
  .wrap{{max-width:740px;margin:0 auto;padding:48px 26px 80px;}}
  h1{{font-size:34px;letter-spacing:-.02em;margin:0 0 6px;}}
  .lead{{color:#6c6760;margin:0 0 30px;}}
  h2{{font-size:19px;margin:34px 0 8px;}}
  a{{color:#d6452a;}}
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
<meta name="theme-color" content="#15131d">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2315131d'/%3E%3Ctext x='16' y='23' font-family='Arial,sans-serif' font-size='20' font-weight='bold' fill='%23ff5a3c' text-anchor='middle'%3EF%3C/text%3E%3C/svg%3E">
<style>
  body{{margin:0;background:#f7f3ec;color:#15131d;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.7;}}
  .bar{{background:#15131d;padding:20px 26px;}}
  .bar a{{color:#fff;text-decoration:none;font-weight:800;font-size:20px;letter-spacing:-.02em;}}
  .bar a span{{color:#ff5a3c;}}
  .wrap{{max-width:560px;margin:0 auto;padding:48px 26px 80px;}}
  h1{{font-size:32px;letter-spacing:-.02em;margin:0 0 6px;}}
  .lead{{color:#6c6760;margin:0 0 30px;}}
  .card{{background:#fff;border:1px solid #e4ded2;border-radius:18px;padding:30px;box-shadow:0 10px 30px rgba(0,0,0,.05);}}
  .price{{font-size:40px;font-weight:800;letter-spacing:-.02em;}}
  .price span{{font-size:17px;font-weight:600;color:#6c6760;}}
  ul{{padding-left:20px;margin:18px 0 26px;}}
  li{{margin:7px 0;}}
  .pay-btn{{display:block;text-align:center;background:#6c4cff;color:#fff;text-decoration:none;font-weight:700;font-size:17px;padding:16px;border-radius:12px;}}
  .pay-btn:hover{{background:#5a3ce0;}}
  .note{{color:#6c6760;font-size:14px;margin-top:22px;text-align:center;}}
  .note a{{color:#6c4cff;}}
  .foot{{color:#a59e92;font-size:13px;margin-top:40px;text-align:center;}}
  .foot a{{color:#a59e92;}}
</style></head><body>
<div class="bar"><a href="/">Front<span>desk</span></a></div>
<div class="wrap">
  <h1>Customer payments</h1>
  <p class="lead">Already set up with {BRAND}? Start your plan below — it takes a minute and renews automatically each month.</p>
  <div class="card">
    <div class="price">£30<span>/month</span></div>
    <div style="color:#6c6760;font-weight:600;margin:-4px 0 2px;">+ £45 one-off setup</div>
    <div style="color:#a59e92;font-size:14px;margin-bottom:6px;">£75 due today, then £30/month</div>
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
<meta name="theme-color" content="#15131d">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%2315131d'/%3E%3Ctext x='16' y='23' font-family='Arial,sans-serif' font-size='20' font-weight='bold' fill='%23ff5a3c' text-anchor='middle'%3EF%3C/text%3E%3C/svg%3E">
<style>
  body{{margin:0;background:#f7f3ec;color:#15131d;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.7;}}
  .bar{{background:#15131d;padding:20px 26px;}}
  .bar a{{color:#fff;text-decoration:none;font-weight:800;font-size:20px;letter-spacing:-.02em;}}
  .bar a span{{color:#ff5a3c;}}
  .wrap{{max-width:740px;margin:0 auto;padding:48px 26px 80px;}}
  h1{{font-size:34px;letter-spacing:-.02em;margin:0 0 6px;}}
  .lead{{color:#6c6760;margin:0 0 30px;}}
  h2{{font-size:19px;margin:34px 0 8px;}}
  a{{color:#d6452a;}}
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
  <p>The service is a one-off setup fee of &pound;45 plus &pound;30 per month. Payments are taken securely through Stripe; your first payment is the setup fee plus your first month, and the &pound;30 monthly fee then recurs automatically until you cancel. Prices may change in future, but we&rsquo;ll always give you notice before any change affects you.</p>

  <h2>3. The setup fee</h2>
  <p>The &pound;45 setup fee covers the work of building and setting up your site and assistant. Because that work is done upfront, the setup fee is non-refundable once we&rsquo;ve started building.</p>

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


# --- TEMPORARY email debug route. Delete once leads are arriving. ---
# /_debug/email?key=fdtest          -> show config
# /_debug/email?key=fdtest&send=1   -> fire a real test email
@app.route("/_debug/email")
def debug_email():
    if request.args.get("key") != os.environ.get("DEBUG_KEY", "fdtest"):
        return Response("not found", status=404)
    info = {
        "resend_key_set": bool(RESEND_API_KEY),
        "resend_key_tail": ("..." + RESEND_API_KEY[-4:]) if RESEND_API_KEY else None,
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
    sid = session.get("sid") or str(uuid.uuid4())
    session["sid"] = sid
    if sid not in conversations:
        conversations[sid] = [{"role": "system", "content": SYSTEM_PROMPT}]
    convo = conversations[sid]
    user_msg = request.json.get("message", "")
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
    # detail appears. Closing-phrase / long-chat are safety nets so a lead is
    # never lost. Sent at most once per visitor.
    if sid not in notified and has_contact_info(convo):
        if lead_ready or _looks_like_closing(user_msg) or len(convo) >= 8:
            notified.add(sid)
            threading.Thread(target=send_lead_email, args=(list(convo),), daemon=True).start()

    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5002)))
