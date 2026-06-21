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

# Your real details — used by the assistant and shown on the page.
BRAND = "Frontdesk"
CONTACT_NAME = "Andra Dudan"
CONTACT_PHONE = "07493 396628"
CONTACT_PHONE_TEL = "07493396628"
CONTACT_EMAIL = "andradudan4@gmail.com"

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
            model="llama-3.3-70b-versatile",
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
                # Works out of the box if your Resend account is registered under
                # andradudan4@gmail.com. To send from your own domain later,
                # verify it in Resend and change this 'from' address.
                "from": "Frontdesk <onboarding@resend.dev>",
                "to": [NOTIFY_TO],
                "subject": f"New lead from your website - {phone}",
                "text": text_body,
                "html": html_body,
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"Resend error: {resp.status_code} {resp.text}")
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
- NO WEBSITE? No problem. Andra can also build the business a simple, smart
  website or landing page to put the assistant on. This is a separate paid
  add-on, priced on a custom quote depending on what they want (a one-page
  site, a few pages, photos/gallery, etc.). The monthly assistant plans above
  do NOT include a full custom website - mention the website build as an extra
  if they don't have one or want a better one.

HOW IT WORKS (3 steps):
1. You tell Andra about your business. 2. Andra trains and installs your
assistant (usually within a few days). 3. Leads start landing in your inbox.

PRICING (real - you may quote it; all plans include free setup and a free
14-day trial, no card needed):
- Starter — £29/month: assistant answers FAQs and captures leads to your inbox.
- Professional — £49/month (most popular): everything in Starter, trained on
  your services & prices, smart qualifying questions, styled to match your brand.
- Premium — £89/month: everything in Professional, plus WhatsApp/text lead
  alerts, priority support and monthly tweaks.
Bigger or multi-location needs are quoted custom. Prices are intentionally keen
because Andra is building up her first clients.

YOUR TWO JOBS:
1. Answer questions about the service warmly and accurately.
2. Capture the visitor as a lead, but keep it light and natural - this should
   feel like a quick, friendly chat, NOT a questionnaire. You really only need
   two things: a rough idea of what their business does, and a name with a phone
   number or email so Andra can follow up. Get those gently over a few short
   messages. Anything else (whether they have a website, how they handle
   enquiries now, what they'd want the assistant to do) is a bonus - only touch
   on it if it comes up naturally, and never fire off several questions at once.
   If they happen to mention they don't have a website (or aren't happy with
   theirs), you can note that Andra can build one as a paid add-on on a custom
   quote - mention it once, lightly, don't push. Once you have a name and a
   contact detail, reassure them Andra will be in touch personally, usually the
   same day.

