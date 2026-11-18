// Lets a page's original chat code run unchanged on Cloudflare.
// The page still calls POST /chat {message} and POST /upload {image}; this script
// answers those calls in the browser: it keeps the conversation, asks /api/chat
// for the reply, and sends the enquiry email through /api/lead.
// Settings come from window.CHAT = {photoReply, photoNote, maxPhotosReply}.
(function () {
  var CFG = window.CHAT || {};
  var PHOTO_REPLY = CFG.photoReply || "Thanks, got the photo - that really helps. You can add another, or tell me if that is all and I will carry on with the quote details.";
  var PHOTO_NOTE = CFG.photoNote || "(Customer attached a photo of the job)";
  var MAX_PHOTOS_REPLY = CFG.maxPhotosReply || "Thanks - that's plenty of photos for now. Leave your name and number and we'll take a look and get you a quote.";
  var MAX_PHOTOS = 6, FALLBACK_MS = 90 * 1000;
  var realFetch = window.fetch.bind(window);
  var state = { history: [], photos: [], sent: false, hasContact: false, hp: "" };
  var timer = 0;

  function reply(obj, status) {
    return new Response(JSON.stringify(obj), { status: status || 200, headers: { "Content-Type": "application/json" } });
  }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  async function post(path, body, opts) {
    for (var i = 0; ; i++) {
      var r = await realFetch(path, Object.assign({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, opts || {}));
      var d = await r.json().catch(function () { return {}; });
      if (r.status === 429 && d.error === "busy" && i < 2) { await wait((d.retryAfter || 10) * 1000); continue; }
      if (!r.ok) throw Object.assign(new Error(d.error || r.status), { data: d });
      return d;
    }
  }
  function pushUser(text) {
    var last = state.history[state.history.length - 1];
    if (last && last.role === "user") last.content += "\n" + text;
    else state.history.push({ role: "user", content: text });
  }
  function arm() { clearTimeout(timer); timer = setTimeout(sendLead, FALLBACK_MS); }

  async function sendLead() {
    clearTimeout(timer);
    if (state.sent || !state.hasContact) return;
    state.sent = true;
    try { await post("/api/lead", { history: state.history, images: state.photos, website: state.hp }); }
    catch (e) { state.sent = false; }
  }

  async function chat(body) {
    var msg = (body.message || "").trim();
    state.hp = body.website || "";
    if (!msg) return reply({ reply: "Sorry, I didn't catch that - could you type that again?" });
    pushUser(msg);
    try {
      var d = await post("/api/chat", { history: state.history, website: state.hp });
      if (d.lead === undefined) { state.history.pop(); return reply({ reply: d.reply }); }
      state.history.push({ role: "assistant", content: d.reply });
      if (d.lead) state.hasContact = true;
      if (d.lead === "now") sendLead();
      else if (d.lead === "later" && !state.sent) arm();
      return reply({ reply: d.reply });
    } catch (e) {
      state.history.pop();
      return reply({ reply: (e.data && e.data.reply) || "Sorry, I had a brief hiccup there - could you send that again?" });
    }
  }

  async function upload(body) {
    if (typeof body.image !== "string" || body.image.indexOf("data:image/") !== 0) {
      return reply({ reply: "Sorry, I couldn't read that image. Please try a JPG or PNG photo." }, 400);
    }
    if (state.photos.length >= MAX_PHOTOS) return reply({ reply: MAX_PHOTOS_REPLY });
    state.photos.push(body.image);
    pushUser(PHOTO_NOTE);
    state.history.push({ role: "assistant", content: PHOTO_REPLY });
    if (state.sent) post("/api/lead", { history: state.history, images: [body.image], followUp: true, website: state.hp }).catch(function () {});
    else if (state.hasContact) arm();
    return reply({ reply: PHOTO_REPLY });
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = url.replace(location.origin, "");
    var method = ((init && init.method) || "GET").toUpperCase();
    if (method === "POST" && (path === "/chat" || path === "/upload")) {
      var body = {};
      try { body = JSON.parse((init && init.body) || "{}"); } catch (e) {}
      return path === "/chat" ? chat(body) : upload(body);
    }
    return realFetch(input, init);
  };

  // If they leave after giving a number but before the email went, send it now (text only:
  // browsers cap what can be sent while a page closes).
  window.addEventListener("pagehide", function () {
    if (state.sent || !state.hasContact) return;
    state.sent = true;
    try {
      realFetch("/api/lead", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ history: state.history, images: [], missedPhotos: state.photos.length, website: state.hp }) });
    } catch (e) {}
  });
})();
