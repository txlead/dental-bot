import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI
import httpx
import os
import threading
from flask import Flask

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
OPENAI_KEY    = os.environ.get("OPENAI_KEY", "")
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
1. Если клиент хочет записаться — скажи что оформим запись через кнопку "Записаться".
2. Если вопрос про что-то чего нет в прайсе — скажи: "Этот вопрос я передал администратору — вам ответят в течение 2 часов."
3. НИКОГДА не говори просто "уточню у администратора" без срока ответа.
4. Не придумывай цены которых нет в списке выше.
5. Никогда не проси номер телефона.
"""

# Состояния: None / "waiting_name" / "waiting_service" / "waiting_time"
user_state   = {}
booking_data = {}

SERVICES = {
    "price_clean":   "Профчистка зубов",
    "price_treat":   "Лечение кариеса",
    "price_implant": "Имплантация",
    "price_white":   "Отбеливание",
}

# ─── КЛАВИАТУРЫ ───────────────────────────────────────────────────────────────
def main_menu():
    m = InlineKeyboardMarkup()
    m.row(
        InlineKeyboardButton("💰 Цены",       callback_data="prices"),
        InlineKeyboardButton("📅 Записаться", callback_data="book")
    )
    m.row(
        InlineKeyboardButton("📍 Адрес",  callback_data="address"),
        InlineKeyboardButton("❓ Вопрос", callback_data="question")
    )
    return m

def prices_menu():
    m = InlineKeyboardMarkup()
    m.row(
        InlineKeyboardButton("🦷 Чистка",      callback_data="price_clean"),
        InlineKeyboardButton("💊 Лечение",     callback_data="price_treat")
    )
    m.row(
        InlineKeyboardButton("🔩 Имплант",     callback_data="price_implant"),
        InlineKeyboardButton("✨ Отбеливание", callback_data="price_white")
    )
    m.row(InlineKeyboardButton("◀️ Назад", callback_data="back_to_start"))
    return m

def after_price_menu(service_key):
    m = InlineKeyboardMarkup()
    m.row(
        InlineKeyboardButton("📅 Записаться", callback_data=f"book_service:{service_key}"),
        InlineKeyboardButton("◀️ Назад",      callback_data="prices")
    )
    return m

def only_back():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_start"))
    return m

def cancel_booking_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("❌ Отменить запись", callback_data="cancel_booking"))
    return m

# ─── УВЕДОМЛЕНИЕ АДМИНИСТРАТОРА ───────────────────────────────────────────────
def notify_admin(text):
    if ADMIN_CHAT_ID:
        try:
            bot.send_message(ADMIN_CHAT_ID, text)
        except Exception:
            pass

# ─── /start ───────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def start(message):
    user_state.pop(message.chat.id, None)
    booking_data.pop(message.chat.id, None)
    bot.send_message(
        message.chat.id,
        "Здравствуйте! Я AI-ассистент стоматологической клиники 🦷\n\nЧем могу помочь?",
        reply_markup=main_menu()
    )

# ─── КНОПКИ ───────────────────────────────────────────────────────────────────
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    cid = call.message.chat.id
    mid = call.message.message_id
    bot.answer_callback_query(call.id)
    data = call.data

    # Сброс состояния при навигации
    if data in ("back_to_start", "prices", "address", "question", "book"):
        user_state.pop(cid, None)
        booking_data.pop(cid, None)

    if data == "back_to_start":
        try:
            bot.edit_message_text("Чем могу помочь?", cid, mid, reply_markup=main_menu())
        except Exception:
            bot.send_message(cid, "Чем могу помочь?", reply_markup=main_menu())

    elif data == "prices":
        try:
            bot.edit_message_text("Выберите услугу:", cid, mid, reply_markup=prices_menu())
        except Exception:
            bot.send_message(cid, "Выберите услугу:", reply_markup=prices_menu())

    elif data in SERVICES:
        service_name = SERVICES[data]
        prices = {
            "price_clean":   "🦷 *Профчистка зубов* — от 3 500 руб\nВключает ультразвук + полировку",
            "price_treat":   "💊 *Лечение кариеса* — от 4 500 руб",
            "price_implant": "🔩 *Имплантация* — от 35 000 руб\nВключает первичную консультацию",
            "price_white":   "✨ *Отбеливание* — от 8 000 руб",
        }
        bot.send_message(cid,
            f"{prices[data]}\n\nЗаписаться на {service_name.lower()}?",
            parse_mode="Markdown",
            reply_markup=after_price_menu(data))

    elif data.startswith("book_service:"):
        # Записаться сразу с выбранной услугой
        service_key = data.split(":")[1]
        service_name = SERVICES.get(service_key, "")
        user_state[cid]   = "waiting_name"
        booking_data[cid] = {"service": service_name}
        bot.send_message(cid,
            f"Отлично! Записываем вас на *{service_name.lower()}* 📝\n\n"
            f"Шаг 1 из 2 — Как вас зовут?",
            parse_mode="Markdown",
            reply_markup=cancel_booking_menu())

    elif data == "book":
        user_state[cid]   = "waiting_name"
        booking_data[cid] = {}
        bot.send_message(cid,
            "Отлично! Оформим запись 📝\n\nШаг 1 из 3 — Как вас зовут?",
            reply_markup=cancel_booking_menu())

    elif data == "cancel_booking":
        user_state.pop(cid, None)
        booking_data.pop(cid, None)
        bot.send_message(cid, "Запись отменена. Чем могу помочь?", reply_markup=main_menu())

    elif data == "address":
        bot.send_message(cid,
            "📍 *Адрес:* Москва, ул. Примерная, 1\n"
            "📞 *Телефон:* +7 (999) 123-45-67\n\n"
            "🕐 Пн–Пт: 9:00–21:00\n"
            "🕐 Сб–Вс: 10:00–18:00",
            parse_mode="Markdown",
            reply_markup=only_back())

    elif data == "question":
        user_state[cid] = "waiting_question"
        bot.send_message(cid,
            "Задайте ваш вопрос — отвечу сразу 💬\n"
            "_(или нажмите /start чтобы вернуться в меню)_",
            parse_mode="Markdown")

# ─── ТЕКСТОВЫЕ СООБЩЕНИЯ ──────────────────────────────────────────────────────
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    cid   = message.chat.id
    text  = message.text.strip()
    state = user_state.get(cid)

    # ── Шаг: имя ──────────────────────────────────────────────────────────────
    if state == "waiting_name":
        # Простая валидация — имя не должно быть длиннее 30 символов
        # и не должно содержать цифры
        if len(text) > 30 or any(c.isdigit() for c in text):
            bot.send_message(cid,
                "Пожалуйста, введите ваше имя 😊",
                reply_markup=cancel_booking_menu())
            return

        booking_data[cid]["name"] = text
        service = booking_data[cid].get("service")

        if service:
            # Услуга уже выбрана — пропускаем шаг 2
            user_state[cid] = "waiting_time"
            bot.send_message(cid,
                f"Приятно познакомиться, {text}! 👋\n\n"
                f"Шаг 2 из 2 — Когда вам удобно? 🗓\n"
                f"_(например: завтра утром, в пятницу после 18:00)_",
                parse_mode="Markdown",
                reply_markup=cancel_booking_menu())
        else:
            user_state[cid] = "waiting_service"
            bot.send_message(cid,
                f"Приятно познакомиться, {text}! 👋\n\n"
                f"Шаг 2 из 3 — Какая услуга вас интересует?\n"
                f"_(чистка, лечение, имплант, отбеливание, консультация)_",
                parse_mode="Markdown",
                reply_markup=cancel_booking_menu())

    # ── Шаг: услуга ───────────────────────────────────────────────────────────
    elif state == "waiting_service":
        booking_data[cid]["service"] = text
        user_state[cid] = "waiting_time"
        bot.send_message(cid,
            "Шаг 3 из 3 — Когда вам удобно? 🗓\n"
            "_(например: завтра утром, в пятницу после 18:00)_",
            parse_mode="Markdown",
            reply_markup=cancel_booking_menu())

    # ── Шаг: время ────────────────────────────────────────────────────────────
    elif state == "waiting_time":
        booking_data[cid]["time"] = text
        d = booking_data[cid]
        user_state.pop(cid, None)
        booking_data.pop(cid, None)

        bot.send_message(cid,
            f"✅ *Заявка принята!*\n\n"
            f"👤 Имя: {d.get('name')}\n"
            f"🦷 Услуга: {d.get('service')}\n"
            f"🕐 Время: {text}\n\n"
            f"Администратор свяжется с вами в ближайшее время для подтверждения.",
            parse_mode="Markdown",
            reply_markup=only_back())

        notify_admin(
            f"🔔 НОВАЯ ЗАЯВКА НА ЗАПИСЬ\n\n"
            f"👤 Имя: {d.get('name')}\n"
            f"🦷 Услуга: {d.get('service')}\n"
            f"🕐 Время: {text}\n"
            f"💬 Chat ID: {cid}"
        )

    # ── Вопрос через кнопку ───────────────────────────────────────────────────
    elif state == "waiting_question":
        user_state.pop(cid, None)
        _ai_respond(cid, text)

    # ── Любое другое сообщение ────────────────────────────────────────────────
    else:
        _ai_respond(cid, text)

# ─── AI ОТВЕТ ─────────────────────────────────────────────────────────────────
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
            notify_admin(
                f"❓ ВОПРОС К АДМИНИСТРАТОРУ\n\n"
                f"💬 Клиент: {text}\n"
                f"🤖 Ответ бота: {answer}\n"
                f"📌 Chat ID: {cid}"
            )
        bot.send_message(cid, answer, reply_markup=main_menu())
    except Exception:
        bot.send_message(cid,
            "Произошла ошибка. Попробуйте ещё раз или нажмите /start",
            reply_markup=main_menu())

# ─── FLASK (Render не засыпает) ───────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running ✅"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ─── ЗАПУСК ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.remove_webhook()
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Bot started ✅")
    bot.polling(none_stop=True, interval=1)