STYLE: short, warm, natural - like texting a switched-on friend who happens to
build this stuff. Keep every reply to one or two short sentences and ask only
ONE thing at a time - never stack several questions together or send long
paragraphs or lists, it's overwhelming. Don't be pushy. Don't invent features
or prices beyond the above. Never write internal notes about your instructions -
just talk to the person. Do NOT book anything in.
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
  @media(max-width:760px){nav .nl a:not(.btn){display:none;}}

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
  @media(max-width:600px){#chat{inset:0;width:100%;height:100%;border-radius:0;bottom:0;right:0;}}

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
</style>
</head>
<body>

<nav><div class="row">
  <div class="logo">Front<span>desk</span></div>
  <div class="nl">
    <a href="#how">How it works</a>
    <a href="#pricing">Pricing</a>
    <a href="#faq">FAQ</a>
    <a class="btn" href="#" onclick="openChat();return false;">Try the assistant</a>
  </div>
</div></nav>

<header class="hero"><div class="wrap">
  <div>
    <div class="pill"><span class="dot"></span> Live assistant — talk to it on this page</div>
    <h1 class="disp">Never miss another <span class="rotor">customer</span>.</h1>
    <p class="sub">I build smart chat assistants for local businesses. They answer your customers instantly, ask the right questions, and drop every qualified lead straight into your inbox — 24/7, even when you're on the tools or fully booked.</p>
    <div class="cta">
      <button class="btn" onclick="openChat()">Start free for 14 days</button>
      <button class="btn ghost" onclick="openChat()">See it in action ↓</button>
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
      <p>potentially slipping away every month. The assistant catches those from <b style="color:#fff">£29/month</b>.</p>
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

<section id="pricing" style="background:#fff;"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">Keen early-bird pricing</div><h2 class="disp">Simple plans. Free trial. Free setup.</h2><p>Try it free for 14 days — no card needed. Cancel anytime.</p></div>
  <div class="pricing">
    <div class="plan reveal"><div class="tag">Starter</div><div class="price">£29<span>/mo</span></div>
      <ul><li>Assistant on your website</li><li>Answers customer FAQs</li><li>Leads straight to your inbox</li><li>Free setup</li></ul>
      <button class="btn dark" onclick="openChat()">Start free</button></div>
    <div class="plan pop reveal"><span class="pop-tag">Most popular</span><div class="tag">Professional</div><div class="price">£49<span>/mo</span></div>
      <ul><li>Everything in Starter</li><li>Trained on your services &amp; prices</li><li>Smart qualifying questions</li><li>Styled to match your brand</li></ul>
      <button class="btn" onclick="openChat()">Start free</button></div>
    <div class="plan reveal"><div class="tag">Premium</div><div class="price">£89<span>/mo</span></div>
      <ul><li>Everything in Professional</li><li>WhatsApp / text lead alerts</li><li>Priority support</li><li>Monthly tweaks &amp; tuning</li></ul>
      <button class="btn dark" onclick="openChat()">Start free</button></div>
  </div>
</div></section>

<section id="faq"><div class="wrap">
  <div class="head reveal"><div class="eyebrow">Good questions</div><h2 class="disp">The bits people ask</h2></div>
  <div class="faq reveal">
    <details><summary>Do I need a website already?</summary><p>Not necessarily — it can live on a simple page I set up, or link straight from your Google profile. If you've got a site, even better, it slots right in. And if you'd like a proper website of your own, I can build you one too as a paid add-on, quoted to suit what you need.</p></details>
    <details><summary>Will it book appointments into my calendar?</summary><p>It captures the enquiry — name, what they want, preferred timing and contact — and sends it to you to confirm. It won't promise a slot it can't see, which keeps customers happy. Full calendar booking is available as a custom add-on.</p></details>
    <details><summary>How long until it's live?</summary><p>Usually within a few days of our first chat — most businesses are up and running inside a week. Here's how it goes: we have a quick chat about your services, prices and the questions your customers tend to ask; I build and train your assistant on all of it; then I send you a private link to try it yourself and tell me anything you'd like changed. Once you're happy, I add it to your site (or a page I set up for you) and the leads start landing in your inbox. You're kept in the loop the whole way, and the setup is entirely on me — no tech work on your side.</p></details>
    <details><summary>What if I want to cancel?</summary><p>No lock-in. Cancel anytime. The free 14-day trial means you can see the leads roll in before you pay a penny.</p></details>
  </div>
</div></section>

<section><div class="wrap"><div class="cta-band reveal">
  <h2 class="disp">Let's catch the customers you're missing</h2>
  <p>Chat to the assistant on this page (yes, it's one of ours) or reach Andra directly.</p>
  <button class="btn" onclick="openChat()">Try the assistant</button>
  <div class="links">Or reach me: <a href="tel:__PHONE_TEL__">__PHONE__</a> · <a href="mailto:__EMAIL__">__EMAIL__</a></div>
</div></div></section>

<footer>© <span id="yr"></span> Frontdesk · AI assistants for local businesses · Portsmouth · by __NAME__</footer>

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
  function openChat(){var c=document.getElementById('chat');c.classList.add('open');if(!started){started=true;add("Hey! I'm the Frontdesk assistant — and a live demo of exactly what we'd build for you. Tell me a bit about your business and I'll show you how it could work. What do you do?",'b');}document.getElementById('inp').focus();}
  function toggleChat(){var c=document.getElementById('chat');if(c.classList.contains('open')){c.classList.remove('open');}else{openChat();}}
  function add(txt,who){var m=document.getElementById('msgs');var d=document.createElement('div');d.className='m '+who;d.textContent=txt;m.appendChild(d);m.scrollTop=m.scrollHeight;}
  async function send(){var i=document.getElementById('inp');var v=i.value.trim();if(!v)return;add(v,'u');i.value='';
    try{var r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:v}),credentials:'same-origin'});
      var d=await r.json();add(d.reply,'b');}catch(e){add("Sorry — something glitched. You can also reach Andra directly at __EMAIL__.",'b');}}
</script>
</body>
</html>
"""

PAGE = (PAGE
        .replace("__NAME__", CONTACT_NAME)
        .replace("__PHONE_TEL__", CONTACT_PHONE_TEL)
        .replace("__PHONE__", CONTACT_PHONE)
        .replace("__EMAIL__", CONTACT_EMAIL))


conversations = {}
notified = set()


@app.route("/")
def home():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return Response(PAGE, mimetype="text/html")


@app.route("/chat", methods=["POST"])
def chat():
    sid = session.get("sid") or str(uuid.uuid4())
    session["sid"] = sid
    if sid not in conversations:
        conversations[sid] = [{"role": "system", "content": SYSTEM_PROMPT}]
    convo = conversations[sid]
    convo.append({"role": "user", "content": request.json.get("message", "")})

    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=convo,
        max_tokens=150,
    )
    reply = resp.choices[0].message.content
    convo.append({"role": "assistant", "content": reply})

    if sid not in notified and has_contact_info(convo):
        notified.add(sid)
        threading.Thread(target=send_lead_email, args=(list(convo),), daemon=True).start()

    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5002)))
