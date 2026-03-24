import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI
import httpx
import os
import threading
import json
import re
from flask import Flask

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "")
OPENAI_KEY    = os.environ.get("OPENAI_KEY", "")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
RENDER_URL    = os.environ.get("RENDER_URL", "https://dental-bot-p20r.onrender.com")

bot    = telebot.TeleBot(BOT_TOKEN)
client = OpenAI(api_key=OPENAI_KEY, http_client=httpx.Client())

# ─────────────────────────────────────────────────────────────────
# ПРОМПТЫ
# ─────────────────────────────────────────────────────────────────

CLINIC_INFO = """
Ты дружелюбный и умный AI-ассистент стоматологической клиники. Отвечай ТОЛЬКО на русском языке.
Отвечай тепло, естественно и по делу — как живой человек, не как робот. Максимум 3-4 предложения.

Услуги и цены:
- Консультация: бесплатно
- Профчистка зубов: от 3 500 руб (включает ультразвук + полировку)
- Лечение кариеса: от 4 500 руб
- Имплантация: от 35 000 руб (включает консультацию). ТОЛЬКО для взрослых 18+
- Отбеливание: от 8 000 руб
- Брекеты / коррекция прикуса / коррекция брекетов: от 45 000 руб (курс). ТОЛЬКО для взрослых 18+
- Виниры: от 15 000 руб за зуб
- Удаление зуба: от 2 500 руб
- Детская стоматология: с 3 лет — осмотр и лечение молочных зубов

Режим работы: пн-пт 9:00–21:00, сб-вс 10:00–18:00
Адрес: Москва, ул. Примерная, 1 (5 мин от метро Примерная)
Телефон: +7 (999) 123-45-67

Правила:
1. Если клиент хочет записаться — скажи что оформим запись и предложи нажать "Записаться".
2. Если вопрос про услугу которой нет в прайсе — скажи: "Этот вопрос я передал администратору — вам ответят в течение 2 часов 😊"
3. Если спрашивают про ребёнка и взрослую процедуру (импланты, брекеты) — объясни логично почему нельзя и предложи альтернативу.
4. НИКОГДА не говори просто "уточню у администратора" без срока ответа.
5. Не придумывай цены которых нет в списке выше.
6. На грубость отвечай спокойно и с лёгким юмором.
7. Всегда заканчивай предложением действия.
"""

BOOKING_DETECT_PROMPT = """
Ты анализируешь сообщение клиента стоматологии.
Режим работы: пн-пт 9:00-21:00, сб-вс 10:00-18:00.

Ответь ТОЛЬКО валидным JSON без markdown:
{"is_booking":true/false,"name":"имя или null","time":"время или null","service_hint":"услуга или null","time_valid":true/false,"time_issue":"проблема или null"}

Примеры:
"Саша хочет завтра утром" -> {"is_booking":true,"name":"Саша","time":"завтра утром","service_hint":null,"time_valid":true,"time_issue":null}
"запишите на коррекцию брекетов" -> {"is_booking":true,"name":null,"time":null,"service_hint":"коррекция брекетов","time_valid":true,"time_issue":null}
"можно записаться на чистку в субботу в 14:00" -> {"is_booking":true,"name":null,"time":"суббота 14:00","service_hint":"чистка","time_valid":true,"time_issue":null}
"запишите Анну в воскресенье в 22:00" -> {"is_booking":true,"name":"Анна","time":"воскресенье 22:00","service_hint":null,"time_valid":false,"time_issue":"В воскресенье работаем только до 18:00"}
"сколько стоит чистка" -> {"is_booking":false,"name":null,"time":null,"service_hint":null,"time_valid":true,"time_issue":null}

is_booking:true ТОЛЬКО если клиент явно хочет записаться: "запишите", "хочу записаться", "можно записаться на..." и т.д.
is_booking:false если это вопрос о цене, адресе, услугах, или упоминается ребёнок/родственник в контексте "можно ли" (это вопрос, не запись).
Примеры is_booking:false: "а брата можно на имплант", "можно ли ребёнку", "сколько стоит", "а можно так".
"""

