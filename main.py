import os
import logging
import sqlite3
from flask import Flask, request
from telegram import Update
from telegram.ext import Dispatcher
import requests
import matplotlib.pyplot as plt
import google.generativeai as genai
from datetime import datetime

# تنظیمات اولیه
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# متغیرهای محیطی (APIها تو کد نیستن)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not TELEGRAM_TOKEN or not GOOGLE_API_KEY:
    logger.error("TELEGRAM_TOKEN or GOOGLE_API_KEY not set in environment variables")
    exit(1)

genai.configure(api_key=GOOGLE_API_KEY)

# ثابت‌ها
RSS_SOURCES = ["https://www.iran-btc.com/feed/", "https://cointelegraph.com/rss"]
COINPAPRIKA_URL = "https://api.coinpaprika.com/v1/tickers?quotes=USD"
DONATION_ADDRESS = "CW5SGHVjrHJks3XTrLDmcN4NQJdn2aySAPoA67799d3j"

# کش و دیتابیس
sent_news = []
MAX_NEWS_CACHE = 20
DB_NAME = "user_settings.db"

# پاک‌سازی HTML
def clean_html(raw_html):
    import re
    from html import unescape
    if not raw_html:
        return ""
    clean = re.compile('<.*?>')
    return unescape(re.sub(clean, '', raw_html)).strip()

# دریافت آخرین خبر
def get_latest_news(lang="fa"):
    global sent_news
    if len(sent_news) > MAX_NEWS_CACHE:
        sent_news = sent_news[-MAX_NEWS_CACHE:]
    sources = [RSS_SOURCES[0]] if lang == "fa" else [RSS_SOURCES[1]]
    for rss_url in sources:
        try:
            logger.info(f"Fetching news from {rss_url}")
            response = requests.get(rss_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {rss_url}, status: {response.status_code}")
                continue
            feed = feedparser.parse(response.content)
            for entry in feed.entries:
                title = entry.title if hasattr(entry, 'title') else "بدون عنوان" if lang == "fa" else "No Title"
                summary = clean_html(entry.description) if hasattr(entry, 'description') else ""
                link = entry.link if hasattr(entry, 'link') else "#"
                if link in sent_news:
                    continue
                if lang == "fa" and rss_url == RSS_SOURCES[1]:
                    translated = translate_text(title, summary, "fa")
                    title, summary = translated["title"], translated["summary"]
                elif lang == "en" and rss_url == RSS_SOURCES[0]:
                    translated = translate_text(title, summary, "en")
                    title, summary = translated["title"], translated["summary"]
                sent_news.append(link)
                logger.info(f"News fetched: {title}")
                return {"title": title, "summary": summary, "link": link, "date": entry.published if hasattr(entry, 'published') else ""}
        except Exception as e:
            logger.error(f"RSS error: {e}")
            continue
    return {"title": "اخباری مرتبط با ارز دیجیتال یافت نشد!" if lang == "fa" else "No cryptocurrency news found!", "summary": "", "link": "", "date": ""}

# ترجمه با Gemini
def translate_text(title, summary, target_lang):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro')
        prompt = f"Translate the following title and summary to {target_lang}:\nTitle: {title}\nSummary: {summary[:200]}"
        response = model.generate_content(prompt)
        translated_text = response.text
        translated_title = translated_text.split("Title: ")[1].split("\n")[0]
        translated_summary = translated_text.split("Summary: ")[1].split("\n")[0]
        return {"title": translated_title, "summary": translated_summary}
    except Exception as e:
        logger.error(f"Gemini translate error: {e}")
        return {"title": title, "summary": summary}

# تحلیل با Gemini
def analyze_with_groq(text, lang="fa"):
    try:
        model = genai.GenerativeModel('gemini-1.5-pro')
        if lang == "fa":
            prompt = f"تحلیل خبر ارز دیجیتال زیر:\n'{text[:500]}'\n\nلطفاً یک تحلیل دقیق و جامع اما مختصر (حداکثر 100 کلمه) ارائه کن. شامل:\n1. دیدگاه: (صعودی/نزولی/خنثی)\n2. دلایل مهم\n3. تأثیر بر بازار (کوتاه‌مدت/بلندمدت)\n4. ارزهای تأثیرپذیر\nپاسخ در فارسی."
        else:
            prompt = f"Analyze this crypto news:\n'{text[:500]}'\n\nProvide a concise analysis (max 100 words) including:\n1. Sentiment: (Bullish/Bearish/Neutral)\n2. Key reasons\n3. Market impact (short/long-term)\n4. Affected cryptocurrencies\nUse English."
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Gemini analysis error: {e}")
        return "خطا در تحلیل." if lang == "fa" else "Analysis error."

# دریافت قیمت‌ها از Coinpaprika
def get_prices():
    try:
        logger.info(f"Fetching prices from {COINPAPRIKA_URL}")
        response = requests.get(COINPAPRIKA_URL, timeout=10)
        if response.status_code == 200:
            coins = response.json()
            result = []
            for coin in coins[:15]:
                quotes = coin.get('quotes', {}).get('USD', {})
                result.append({
                    'symbol': coin.get('symbol', '').upper(),
                    'name': coin.get('name', ''),
                    'price': quotes.get('price', 0),
                    'change_24h': quotes.get('percent_change_24h', 0),
                    'market_cap': quotes.get('market_cap', 0)
                })
            logger.info(f"Prices fetched for {len(result)} coins")
            return result
        logger.warning(f"Failed to fetch prices, status: {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Coinpaprika error: {e}")
        return None

# دریافت چارت از Binance
def get_chart_data(symbol="BTCUSDT", interval="1h", limit=100):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            chart_data = []
            for candle in data:
                chart_data.append({
                    "time": candle[0] / 1000,
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5])
                })
            return chart_data
        logger.warning(f"Failed to fetch chart data, status: {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Binance API error: {e}")
        return None

