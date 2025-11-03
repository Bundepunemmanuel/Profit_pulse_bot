from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import os, requests, asyncio

# ======================
# Environment variables (for safety)
# ======================
BOT_TOKEN = os.getenv("8216926903:AAHtioI5bemnlxB3sAowr9unWI2DcUH_Sk4")
NOWPAYMENTS_API_KEY = os.getenv("M7QJQTV-Q464J8W-M3B2DRE-W3MQ95T")
AMMER_TOKEN = os.getenv("5775769170:LIVE:TG_MkngbkIcVMIlu8J6m9z0dkEA")
DEEPSEEK_API_KEY = os.getenv("sk-43f01105c3c24ca9b8b2f32e9c5f3c4c")
ADMIN_ID = int(os.getenv("7873376126", 0))

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Profit Pulse Bot is running!"

# ======================
# Telegram Bot Handlers
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome to Profit Pulse!\nI can show your crypto profits and connect you to payment gateways!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💡 Available commands:\n/start - Welcome message\n/pay - Create crypto payment\n/ai <question> - Ask AI powered by DeepSeek")

async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💰 Creating payment link... please wait...")
    url = "https://api.nowpayments.io/v1/payment"
    headers = {"x-api-key": NOWPAYMENTS_API_KEY}
    data = {
        "price_amount": 5,
        "price_currency": "usd",
        "pay_currency": "usdttrc20",
        "order_id": "order123",
        "order_description": "Profit Pulse Premium Access"
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        info = response.json()
        await update.message.reply_text(f"✅ Payment created!\n\n🔗 Pay here: {info.get('invoice_url', 'No link')}")
    except Exception as e:
        await update.message.reply_text(f"❌ Payment failed: {e}")

async def ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🤖 Please provide a question, e.g. /ai How to trade crypto safely?")
        return
    query = " ".join(context.args)
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
    try:
        r = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": query}],
            }
        )
        res = r.json()
        reply = res.get("choices", [{}])[0].get("message", {}).get("content", "No reply")
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"⚠️ AI Error: {e}")

def run_bot():
    app_telegram = ApplicationBuilder().token(BOT_TOKEN).build()
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.add_handler(CommandHandler("help", help_command))
    app_telegram.add_handler(CommandHandler("pay", pay))
    app_telegram.add_handler(CommandHandler("ai", ai))
    app_telegram.run_polling()

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