TIME_CHECK_PROMPT = """
Проверь время для записи к врачу.
Режим работы: пн-пт 9:00-21:00, сб-вс 10:00-18:00.
Ответь ТОЛЬКО JSON без markdown:
{"valid":true/false,"issue":"проблема или null"}

Правила — будь максимально лояльным:
- ЛЮБОЕ приблизительное время → valid:true. Примеры которые ВСЕГДА valid:true:
  "завтра", "утром", "вечером", "в обед", "завтра в обед", "завтра утром", "завтра вечером",
  "на следующей неделе", "в пятницу", "в субботу", "после обеда", "ближайшее время",
  "сегодня", "послезавтра", "на этой неделе", "в любое время"
- valid:false ТОЛЬКО если конкретное время явно вне работы: например "воскресенье в 23:00", "понедельник в 7:00"
- Бессмысленное ("никогда", "не знаю", случайные буквы) → valid:false
- Если сомневаешься — всегда ставь valid:true
"""

# ─────────────────────────────────────────────────────────────────
# ДАННЫЕ
# ─────────────────────────────────────────────────────────────────

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

# Правильные падежи для "записываем на ЧТО?"
SERVICE_ACCUSATIVE = {
    "Профчистка зубов":         "профчистку зубов",
    "Лечение кариеса":          "лечение кариеса",
    "Имплантация":              "имплантацию",
    "Отбеливание":              "отбеливание",
    "Консультация (бесплатно)": "консультацию",
}

def service_accusative(service: str) -> str:
    return SERVICE_ACCUSATIVE.get(service, service.lower())

# ─────────────────────────────────────────────────────────────────
# КЛАВИАТУРЫ
# ─────────────────────────────────────────────────────────────────

def main_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("💰 Цены",       callback_data="prices"),
          InlineKeyboardButton("📅 Записаться", callback_data="book"))
    m.row(InlineKeyboardButton("📍 Адрес",      callback_data="address"),
          InlineKeyboardButton("❓ Вопрос",     callback_data="question"))
    return m

def prices_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🦷 Чистка",  callback_data="price_clean"),
          InlineKeyboardButton("💊 Лечение",  callback_data="price_treat"))
    m.row(InlineKeyboardButton("🔩 Имплант",  callback_data="price_implant"),
          InlineKeyboardButton("✨ Отбелить", callback_data="price_white"))
    m.row(InlineKeyboardButton("◀️ Назад",   callback_data="back_to_start"))
    return m

def after_price_menu(key):
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("📅 Записаться", callback_data=f"book_service:{key}"),
          InlineKeyboardButton("◀️ Назад",      callback_data="prices"))
    return m

def service_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("🦷 Чистка",     callback_data="select_clean"),
          InlineKeyboardButton("💊 Лечение",     callback_data="select_treat"))
    m.row(InlineKeyboardButton("🔩 Имплант",      callback_data="select_implant"),
          InlineKeyboardButton("✨ Отбеливание",  callback_data="select_white"))
    m.row(InlineKeyboardButton("📋 Консультация", callback_data="select_consult"))
    m.row(InlineKeyboardButton("❌ Отменить",      callback_data="cancel_booking"))
    return m

def cancel_menu():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("❌ Отменить запись", callback_data="cancel_booking"))
    return m

def only_back():
    m = InlineKeyboardMarkup()
    m.row(InlineKeyboardButton("◀️ В главное меню", callback_data="back_to_start"))
    return m

# ─────────────────────────────────────────────────────────────────
# УТИЛИТЫ
# ─────────────────────────────────────────────────────────────────

def notify_admin(text: str):
    if ADMIN_CHAT_ID:
        try:
            bot.send_message(ADMIN_CHAT_ID, text, parse_mode="Markdown")
        except Exception:
            pass

def confirm_booking(cid, name, phone, service, time_str):
    phone_line = f"📞 Телефон: {phone}\n" if phone else ""
    bot.send_message(
        cid,
        f"✅ *Заявка принята!*\n\n"
        f"👤 Имя: {name}\n"
        f"{phone_line}"
        f"🦷 Услуга: {service}\n"
        f"🕐 Время: {time_str}\n\n"
        f"Администратор свяжется с вами в ближайшее время для подтверждения.",
        parse_mode="Markdown",
        reply_markup=only_back(),
    )
    notify_admin(
        f"🔔 *НОВАЯ ЗАЯВКА*\n\n"
        f"👤 {name}\n"
        f"📞 {phone or '—'}\n"
        f"🦷 {service}\n"
        f"🕐 {time_str}\n"
        f"💬 Chat ID: `{cid}`"
    )

def safe_parse_json(text: str):
    try:
        text = text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception:
        return None

