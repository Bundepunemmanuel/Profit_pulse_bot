# main.py
# Profit Pulse Bot — multi-mode, multi-tier, referral & payments (NowPayments + AmmerPay),
# DeepSeek integration, local JSON storage, Render-ready (Flask + polling thread).
#
# IMPORTANT: Set secrets as environment variables (see README below).

import os
import json
import threading
from datetime import datetime, timedelta, date
import httpx
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------------------------
# Configuration (env vars)
# ---------------------------
BOT_TOKEN = os.getenv("8216926903:AAHtioI5bemnlxB3sAowr9unWI2DcUH_Sk4")
DEEPSEEK_API_KEY = os.getenv("sk-43f01105c3c24ca9b8b2f32e9c5f3c4c")
NOWPAYMENTS_API_KEY = os.getenv("M7QJQTV-Q464J8W-M3B2DRE-W3MQ95T")
AMMERPAY_TOKEN = os.getenv("5775769170:LIVE:TG_MkngbkIcVMIlu8J6m9z0dkEA")
ADMIN_ID = int(os.getenv("7873376126") or 0)
BINANCE_REF = os.getenv("https://www.binance.com/referral/earn-together/refer2earn-usdc/claim?hl=en&ref=GRO_28502_UIYMA&utm_source=default") or "https://www.binance.com"
FIVERR_REF = os.getenv("https://www.fiverr.com/pe/jjKbAPV") or "https://www.fiverr.com"

# Tier durations (days)
TIERS = {
    "basic": {"price": 5, "days": 7},
    "plus":  {"price": 25, "days": 30},
    "max":   {"price": 90, "days": 365},
}

USERS_FILE = "users.json"
FREE_DAILY_LIMIT = 7
REFERRAL_TARGET = 3  # invites required to get 1-day pro

# ---------------------------
# Simple local JSON storage
# ---------------------------
def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(u):
    with open(USERS_FILE, "w") as f:
        json.dump(u, f, indent=2, default=str)

def get_user(uid, create=True):
    uid = str(uid)
    users = load_users()
    if uid not in users and create:
        users[uid] = {
            "uid": uid,
            "username": None,
            "mode": "business",            # business|investment|mentor
            "tier": None,                 # None or 'basic'/'plus'/'max' for paid tiers
            "pro_expires_at": None,       # ISO string when pro ends
            "is_trial_used": False,       # new-user trial used?
            "free_uses_today": 0,
            "free_uses_date": date.today().isoformat(),
            "ref_code": f"ref_{uid}",
            "ref_count": 0,
            "referred_by": None,
            "created_at": datetime.utcnow().isoformat(),
        }
        save_users(users)
    return users.get(uid)

def save_user_obj(user):
    users = load_users()
    users[str(user["uid"])] = user
    save_users(users)

# ---------------------------
# Utilities: pro/trial/limits
# ---------------------------
def reset_daily_if_needed(user):
    today = date.today().isoformat()
    if user.get("free_uses_date") != today:
        user["free_uses_today"] = 0
        user["free_uses_date"] = today

def can_use_free(user):
    reset_daily_if_needed(user)
    return user.get("free_uses_today", 0) < FREE_DAILY_LIMIT

def increment_free_use(user):
    reset_daily_if_needed(user)
    user["free_uses_today"] = user.get("free_uses_today", 0) + 1
    save_user_obj(user)

def is_pro(user):
    # admin is always pro
    if int(user["uid"]) == ADMIN_ID:
        return True
    exp = user.get("pro_expires_at")
    if exp:
        try:
            exp_dt = datetime.fromisoformat(exp)
            if exp_dt > datetime.utcnow():
                return True
            else:
                # expired
                user["tier"] = None
                user["pro_expires_at"] = None
                save_user_obj(user)
                return False
        except Exception:
            return False
    return False

def grant_pro(user, days, tier_name=None):
    expire = datetime.utcnow() + timedelta(days=days)
    user["pro_expires_at"] = expire.isoformat()
    user["tier"] = tier_name
    save_user_obj(user)

