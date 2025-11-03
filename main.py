from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import os

TOKEN = os.getenv("BOT_TOKEN", "8216926903:AAHtioI5bemnlxB3sAowr9unWI2DcUH_Sk4")

app = Flask(__name__)

@app.route('/')
def home():
    return "Profit Pulse bot is running!"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Hello! Profit Pulse bot is live and ready!")

def main():
    app_telegram = ApplicationBuilder().token(TOKEN).build()
    app_telegram.add_handler(CommandHandler("start", start))
    app_telegram.run_polling()

if __name__ == "__main__":
    import threading
    threading.Thread(target=main).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