def check_time(time_str: str) -> dict:
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": TIME_CHECK_PROMPT},
                      {"role": "user",   "content": f"время: {time_str}"}],
            max_tokens=80,
        )
        result = safe_parse_json(r.choices[0].message.content)
        return result if result else {"valid": True, "issue": None}
    except Exception:
        return {"valid": True, "issue": None}

def validate_phone(text: str) -> bool:
    cleaned = re.sub(r"[\s\-\(\)]", "", text)
    return bool(re.match(r"^[\+\d]{7,15}$", cleaned))

# ─────────────────────────────────────────────────────────────────
# ХЕНДЛЕРЫ
# ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def start(message):
    user_state.pop(message.chat.id, None)
    booking_data.pop(message.chat.id, None)
    bot.send_message(
        message.chat.id,
        "Здравствуйте! Я AI-ассистент стоматологической клиники 🦷\n\nЧем могу помочь?",
        reply_markup=main_menu(),
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    cid  = call.message.chat.id
    mid  = call.message.message_id
    data = call.data
    bot.answer_callback_query(call.id)

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
        key     = data.split(":")[1]
        service = SERVICES_MAP.get(key, "")
        booking_data[cid] = {"service": service}
        user_state[cid]   = "waiting_name"
        bot.send_message(
            cid,
            f"Записываем на *{service_accusative(service)}* 📝\n\nКак вас зовут?",
            parse_mode="Markdown", reply_markup=cancel_menu(),
        )

    elif data in ("select_clean","select_treat","select_implant","select_white","select_consult"):
        service = SERVICES_MAP.get(data, "")
        if cid not in booking_data:
            booking_data[cid] = {}
        booking_data[cid]["service"] = service
        if booking_data[cid].get("name") and booking_data[cid].get("phone"):
            # имя и телефон уже есть (пришёл через умный роутинг) — сразу к времени
            user_state[cid] = "waiting_time"
            bot.send_message(
                cid,
                f"Отлично! Записываем на *{service_accusative(service)}* ✅\n\n"
                f"Когда вам удобно прийти? 🗓\n"
                f"_(например: завтра утром, пятница после 18:00)_\n\n"
                f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00",
                parse_mode="Markdown", reply_markup=cancel_menu(),
            )
        elif booking_data[cid].get("name"):
            # имя есть, телефона нет — спрашиваем телефон
            user_state[cid] = "waiting_phone"
            bot.send_message(
                cid,
                f"Отлично! Записываем на *{service_accusative(service)}* ✅\n\n"
                f"Ваш номер телефона для подтверждения:",
                parse_mode="Markdown", reply_markup=cancel_menu(),
            )
        else:
            # нет ни имени ни телефона — начинаем с имени
            user_state[cid] = "waiting_name"
            bot.send_message(
                cid,
                f"Отлично! Записываем на *{service_accusative(service)}* ✅\n\nКак вас зовут?",
                parse_mode="Markdown", reply_markup=cancel_menu(),
            )

    elif data == "book":
        booking_data[cid] = {}
        user_state[cid]   = "waiting_name"
        bot.send_message(cid, "Оформим запись 📝\n\nКак вас зовут?", reply_markup=cancel_menu())

    elif data == "cancel_booking":
        user_state.pop(cid, None)
        booking_data.pop(cid, None)
        bot.send_message(cid, "Запись отменена. Чем могу помочь?", reply_markup=main_menu())

    elif data == "address":
        bot.send_message(
            cid,
            "📍 *Адрес:* Москва, ул. Примерная, 1\n"
            "🚇 5 минут от метро Примерная\n"
            "📞 *Телефон:* +7 (999) 123-45-67\n\n"
            "🕐 Пн–Пт: 9:00–21:00\n🕐 Сб–Вс: 10:00–18:00",
            parse_mode="Markdown", reply_markup=only_back(),
        )

    elif data == "question":
        user_state[cid] = "waiting_question"
        bot.send_message(cid, "Задайте ваш вопрос — отвечу сразу 💬")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    cid   = message.chat.id
    text  = message.text.strip()
    state = user_state.get(cid)

    # ── Шаг: имя ────────────────────────────────────────────────
    if state == "waiting_name":
        if len(text) > 50 or any(c.isdigit() for c in text):
            bot.send_message(cid, "Пожалуйста, введите ваше имя 😊", reply_markup=cancel_menu())
            return
        booking_data[cid]["name"] = text
        user_state[cid] = "waiting_phone"
        bot.send_message(
            cid,
            f"Приятно познакомиться, {text}! 👋\n\n"
            f"Ваш номер телефона для подтверждения записи:",
            reply_markup=cancel_menu(),
        )

    # ── Шаг: телефон ────────────────────────────────────────────
    elif state == "waiting_phone":
        # Клиент говорит что нет номера — принимаем без телефона
        no_phone_signals = ["нет номера", "нету номера", "нет телефона", "без номера",
                            "не хочу", "не буду", "пропустить", "не дам", "нет"]
        if any(sig in text.lower() for sig in no_phone_signals):
            booking_data[cid]["phone"] = "не указан"
            text = "не указан"  # продолжаем как будто ввёл
        elif not validate_phone(text):
            bot.send_message(
                cid,
                "Пожалуйста, введите номер телефона 📞 или напишите «нет» чтобы пропустить\n"
                "Например: +7 999 123-45-67",
                reply_markup=cancel_menu(),
            )
            return
        booking_data[cid]["phone"] = text
        service      = booking_data[cid].get("service")
        time_pending = booking_data[cid].get("time_pending")

        if service and time_pending:
            # время уже есть из умного роутинга — не спрашиваем повторно
            d = booking_data.pop(cid)
            user_state.pop(cid, None)
            confirm_booking(cid, d.get("name","—"), d.get("phone",""), d.get("service","Консультация"), time_pending)
        elif service:
            user_state[cid] = "waiting_time"
            bot.send_message(
                cid,
                f"Отлично! Записываем на *{service_accusative(service)}* ✅\n\n"
                f"Когда вам удобно прийти? 🗓\n"
                f"_(например: завтра утром, пятница после 18:00)_\n\n"
                f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00",
                parse_mode="Markdown", reply_markup=cancel_menu(),
            )
        else:
            user_state[cid] = "waiting_service"
            bot.send_message(cid, "Выберите услугу:", reply_markup=service_menu())

    # ── Шаг: выбор услуги текстом ───────────────────────────────
    elif state == "waiting_service":
        # Клиент написал услугу текстом — пробуем распознать через GPT
        hint = text.lower()
        matched = None
        for key, svc_name in SERVICES_MAP.items():
            if not key.startswith("select_"):
                continue
            if any(w in hint for w in svc_name.lower().split()):
                matched = svc_name
                break
        if matched:
            booking_data[cid]["service"] = matched
            user_state[cid] = "waiting_time"
            bot.send_message(
                cid,
                f"Отлично! Записываем на *{service_accusative(matched)}* ✅\n\n"
                f"Когда вам удобно прийти? 🗓\n"
                f"_(например: завтра утром, пятница после 18:00)_\n\n"
                f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00",
                parse_mode="Markdown", reply_markup=cancel_menu(),
            )
        else:
            # Нестандартная услуга — GPT ответит и уведомит админа
            SERVICE_ACCUSATIVE[text.capitalize()] = text.lower()
            booking_data[cid]["service"] = text.capitalize()
            user_state[cid] = "waiting_time"
            bot.send_message(
                cid,
                f"Записываем на *{text.lower()}* ✅\n\n"
                f"Когда вам удобно прийти? 🗓\n"
                f"_(например: завтра утром, пятница после 18:00)_\n\n"
                f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00",
                parse_mode="Markdown", reply_markup=cancel_menu(),
            )
            notify_admin(f"❓ *Нестандартная услуга*\n\nКлиент запросил: {text}\nChat ID: `{cid}`")

    # ── Шаг: время ──────────────────────────────────────────────
    elif state == "waiting_time":
        # Сначала проверяем — может клиент передумал и задаёт вопрос?
        question_signals = [
            "цена", "цены", "сколько", "стоит", "стоимость", "почём",
            "адрес", "где находитесь", "как добраться", "метро",
            "можно ли", "а можно", "подойдёт ли", "брат", "сестра", "ребёнок",
            "дети", "ребёнку", "годик", "лет", "год", "годика",
            "не чистка", "не лечение", "не имплант", "лучше", "вместо",
            "осмотр", "консультация", "другую", "другой", "поменять",
            "нет номера", "нету номера", "вопрос", "спросить",
        ]
        text_lower = text.lower()
        is_question = any(sig in text_lower for sig in question_signals)

        if is_question:
            # Выходим из флоу и отвечаем как на вопрос
            user_state.pop(cid, None)
            booking_data.pop(cid, None)
            _ai_respond(cid, text)
            return

        t = check_time(text)
        if not t.get("valid", True):
            bot.send_message(
                cid,
                f"⚠️ К сожалению, это время не подходит.\n"
                f"{t.get('issue', '')}\n\n"
                f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00\n\n"
                f"Пожалуйста, выберите другое время:",
                reply_markup=cancel_menu(),
            )
            return
        d = booking_data.get(cid, {})
        user_state.pop(cid, None)
        booking_data.pop(cid, None)
        confirm_booking(cid, d.get("name","—"), d.get("phone",""), d.get("service","Консультация"), text)

    # ── Вопрос ──────────────────────────────────────────────────
    elif state == "waiting_question":
        user_state.pop(cid, None)
        _ai_respond(cid, text)

    # ── Умный роутинг ───────────────────────────────────────────
    else:
        try:
            r = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": BOOKING_DETECT_PROMPT},
                          {"role": "user",   "content": text}],
                max_tokens=150,
            )
            booking = safe_parse_json(r.choices[0].message.content)
        except Exception:
            booking = None

        if booking and booking.get("is_booking"):
            name         = booking.get("name")
            time_str     = booking.get("time")
            service_hint = booking.get("service_hint")
            valid        = booking.get("time_valid", True)
            issue        = booking.get("time_issue")

            if not valid:
                bot.send_message(
                    cid,
                    f"⚠️ {issue}\n\n"
                    f"⏰ Пн–Пт: 9:00–21:00 | Сб–Вс: 10:00–18:00\n\n"
                    f"Пожалуйста, уточните удобное время:",
                    reply_markup=cancel_menu(),
                )
                user_state[cid]   = "waiting_time"
                booking_data[cid] = {"name": name, "service": None}
                return

            booking_data[cid] = {}
            if name:
                booking_data[cid]["name"] = name
            if time_str:
                booking_data[cid]["time_pending"] = time_str
            # Если GPT распознал услугу — ищем её в нашем маппинге
            if service_hint:
                hint_lower = service_hint.lower()
                matched = None
                for key, svc_name in SERVICES_MAP.items():
                    if not key.startswith("select_"):
                        continue
                    if any(w in hint_lower for w in svc_name.lower().split()):
                        matched = svc_name
                        break
                # Нестандартная услуга (брекеты и т.д.) — сохраняем как есть
                if not matched:
                    matched = service_hint.capitalize()
                    SERVICE_ACCUSATIVE[matched] = service_hint.lower()
                booking_data[cid]["service"] = matched

            if name:
                user_state[cid] = "waiting_phone"
                bot.send_message(
                    cid,
                    f"Оформим запись, {name}! 📝\n\nВаш номер телефона для подтверждения:",
                    reply_markup=cancel_menu(),
                )
            else:
                user_state[cid] = "waiting_name"
                bot.send_message(cid, "Оформим запись 📝\n\nКак вас зовут?", reply_markup=cancel_menu())
        else:
            _ai_respond(cid, text)