# ---------------------------
# Referral helpers
# ---------------------------
def handle_start_referral(new_uid, start_payload):
    # start_payload expected like "ref_<uid>" or None
    if not start_payload:
        return
    if start_payload.startswith("ref_"):
        referrer = start_payload.split("ref_")[-1]
        users = load_users()
        ref_user = users.get(str(referrer))
        if ref_user:
            # set referred_by for new user if not set
            newu = get_user(new_uid)
            if not newu.get("referred_by"):
                newu["referred_by"] = referrer
                save_user_obj(newu)
                # increment referrer count
                ref_user["ref_count"] = ref_user.get("ref_count", 0) + 1
                save_user_obj(ref_user)
                # reward if reached target
                if ref_user.get("ref_count", 0) >= REFERRAL_TARGET:
                    # grant 1 day pro reward and reset count
                    grant_pro(ref_user, days=1, tier_name="referral-1day")
                    ref_user["ref_count"] = 0
                    save_user_obj(ref_user)

# ---------------------------
# NowPayments helpers
# ---------------------------
NOWPAY_BASE = "https://api.nowpayments.io/v1"

async def create_nowpayments_payment(amount_usd, order_id, order_desc):
    if not NOWPAYMENTS_API_KEY:
        return {"error": "NowPayments key not configured."}
    headers = {"x-api-key": NOWPAYMENTS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "price_amount": amount_usd,
        "price_currency": "usd",
        "order_id": order_id,
        "order_description": order_desc,
        # optionally set pay_currency, etc.
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{NOWPAY_BASE}/payment", headers=headers, json=payload)
        try:
            return r.json()
        except Exception:
            return {"error": "NowPayments invalid response."}

async def check_nowpayments_payment(payment_id):
    if not NOWPAYMENTS_API_KEY:
        return {"error": "NowPayments key not configured."}
    headers = {"x-api-key": NOWPAYMENTS_API_KEY}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{NOWPAY_BASE}/payment/{payment_id}", headers=headers)
        try:
            return r.json()
        except Exception:
            return {"error": "NowPayments invalid response."}

# ---------------------------
# DeepSeek helper
# ---------------------------
def ask_deepseek_sync(prompt):
    if not DEEPSEEK_API_KEY:
        return "DeepSeek key not configured."
    try:
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
        payload = {"model":"deepseek-chat","messages":[{"role":"user","content":prompt}]}
        r = httpx.post("https://api.deepseek.com/v1/chat/completions", headers=headers, json=payload, timeout=20)
        data = r.json()
        return data.get("choices",[{}])[0].get("message",{}).get("content","(No reply)")
    except Exception as e:
        return f"DeepSeek error: {e}"

# ---------------------------
# Bot command handlers
# ---------------------------

# Flask app for Render health check
app = Flask(__name__)
@app.route("/")
def home():
    return {"status": "alive", "app": "Profit Pulse Bot"}

# utilities to build subscription buttons
def subscribe_buttons():
    kb = [
        [InlineKeyboardButton("Pro Basic — $5 (7 days)", callback_data="buy_tier_basic")],
        [InlineKeyboardButton("Pro Plus — $25 (30 days)", callback_data="buy_tier_plus")],
        [InlineKeyboardButton("Pro Max — $90 (365 days)", callback_data="buy_tier_max")],
        [InlineKeyboardButton("Pay with AmmerPay", callback_data="buy_ammer")]
    ]
    return InlineKeyboardMarkup(kb)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    # parse start payload for referral
    start_payload = None
    if args:
        start_payload = args[0]
    u = get_user(user.id, create=True)
    u["username"] = user.username
    save_user_obj(u)
    handle_start_referral(user.id, start_payload)

    # new-user trial (auto 1 day)
    if not u.get("is_trial_used"):
        grant_pro(u, days=1, tier_name="trial-1day")
        u["is_trial_used"] = True
        save_user_obj(u)

    text = "👋 Welcome to Profit Pulse!\nChoose a mode with /switch or use the buttons below."
    await update.message.reply_text(text)
    await update.message.reply_text("Want Pro features? Tap to upgrade:", reply_markup=subscribe_buttons())

async def switch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    u = get_user(uid)
    if not u:
        await update.message.reply_text("Please /start first.")
        return
    # show mode options
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💼 Business", callback_data="mode_business"),
         InlineKeyboardButton("💸 Investment", callback_data="mode_investment")],
        [InlineKeyboardButton("🧠 AI Mentor", callback_data="mode_mentor")]
    ])
    await update.message.reply_text("Choose a mode:", reply_markup=kb)

