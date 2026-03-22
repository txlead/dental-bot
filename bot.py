import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI
import httpx
import os
import threading
from flask import Flask
 
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
 
bot    = telebot.TeleBot(BOT_TOKEN)
client = OpenAI(api_key=OPENAI_KEY, http_client=httpx.Client())
 
CLINIC_INFO = """
Ты вежливый AI-ассистент стоматологической клиники. Отвечай ТОЛЬКО на русском языке.
Отвечай кратко и по делу — не более 3-4 предложений.
 
Услуги и цены:
- Консультация: бесплатно
- Профчистка зубов: от 3 500 руб (включает ультразвук + полировку)
- Лечение кариеса: от 4 500 руб
- Имплантация: от 35 000 руб (включает консультацию)
- Отбеливание: от 8 000 руб
 
Режим работы: пн-пт 9:00–21:00, сб-вс 10:00–18:00
Адрес: Москва, ул. Примерная, 1
Телефон: +7 (999) 123-45-67
 
Правила:
1. Если клиент хочет записаться — скажи что сейчас оформим запись через кнопку "Записаться".
2. Если вопрос про пластины, брекеты, ортодонтию или что-то чего нет в прайсе — скажи точно: "Этот вопрос я передал администратору — вам ответят в течение 2 часов."
3. НИКОГДА не говори просто "уточню у администратора" без объяснения срока ответа.
4. Не придумывай цены которых нет в списке выше.
"""
 
user_state = {}
booking_data = {}
 
def main_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💰 Цены",      callback_data="prices"),
        InlineKeyboardButton("📅 Записаться", callback_data="book")
    )
    markup.row(
        InlineKeyboardButton("📍 Адрес",  callback_data="address"),
        InlineKeyboardButton("❓ Вопрос", callback_data="question")
    )
    return markup
 
def back_to_main():
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("◀️ Главное меню", callback_data="back"))
    return markup
 
def after_price_menu():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📅 Записаться", callback_data="book"),
        InlineKeyboardButton("◀️ Назад",      callback_data="prices")
    )
    return markup
 
def notify_admin(text):
    if ADMIN_CHAT_ID:
        try:
            bot.send_message(ADMIN_CHAT_ID, text)
        except Exception:
            pass
 
@bot.message_handler(commands=["start"])
def start(message):
    user_state.pop(message.chat.id, None)
    booking_data.pop(message.chat.id, None)
    bot.send_message(
        message.chat.id,
        "Здравствуйте! Я AI-ассистент стоматологической клиники 🦷\n\nЧем могу помочь?",
        reply_markup=main_menu()
    )
 
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    cid = call.message.chat.id
    mid = call.message.message_id
    bot.answer_callback_query(call.id)
 
    if call.data in ("back", "prices", "address", "question"):
        user_state.pop(cid, None)
        booking_data.pop(cid, None)
 
    if call.data == "prices":
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("🦷 Чистка",     callback_data="price_clean"),
            InlineKeyboardButton("💊 Лечение",    callback_data="price_treat")
        )
        markup.row(
            InlineKeyboardButton("🔩 Имплант",    callback_data="price_implant"),
            InlineKeyboardButton("✨ Отбеливание", callback_data="price_white")
        )
        markup.row(InlineKeyboardButton("◀️ Назад", callback_data="back"))
        bot.edit_message_text("Выберите услугу:", cid, mid, reply_markup=markup)
 
    elif call.data == "price_clean":
        bot.send_message(cid, "🦷 *Профчистка зубов* — от 3 500 руб\nВключает ультразвук + полировку\n\nЗаписаться на чистку?", parse_mode="Markdown", reply_markup=after_price_menu())
    elif call.data == "price_treat":
        bot.send_message(cid, "💊 *Лечение кариеса* — от 4 500 руб\n\nЗаписаться?", parse_mode="Markdown", reply_markup=after_price_menu())
    elif call.data == "price_implant":
        bot.send_message(cid, "🔩 *Имплантация* — от 35 000 руб\nВключает первичную консультацию\n\nЗаписаться?", parse_mode="Markdown", reply_markup=after_price_menu())
    elif call.data == "price_white":
        bot.send_message(cid, "✨ *Отбеливание* — от 8 000 руб\n\nЗаписаться?", parse_mode="Markdown", reply_markup=after_price_menu())
 
    elif call.data == "address":
        bot.send_message(cid, "📍 *Адрес:* Москва, ул. Примерная, 1\n📞 *Телефон:* +7 (999) 123-45-67\n\n🕐 Пн–Пт: 9:00–21:00\n🕐 Сб–Вс: 10:00–18:00", parse_mode="Markdown", reply_markup=back_to_main())
 
    elif call.data == "book":
        user_state[cid] = "waiting_name"
        booking_data[cid] = {}
        bot.send_message(cid, "Отлично! Оформим запись 📝\n\nШаг 1 из 3 — Как вас зовут?")
 
    elif call.data == "question":
        user_state[cid] = "waiting_question"
        bot.send_message(cid, "Задайте ваш вопрос — отвечу сразу 💬")
 
    elif call.data == "back":
        bot.edit_message_text("Чем могу помочь?", cid, mid, reply_markup=main_menu())
 
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    cid   = message.chat.id
    text  = message.text.strip()
    state = user_state.get(cid)
 
    if state == "waiting_name":
        booking_data[cid]["name"] = text
        user_state[cid] = "waiting_service"
        bot.send_message(cid, f"Приятно познакомиться, {text}! 👋\n\nШаг 2 из 3 — Какая услуга вас интересует?\n(чистка, лечение, имплант, отбеливание, консультация)")
 
    elif state == "waiting_service":
        booking_data[cid]["service"] = text
        user_state[cid] = "waiting_time"
        bot.send_message(cid, "Шаг 3 из 3 — Когда вам удобно? 🗓\n(например: завтра утром, в пятницу после 18:00)")
 
    elif state == "waiting_time":
        booking_data[cid]["time"] = text
        data = booking_data[cid]
        user_state.pop(cid, None)
        booking_data.pop(cid, None)
        bot.send_message(cid,
            f"✅ Заявка принята!\n\n👤 Имя: {data.get('name')}\n🦷 Услуга: {data.get('service')}\n🕐 Время: {text}\n\nМы свяжемся с вами для подтверждения.",
            reply_markup=main_menu())
        notify_admin(f"🔔 НОВАЯ ЗАЯВКА\n\n👤 {data.get('name')}\n🦷 {data.get('service')}\n🕐 {text}\n💬 Chat ID: {cid}")
 
    elif state == "waiting_question":
        user_state.pop(cid, None)
        _ai_respond(cid, text)
    else:
        _ai_respond(cid, text)
 
def _ai_respond(cid, text):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": CLINIC_INFO},
                {"role": "user",   "content": text}
            ]
        )
        answer = response.choices[0].message.content
        if "администратор" in answer.lower():
            notify_admin(f"❓ ВОПРОС К АДМИНУ\n\n💬 {text}\n🤖 {answer}\n📌 Chat ID: {cid}")
        bot.send_message(cid, answer, reply_markup=main_menu())
    except Exception:
        bot.send_message(cid, "Произошла ошибка. Попробуйте ещё раз или нажмите /start", reply_markup=main_menu())
 
app = Flask(__name__)
 
@app.route("/")
def home():
    return "Bot is running ✅"
 
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
 
if __name__ == "__main__":
    bot.remove_webhook()
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("Bot started ✅")
    bot.polling(none_stop=True, interval=1)