# ─────────────────────────────────────────────────────────────────
# AI ОТВЕТ
# ─────────────────────────────────────────────────────────────────

def _ai_respond(cid, text):
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": CLINIC_INFO},
                      {"role": "user",   "content": text}],
        )
        answer = r.choices[0].message.content
        if "администратор" in answer.lower():
            notify_admin(
                f"❓ *ВОПРОС К АДМИНИСТРАТОРУ*\n\n"
                f"💬 {text}\n\n"
                f"🤖 Ответ: {answer}\n\n"
                f"📌 Chat ID: `{cid}`"
            )
        bot.send_message(cid, answer, reply_markup=main_menu())
    except Exception:
        bot.send_message(cid, "Произошла ошибка. Попробуйте ещё раз или нажмите /start",
                         reply_markup=main_menu())

# ─────────────────────────────────────────────────────────────────
# FLASK + SELF-PING
# ─────────────────────────────────────────────────────────────────

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running ✅"

@app.route("/ping")
def ping_route():
    return "pong", 200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def self_ping():
    import time
    import urllib.request
    while True:
        time.sleep(120)  # каждые 2 минуты
        try:
            urllib.request.urlopen(RENDER_URL + "/ping", timeout=15)
            print("[ping] ok")
        except Exception as e:
            print(f"[ping] err: {e}")
            # Если упал — ждём меньше и пробуем снова
            time.sleep(30)
            try:
                urllib.request.urlopen(RENDER_URL + "/ping", timeout=15)
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.remove_webhook()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()

    print("Bot started ✅")
    bot.polling(none_stop=True, interval=1, timeout=30)