async def callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = str(q.from_user.id)
    u = get_user(uid)
    if data.startswith("mode_"):
        mode = data.split("mode_")[1]
        u["mode"] = mode
        save_user_obj(u)
        await q.edit_message_text(f"Mode set: {mode.title()}")
        return
    if data.startswith("buy_tier_"):
        tier = data.split("buy_tier_")[1]
        # create NowPayments invoice async and reply link
        order_id = f"{uid}-{tier}-{int(datetime.utcnow().timestamp())}"
        order_desc = f"ProfitPulse {tier} subscription for user {uid}"
        info = await create_nowpayments_payment(TIERS[tier]["price"], order_id, order_desc)
        if info.get("invoice_url") or info.get("invoice_id") or info.get("payment_url") or info.get("id"):
            # nowpayments returns different fields depending on API version
            invoice_url = info.get("invoice_url") or info.get("payment_url") or info.get("invoice_link") or info.get("id")
            invoice_id = info.get("id") or info.get("invoice_id") or info.get("payment_id")
            # store last invoice id in user for verifying later
            u["last_invoice_id"] = invoice_id
            save_user_obj(u)
            await q.edit_message_text(f"✅ Invoice created. Pay here:\n{invoice_url}\n\nAfter paying use /verify <invoice_id>")
        else:
            await q.edit_message_text(f"Could not create invoice: {info}")
        return
    if data == "buy_ammer":
        # provide AmmerPay link (user can click and pay via Telegram)
        await q.edit_message_text(f"Open AmmerPay bot to pay: https://t.me/AmmerPayBot?start={AMMERPAY_TOKEN}")
        return

# ---------------------------
# Mode commands — many commands will check is_pro(user)
# ---------------------------

# Helper to enforce access:
async def require_feature(update: Update, ctx: ContextTypes.DEFAULT_TYPE, pro_required=False):
    u = get_user(update.effective_user.id)
    if pro_required and not is_pro(u):
        # allow limited free usage
        if can_use_free(u):
            increment_free_use(u)
            return True
        else:
            await update.message.reply_text("🔒 Free daily limit reached (7). Upgrade for unlimited access.")
            return False
    return True

# Business mode: example advanced commands
async def bizplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if u["mode"] != "business":
        await update.message.reply_text("Switch to Business Mode first with /switch.")
        return
    allowed = await require_feature(update, context, pro_required=False)
    if not allowed: return
    idea = " ".join(context.args) or "a small online tutoring business"
    # simple template (could be improved by calling DeepSeek for Pro users)
    if is_pro(u):
        plan = ask_deepseek_sync(f"Create a 7-step business plan for: {idea}")
        await update.message.reply_text(f"📝 Business Plan (AI):\n{plan}")
    else:
        await update.message.reply_text(f"📝 Quick Business Plan for *{idea}*:\n1) Define niche\n2) Build MVP\n3) Setup basic marketing\n4) Find first customers", parse_mode="Markdown")

async def namegen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if u["mode"] != "business":
        await update.message.reply_text("Switch to Business Mode first with /switch.")
        return
    if not is_pro(u):
        allowed = await require_feature(update, context, pro_required=True)
        if not allowed: return
    topic = " ".join(context.args) or "tech startup"
    res = ask_deepseek_sync(f"Generate 10 brand names and short slogans for: {topic}")
    await update.message.reply_text(f"🔤 Name ideas:\n{res}")