def generate_chart_image(symbol="BTCUSDT", interval="1h"):
    data = get_chart_data(symbol, interval)
    if not data:
        return None
    
    plt.figure(figsize=(10, 6))
    plt.plot([d["time"] for d in data], [d["close"] for d in data], label="Close Price")
    plt.title(f"{symbol} Price Chart ({interval})")
    plt.xlabel("Time")
    plt.ylabel("Price (USDT)")
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    
    img_path = "/tmp/chart.png"
    plt.savefig(img_path)
    plt.close()
    return img_path

# مدیریت دیتابیس
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (chat_id TEXT PRIMARY KEY, language TEXT, notifications INTEGER, notification_time TEXT, favorites TEXT)''')
    conn.commit()
    conn.close()

def get_user_settings(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT language, notifications, notification_time, favorites FROM users WHERE chat_id = ?", (str(chat_id),))
    result = c.fetchone()
    if result:
        lang, notif, time, fav = result
        return {"language": lang, "notifications": bool(notif), "notification_time": time, "favorites": eval(fav) if fav else []}
    conn.close()
    return {"language": None, "notifications": False, "notification_time": "12:00", "favorites": []}

def save_user_settings(chat_id, settings):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (chat_id, language, notifications, notification_time, favorites) VALUES (?, ?, ?, ?, ?)",
              (str(chat_id), settings["language"], settings["notifications"], settings["notification_time"], str(settings["favorites"])))
    conn.commit()
    conn.close()

# دکمه‌ها
keyboard_fa = [["🔄 آخرین خبر", "📊 تحلیل بازار"], ["💰 قیمت‌ها", "/chart BTCUSDT 1h"], ["⚙️ تنظیمات", "💰 حمایت"]]
keyboard_en = [["🔄 Latest News", "📊 Market Analysis"], ["💰 Prices", "/chart BTCUSDT 1h"], ["⚙️ Settings", "💰 Donate"]]

# پردازش پیام‌ها
def handle_message(update, context):
    bot = context.bot
    chat_id = update.message.chat_id
    text = update.message.text
    settings = get_user_settings(chat_id)
    lang = settings["language"] or "fa"
    markup = ReplyKeyboardMarkup(keyboard_fa, resize_keyboard=True) if lang == "fa" else ReplyKeyboardMarkup(keyboard_en, resize_keyboard=True)

    if not settings["language"]:
        update.message.reply_text("🌐 لطفاً زبان را انتخاب کنید / Select language:", reply_markup=ReplyKeyboardMarkup([["🇮🇷 فارسی", "🇺🇸 English"]], one_time_keyboard=True))
        return

    if text in ["🇮🇷 فارسی", "🇺🇸 English"]:
        settings["language"] = "fa" if text == "🇮🇷 فارسی" else "en"
        save_user_settings(chat_id, settings)
        update.message.reply_text("زبان تنظیم شد!" if lang == "fa" else "Language set!", reply_markup=markup)
        return

    elif text in ["🔄 آخرین خبر", "🔄 Latest News"]:
        news = get_latest_news(lang)
        analysis = analyze_with_groq(news["title"] + " " + news["summary"], lang)
        msg = f"📰 {('تیتر' if lang == 'fa' else 'Title')}: {news['title']}\n📝 {('خلاصه' if lang == 'fa' else 'Summary')}: {news['summary']}\n🔗 {news['link']}"
        bot.send_message(chat_id, msg)
        bot.send_message(chat_id, f"📊 {('تحلیل' if lang == 'fa' else 'Analysis')}:\n{analysis}", reply_markup=markup)

    elif text in ["📊 تحلیل بازار", "📊 Market Analysis"]:
        news1 = get_latest_news(lang)
        news2 = get_latest_news(lang)
        prices = get_prices()
        up_count, down_count = 0, 0
        if prices:
            for coin in prices[:10]:
                if coin['change_24h'] > 0:
                    up_count += 1
                else:
                    down_count += 1
        combined_data = f"{news1['title']} {news1['summary']} {news2['title']} {news2['summary']} Up/Down: {up_count}/{down_count}"
        analysis = analyze_with_groq(combined_data, lang)
        msg = f"📊 {('تحلیل بازار' if lang == 'fa' else 'Market Analysis')}:\n{analysis}\n📈 {up_count} صعودی / 📉 {down_count} نزولی" if lang == "fa" else f"📊 Market Analysis:\n{analysis}\n📈 {up_count} up / 📉 {down_count} down"
        bot.send_message(chat_id, msg, reply_markup=markup)

    elif text in ["💰 قیمت‌ها", "💰 Prices"]:
        prices = get_prices()
        if prices:
            msg = f"💰 {('قیمت‌ها' if lang == 'fa' else 'Prices')}:\n"
            for i, coin in enumerate(prices):
                change_emoji = "🟢" if coin['change_24h'] > 0 else "🔴"
                msg += f"{i+1}. {coin['name']} ({coin['symbol']}): ${coin['price']:,.2f} {change_emoji} {coin['change_24h']:.2f}%\n"
        else:
            msg = "خطا در دریافت قیمت‌ها." if lang == "fa" else "Error fetching prices."
        bot.send_message(chat_id, msg, reply_markup=markup)

    elif text.startswith("/chart"):
        parts = text.split()
        if len(parts) >= 2:
            symbol = parts[1].upper() + "USDT"
            interval = parts[2] if len(parts) > 2 else "1h"
            img_path = generate_chart_image(symbol, interval)
            if img_path and os.path.exists(img_path):
                with open(img_path, 'rb') as photo:
                    bot.send_photo(chat_id, photo, caption=f"{symbol} Chart ({interval})")
                os.remove(img_path)
            else:
                bot.send_message(chat_id, "خطا در تولید چارت." if lang == "fa" else "Error generating chart.")

    elif text in ["⚙️ تنظیمات", "⚙️ Settings"]:
        settings_markup = ReplyKeyboardMarkup([["🌐 تغییر زبان", "🔔 اعلان‌ها"], ["↩️ بازگشت"]] if lang == "fa" else [["🌐 Change Language", "🔔 Notifications"], ["↩️ Back"]], resize_keyboard=True)
        bot.send_message(chat_id, "", reply_markup=settings_markup)

    elif text in ["🌐 تغییر زبان", "🌐 Change Language"]:
        settings["language"] = "en" if lang == "fa" else "fa"
        save_user_settings(chat_id, settings)
        bot.send_message(chat_id, "زبان تغییر کرد!" if lang == "fa" else "Language changed!", reply_markup=markup)

    elif text in ["🔔 اعلان‌ها", "🔔 Notifications"]:
        settings["notifications"] = not settings["notifications"]
        save_user_settings(chat_id, settings)
        bot.send_message(chat_id, f"اعلان‌ها {'فعال' if settings['notifications'] else 'غیرفعال'} شد." if lang == "fa" else f"Notifications {'enabled' if settings['notifications'] else 'disabled'}.", reply_markup=markup)

    elif text in ["↩️ بازگشت", "↩️ Back"]:
        bot.send_message(chat_id, "", reply_markup=markup)

    elif text in ["💰 حمایت", "💰 Donate"]:
        msg = f"💰 {('حمایت از ربات' if lang == 'fa' else 'Support the bot')}:\n`{DONATION_ADDRESS}`\n{('ممنون!' if lang == 'fa' else 'Thanks!')}" if lang == "fa" else f"💰 Support the bot:\n`{DONATION_ADDRESS}`\nThanks!"
        bot.send_message(chat_id, msg, parse_mode="Markdown", reply_markup=markup)

    else:
        bot.send_message(chat_id, "", reply_markup=markup)

# تنظیم Webhook
@app.route('/', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(), bot=None)
    if update:
        dispatcher = Dispatcher(bot, None, workers=0)
        dispatcher.process_update(update)
        logger.info("Webhook processed a POST request")
    return 'OK', 200

# اجرای برنامه
if __name__ == "__main__":
    init_db()
    bot = Bot(TELEGRAM_TOKEN)
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
