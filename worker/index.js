// Cloudflare Worker for Frontdesk (frontdesk.org.uk). The shared chat engine is in
// core.js; this file holds the Frontdesk wording and lead email layout (as app.py).
import { makeWorker, esc, transcript, findPhone, findEmail } from "./core.js";
import { SYSTEM_PROMPT, LEAD_SUMMARY_PROMPT } from "./prompts.js";

function row(label, value) {
  if (!value) return "";
  return '<tr>' +
    `<td style="padding:10px 16px;border-bottom:1px solid #efe9df;color:#8a8378;font-size:13px;white-space:nowrap;vertical-align:top;width:150px">${esc(label)}</td>` +
    `<td style="padding:10px 16px;border-bottom:1px solid #efe9df;color:#17201d;font-size:14px;font-weight:600">${esc(value)}</td>` +
    '</tr>';
}

function transcriptHtml(conv) {
  return conv.map((m) => {
    const [who, color, bg] = m.role === "user" ? ["Visitor", "#2449f5", "#f4f4ef"] : ["Frontdesk Assistant", "#7c5cff", "#ffffff"];
    return '<div style="margin:0 0 12px">' +
      `<div style="font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:${color};font-weight:700;margin-bottom:4px">${who}</div>` +
      `<div style="background:${bg};border:1px solid #ece6da;border-radius:10px;padding:11px 14px;font-size:14px;color:#2a2630;line-height:1.5">${esc(m.content).replace(/\n/g, "<br>")}</div>` +
      '</div>';
  }).join("");
}

function leadEmail({ pick, conv }) {
  const fields = {
    Name: pick("name"),
    "Business / trade": pick("business / trade", "business", "trade"),
    Phone: findPhone(conv),
    Email: findEmail(conv),
    "Has a website?": pick("has a website?", "has a website", "website"),
    "Wants help with": pick("what they want help with", "wants help with"),
    "Handles enquiries now": pick("how they handle enquiries now", "handles enquiries now"),
    "Best contact": pick("best contact"),
    Notes: pick("other notes", "notes"),
  };
  const text = ["NEW LEAD - Frontdesk", "===================="];
  for (const [k, v] of Object.entries(fields)) text.push(`${k}: ${v || "Not specified"}`);
  text.push("====================", "", "Full conversation:", "", transcript(conv));

  const html = '<!DOCTYPE html><html><body style="margin:0;background:#efe9df;padding:24px;' +
    'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif">' +
    '<div style="max-width:620px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 14px rgba(21,19,29,.10)">' +
    '<div style="background:#17201d;padding:26px 30px">' +
    '<div style="color:#2449f5;font-size:12px;letter-spacing:.2em;text-transform:uppercase;font-weight:700">Frontdesk</div>' +
    '<div style="color:#fff;font-size:22px;font-weight:800;margin-top:5px;letter-spacing:-.01em">New lead from your website</div></div>' +
    '<div style="padding:26px 30px">' +
    '<p style="margin:0 0 20px;font-size:14px;color:#6c6760">Captured by your website assistant - here\'s who to follow up with:</p>' +
    '<table style="width:100%;border-collapse:collapse;border:1px solid #efe9df;border-radius:8px;overflow:hidden;margin-bottom:28px">' +
    Object.entries(fields).map(([k, v]) => row(k, v)).join("") + '</table>' +
    '<div style="font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:#a59e92;font-weight:700;margin-bottom:14px">Full conversation</div>' +
    transcriptHtml(conv) + '</div>' +
    '<div style="background:#faf7f1;padding:16px 30px;border-top:1px solid #efe9df;font-size:12px;color:#a59e92">Sent automatically by your Frontdesk website assistant.</div>' +
    '</div></body></html>';

  return { subject: `New lead from your website - ${fields.Phone || "no number yet"}`, text: text.join("\n"), html };
}

export default makeWorker({
  SYSTEM_PROMPT,
  LEAD_SUMMARY_PROMPT,
  MAX_TOKENS: 160,
  TEMPERATURE: 0.4,
  MAXED_REPLY: "Thanks for all the detail! Drop your name and number and Andra will pick this up with you personally.",
  EMPTY_REPLY: "Brilliant — that's everything I need. Andra will be in touch personally, usually the same day.",
  // app.py sends once the chat (with its system prompt) reaches 8 messages.
  sendNowAfter: (full) => full.length + 1 >= 8,
  leadEmail,
  followUpEmail: () => null,
});