# Investment mode commands
async def crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if u["mode"] != "investment":
        await update.message.reply_text("Switch to Investment Mode first with /switch.")
        return
    allowed = await require_feature(update, context, pro_required=False)
    if not allowed: return
    # simple public API call for price example
    symbol = (context.args[0].upper() if context.args else "BTCUSDT")
    try:
        r = httpx.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}", timeout=10)
        data = r.json()
        await update.message.reply_text(f"📈 {symbol} price: {data.get('price')}")
    except Exception as e:
        await update.message.reply_text(f"Error fetching price: {e}")

async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if u["mode"] != "investment":
        await update.message.reply_text("Switch to Investment Mode first with /switch.")
        return
    if not is_pro(u):
        allowed = await require_feature(update, context, pro_required=True)
        if not allowed: return
    # pro-only: call DeepSeek for signal generation
    q = " ".join(context.args) or "generate 3 crypto trading signals for BTC and ETH"
    res = ask_deepseek_sync(f"As a crypto analyst, {q}. Provide signals and risk levels.")
    await update.message.reply_text(f"📡 Signals (AI):\n{res}")

# AI Mentor commands
async def askai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if u["mode"] != "mentor":
        await update.message.reply_text("Switch to AI Mentor Mode first with /switch.")
        return
    # askai is pro-only
    if not is_pro(u):
        allowed = await require_feature(update, context, pro_required=True)
        if not allowed: return
    q = " ".join(context.args)
    if not q:
        await update.message.reply_text("Usage: /askai <question>")
        return
    res = ask_deepseek_sync(q)
    await update.message.reply_text(f"🧠 Mentor says:\n{res}")

# Referral link show
async def myref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    code = u.get("ref_code")
    link = f"https://t.me/{(os.getenv('BOT_USERNAME') or 'ProfitPulseBot')}?start={code}"
    await update.message.reply_text(f"🎯 Your referral link:\n{link}\nReferrals: {u.get('ref_count',0)} / {REFERRAL_TARGET}")

# Upgrade command to show options
async def upgrade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Choose a plan or pay with AmmerPay:", reply_markup=subscribe_buttons())

# Verify NowPayments invoice
async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    if not context.args:
        await update.message.reply_text("Usage: /verify <invoice_id>")
        return
    invoice_id = context.args[0]
    info = await check_nowpayments_payment(invoice_id)
    status = info.get("payment_status") or info.get("status")
    if status and status.lower() in ("finished","success","confirmed"):
        # determine tier from user.last requested tier if we stored it; fallback to basic 7d
        tier = u.get("last_requested_tier") or "basic"
        grant_pro(u, days=TIERS[tier]["days"], tier_name=tier)
        await update.message.reply_text(f"✅ Payment confirmed. You are now Pro ({tier}) until {u['pro_expires_at']}")
    else:
        await update.message.reply_text(f"⚠️ Payment not complete. Status: {status}\nResponse: {info}")

# Admin commands
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return
    users = load_users()
    total = len(users)
    pro_count = sum(1 for u in users.values() if u.get("pro_expires_at"))
    text = f"👑 Admin Stats\nTotal users: {total}\nPro / trial users: {pro_count}"
    await update.message.reply_text(text)

async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return
    users = load_users()
    await update.message.reply_text(f"Users data:\n{json.dumps(users, indent=2)[:3800]}")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    users = load_users()
    sent = 0
    for k in users.keys():
        try:
            await context.bot.send_message(chat_id=int(k), text=f"📢 Admin: {msg}")
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"Broadcast sent to {sent} users.")

# ---------------------------
# Setup bot and handlers
# ---------------------------
def run_bot():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set in env")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # basic handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("switch", switch_handler))
    app.add_handler(CallbackQueryHandler(callback_query))
    app.add_handler(CommandHandler("bizplan", bizplan))
    app.add_handler(CommandHandler("namegen", namegen))
    app.add_handler(CommandHandler("crypto", crypto))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(CommandHandler("askai", askai))
    app.add_handler(CommandHandler("myref", myref))
    app.add_handler(CommandHandler("upgrade", upgrade_cmd))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    print("🤖 Bot polling starting...")
    app.run_polling()

if __name__ == "__main__":
    # Run bot polling in a thread and Flask web server for Render
    t = threading.Thread(target=run_bot, daemon=True)
    t.start()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
