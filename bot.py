import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI
import httpx
import os
import threading
import json
from flask import Flask

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
OPENAI_KEY    = os.environ.get("OPENAI_KEY", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

bot    = telebot.TeleBot(BOT_TOKEN)
client = OpenAI(api_key=OPENAI_KEY, http_client=httpx.Client())

CLINIC_INFO = """
Ты вежливый AI-ассистент стоматологической клиники. Отвечай ТОЛЬКО на русском языке.
Отвечай кратко и по делу — максимум 3-4 предложения.

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

BOOKING_DETECT_PROMPT = """
Ты анализируешь сообщение клиента стоматологии.
Определи: содержит ли сообщение готовую заявку (имя + время)?
Режим работы: пн-пт 9:00-21:00, сб-вс 10:00-18:00.

Ответь ТОЛЬКО валидным JSON без markdown и без ```:
{"is_booking": true/false, "name": "имя или null", "time": "время или null", "time_valid": true/false, "time_issue": "проблема или null"}

Примеры:
"Саша хочет завтра утром" → {"is_booking":true,"name":"Саша","time":"завтра утром","time_valid":true,"time_issue":null}
"запишите Анну в воскресенье в 22:00" → {"is_booking":true,"name":"Анна","time":"воскресенье 22:00","time_valid":false,"time_issue":"В воскресенье работаем только до 18:00"}
"сколько стоит чистка" → {"is_booking":false,"name":null,"time":null,"time_valid":true,"time_issue":null}
"привет" → {"is_booking":false,"name":null,"time":null,"time_valid":true,"time_issue":null}
"""

TIME_CHECK_PROMPT = """
Проверь время записи. Режим работы: пн-пт 9:00-21:00, сб-вс 10:00-18:00.
Ответь ТОЛЬКО валидным JSON без markdown:
{"valid": true/false, "issue": "описание проблемы или null"}
Если время неопределённое (завтра, на следующей неделе, утром) — valid:true.
"""

user_state   = {}
booking_data = {}

SERVICES_MAP = {
    "select_clean":   "Профчистка зубов",
    "select_treat":   "Лечение кариеса",
    "select_implant": "Имплантация",
    "select_white":   "Отбеливание",
    "select_consult": "Консультация (бесплатно)",
    "price_clean":    "Профчистка зубов",
    "price_treat":    "Лечение кариеса",
    "price_implant":  "Имплантация",
    "price_white":    "Отбеливание",
}

PRICES_TEXT = {
    "price_clean":   "🦷 *Профчистка зубов* — от 3 500 руб\nВключает ультразвук + полировку",
    "price_treat":   "💊 *Лечение кариеса* — от 4 500 руб",
    "price_implant": "🔩 *Имплантация* — от 35 000 руб\nВключает первичную консультацию",
    "price_white":   "✨ *Отбеливание* — от 8 000 руб",
}

def main_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("💰 Цены", callback_data="prices"),
          InlineKeyboardButton("📅 Записаться", callback_data="book"))
    m.row(InlineKeyboardButton("📍 Адрес", callback_data="address"),
          InlineKeyboardButton("❓ Вопрос", callback_data="question"))
    return m

def prices_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🦷 Чистка", callback_data="price_clean"),
          InlineKeyboardButton("💊 Лечение", callback_data="price_treat"))
    m.row(InlineKeyboardButton("🔩 Имплант", callback_data="price_implant"),
          InlineKeyboardButton("✨ Отбеливание", callback_data="price_white"))
    m.row(InlineKeyboardButton("◀️ Назад", callback_data="back_to_start"))
    return m

def after_price_menu(key):
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("📅 Записаться", callback_data=f"book_service:{key}"),
          InlineKeyboardButton("◀️ Назад", callback_data="prices"))
    return m

def service_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🦷 Чистка", callback_data="select_clean"),
          InlineKeyboardButton("💊 Лечение", callback_data="select_treat"))
    m.row(InlineKeyboardButton("🔩 Имплант", callback_data="select_implant"),
          InlineKeyboardButton("✨ Отбеливание", callback_data="select_white"))
    m.row(InlineKeyboardButton("📋 Консультация", callback_data="select_consult"))
    m.row(InlineKeyboardButton("❌ Отменить", callback_data="cancel_booking"))
    return m

def cancel_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("❌ Отменить запись", callback_data="cancel_booking"))
    return m

def only_back():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_start"))
    return m

def notify_admin(text):
    if ADMIN_CHAT_ID:
        try:
            bot.send_message(ADMIN_CHAT_ID, text)
        except Exception:
            pass

def confirm_booking(cid, name, service, time_str):
    bot.send_message(cid,
        f"✅ *Заявка принята!*\n\n"
        f"👤 Имя: {name}\n"
        f"🦷 Услуга: {service}\n"
        f"🕐 Время: {time_str}\n\n"
        f"Администратор свяжется с вами в ближайшее время для подтверждения.",
        parse_mode="Markdown", reply_markup=only_back())
    notify_admin(f"🔔 НОВАЯ ЗАЯВКА\n\n👤 {name}\n🦷 {service}\n🕐 {time_str}\n💬 Chat ID: {cid}")

def safe_parse_json(text):
    try:
        text = text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception:
        return None

def check_time(time_str):
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": TIME_CHECK_PROMPT},
                      {"role": "user", "content": f"время: {time_str}"}],
            max_tokens=80)
        result = safe_parse_json(r.choices[0].message.content)
        return result if result else {"valid": True, "issue": None}
    except Exception:
        return {"valid": True, "issue": None}

@bot.message_handler(commands=["start"])
def start(message):
    user_state.pop(message.chat.id, None)
    booking_data.pop(message.chat.id, None)
    bot.send_message(message.chat.id,
        "Здравствуйте! Я AI-ассистент стоматологической клиники 🦷\n\nЧем могу помочь?",
        reply_markup=main_menu())

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    cid = call.message.chat.id
    mid = call.message.message_id
    bot.answer_callback_query(call.id)
    data = call.data

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

    elif data in PRICES_TEXT:
        bot.send_message(cid, f"{PRICES_TEXT[data]}\n\nЗаписаться?",
            parse_mode="Markdown", reply_markup=after_price_menu(data))

    elif data.startswith("book_service:"):
        key = data.split(":")[1]
        service = SERVICES_MAP.get(key, "")
        user_state[cid] = "waiting_name"
        booking_data[cid] = {"service": service}
        bot.send_message(cid,
            f"Записываем на *{service.lower()}* 📝\n\nКак вас зовут?",
            parse_mode="Markdown", reply_markup=cancel_menu())

    elif data in ("select_clean","select_treat","select_implant","select_white","select_consult"):
        service = SERVICES_MAP.get(data, "")
        booking_data[cid]["service"] = service
        user_state[cid] = "waiting_time"
        bot.send_message(cid,
            f"Отлично! Записываем на *{service.lower()}* ✅\n\n"
            f"Когда вам удобно прийти? 🗓\n"
            f"_(например: завтра утром, пятница после 18:00)_\n\n"
            f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00",
            parse_mode="Markdown", reply_markup=cancel_menu())

    elif data == "book":
        user_state[cid] = "waiting_name"
        booking_data[cid] = {}
        bot.send_message(cid, "Оформим запись 📝\n\nКак вас зовут?", reply_markup=cancel_menu())

    elif data == "cancel_booking":
        user_state.pop(cid, None)
        booking_data.pop(cid, None)
        bot.send_message(cid, "Запись отменена. Чем могу помочь?", reply_markup=main_menu())

    elif data == "address":
        bot.send_message(cid,
            "📍 *Адрес:* Москва, ул. Примерная, 1\n"
            "📞 *Телефон:* +7 (999) 123-45-67\n\n"
            "🕐 Пн–Пт: 9:00–21:00\n🕐 Сб–Вс: 10:00–18:00",
            parse_mode="Markdown", reply_markup=only_back())

    elif data == "question":
        user_state[cid] = "waiting_question"
        bot.send_message(cid, "Задайте ваш вопрос — отвечу сразу 💬")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    cid   = message.chat.id
    text  = message.text.strip()
    state = user_state.get(cid)

    if state == "waiting_name":
        if len(text) > 40 or any(c.isdigit() for c in text):
            bot.send_message(cid, "Пожалуйста, введите ваше имя 😊", reply_markup=cancel_menu())
            return
        booking_data[cid]["name"] = text
        service = booking_data[cid].get("service")
        if service:
            user_state[cid] = "waiting_time"
            bot.send_message(cid,
                f"Приятно познакомиться, {text}! 👋\n\n"
                f"Когда вам удобно прийти? 🗓\n"
                f"_(например: завтра утром, пятница после 18:00)_\n\n"
                f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00",
                parse_mode="Markdown", reply_markup=cancel_menu())
        else:
            user_state[cid] = "waiting_service"
            bot.send_message(cid,
                f"Приятно познакомиться, {text}! 👋\n\nВыберите услугу:",
                reply_markup=service_menu())

    elif state == "waiting_service":
        # Текстовый ввод услуги — мягко направляем на кнопки
        bot.send_message(cid,
            "Пожалуйста, выберите услугу из списка 👇",
            reply_markup=service_menu())

    elif state == "waiting_time":
        t = check_time(text)
        if not t.get("valid", True):
            bot.send_message(cid,
                f"⚠️ К сожалению, это время не подходит.\n"
                f"{t.get('issue', '')}\n\n"
                f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00\n\n"
                f"Пожалуйста, выберите другое время:",
                reply_markup=cancel_menu())
            return
        d = booking_data[cid]
        user_state.pop(cid, None)
        booking_data.pop(cid, None)
        confirm_booking(cid, d.get("name"), d.get("service"), text)

    elif state == "waiting_question":
        user_state.pop(cid, None)
        _ai_respond(cid, text)

    else:
        # Проверяем умную заявку
        try:
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": BOOKING_DETECT_PROMPT},
                          {"role": "user", "content": text}],
                max_tokens=150)
            booking = safe_parse_json(r.choices[0].message.content)
        except Exception:
            booking = None

        if booking and booking.get("is_booking"):
            name = booking.get("name")
            time_str = booking.get("time")
            valid = booking.get("time_valid", True)
            issue = booking.get("time_issue")

            if not valid:
                bot.send_message(cid,
                    f"⚠️ {issue}\n\n"
                    f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00\n\n"
                    f"Пожалуйста, уточните удобное время:",
                    reply_markup=cancel_menu())
                user_state[cid] = "waiting_time"
                booking_data[cid] = {"name": name, "service": None}
                return

            if name and time_str:
                # Есть имя и время — спрашиваем услугу кнопками
                booking_data[cid] = {"name": name, "time_pending": time_str}
                user_state[cid] = "waiting_service"
                bot.send_message(cid,
                    f"Отлично, {name}! Время: {time_str} ✅\n\nВыберите услугу:",
                    reply_markup=service_menu())
            elif name:
                booking_data[cid] = {"name": name}
                user_state[cid] = "waiting_service"
                bot.send_message(cid,
                    f"Приятно познакомиться, {name}! 👋\n\nВыберите услугу:",
                    reply_markup=service_menu())
            else:
                _ai_respond(cid, text)
        else:
            _ai_respond(cid, text)

def _ai_respond(cid, text):
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": CLINIC_INFO},
                      {"role": "user", "content": text}])
        answer = r.choices[0].message.content
        if "администратор" in answer.lower():
            notify_admin(f"❓ ВОПРОС К АДМИНИСТРАТОРУ\n\n💬 {text}\n🤖 {answer}\n📌 {cid}")
        bot.send_message(cid, answer, reply_markup=main_menu())
    except Exception:
        bot.send_message(cid, "Произошла ошибка. Попробуйте ещё раз или нажмите /start",
            reply_markup=main_menu())

# ── Кнопки выбора услуги во время waiting_service ─────────────────────────────
@bot.callback_query_handler(func=lambda call: call.data in (
    "select_clean","select_treat","select_implant","select_white","select_consult"
) and False)  # handled above
def _dummy(call): pass

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running ✅"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    bot.remove_webhook()
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Bot started ✅")
    bot.polling(none_stop=True, interval=1)
