import asyncio
import datetime
import logging
import os
import re
import sys

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bzd_bot")

# ── КОНФИГУРАЦИЯ ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    log.error("Переменная BOT_TOKEN не найдена!")
    sys.exit(1)

# ── ПАРСИНГ ───────────────────────────────────────────────────────────────────
LOWER_CODES = {"3Б","3Д","2К","2Н","2Б"}
CAR_NAMES   = {2:"Сидячий", 3:"Плацкарт", 4:"Купе", 5:"Мягкий", 6:"СВ"}

try:
    from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
    from telegram.constants import ParseMode
    import phpserialize
except ImportError:
    log.error("Установите зависимости: pip install python-telegram-bot phpserialize playwright beautifulsoup4")
    sys.exit(1)

def _dec(s): return s.replace("&quot;",'"').replace("&amp;","&").replace("&lt;","<").replace("&gt;",">")
def _php(raw):
    try: return phpserialize.loads(raw.encode(), decode_strings=True)
    except: return {}

def parse_rv(rv, car_types):
    r = {"train":"","from":"","to":"","dep":"","has_any":False,"has_lower":False,"details":[]}
    p = _php(_dec(rv))
    if not p: return r
    r["train"] = str(p.get("train_number","")).strip()
    r["from"], r["to"] = p.get("from_station_db",""), p.get("to_station_db","")
    ft = p.get("from_time", 0)
    if ft: r["dep"] = datetime.datetime.fromtimestamp(int(ft)).strftime("%d.%m %H:%M")
    
    pl = p.get("places", {})
    places_list = list(pl.values()) if isinstance(pl, dict) else pl
    for cg in (places_list if isinstance(places_list, list) else []):
        ct = int(cg.get("car_type", 0))
        if car_types and ct not in car_types: continue
        pm = cg.get("price_multi", {})
        pml = list(pm.values()) if isinstance(pm, dict) else pm
        for x in (pml if isinstance(pml, list) else []):
            cs, n = x.get("classservice",""), int(x.get("places",0))
            if n > 0:
                r["has_any"] = True
                is_low = cs in LOWER_CODES
                if is_low: r["has_lower"] = True
                r["details"].append({"ct":ct,"cs":cs,"n":n,"lower":is_low})
    return r

# ── СОСТОЯНИЕ ─────────────────────────────────────────────────────────────────
USERS = {}

def get_user(chat_id):
    if chat_id not in USERS:
        USERS[chat_id] = {
            "url": None, "lower_only": True, "trains": None,
            "interval": 30, "car_types": [3, 4], "task": None,
            "history": [], "alerted": set(), "state": "IDLE"
        }
    return USERS[chat_id]

# ── КЛАВИАТУРЫ ────────────────────────────────────────────────────────────────
def kb_main():
    return ReplyKeyboardMarkup([
        ["🔍 Статус", "⚙ Настройки"],
        ["📜 История", "🛑 Остановить"]
    ], resize_keyboard=True)

def kb_settings():
    return ReplyKeyboardMarkup([
        ["⏱ Интервал", "🚆 Поезд"],
        ["💺 Тип мест", "🔙 Назад"]
    ], resize_keyboard=True)

def kb_seats():
    return ReplyKeyboardMarkup([["🔽 Только нижние", "🔄 Любые"], ["🔙 Назад"]], resize_keyboard=True)

# ── ОБРАБОТКА ТЕКСТА ──────────────────────────────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    u = get_user(chat_id)
    text = update.message.text.strip()

    # --- ЛОГИКА СОСТОЯНИЙ (ВВОД ДАННЫХ) ---
    if u["state"] == "SET_INTERVAL":
        if text.isdigit() and int(text) >= 10:
            u["interval"] = int(text)
            u["state"] = "IDLE"
            await update.message.reply_text(f"✅ Интервал установлен: {u['interval']} сек.", reply_markup=kb_settings())
        else:
            await update.message.reply_text("⚠ Введите число не меньше 10.")
        return

    if u["state"] == "SET_TRAIN":
        if text == "❌ Любой":
            u["trains"] = None
        else:
            u["trains"] = [text.upper()]
        u["state"] = "IDLE"
        await update.message.reply_text(f"✅ Фильтр поезда: {u['trains'][0] if u['trains'] else 'Выключен'}", reply_markup=kb_settings())
        return

    # --- ГЛАВНОЕ МЕНЮ ---
    if text == "🔍 Статус":
        active = "🟢 Активен" if u["task"] and not u["task"].done() else "🔴 Выключен"
        tr = u["trains"][0] if u["trains"] else "Все"
        seats = "Только нижние" if u["lower_only"] else "Любые"
        msg = (f"<b>ТЕКУЩИЙ СТАТУС</b>\n\n"
               f"📡 Мониторинг: {active}\n"
               f"⏱ Интервал: {u['interval']} сек\n"
               f"🚆 Поезд: {tr}\n"
               f"🪑 Места: {seats}\n\n"
               f"🔗 URL: {u['url'] or 'Не задан'}")
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    elif text == "⚙ Настройки":
        await update.message.reply_text("🛠 <b>Настройки мониторинга:</b>", parse_mode=ParseMode.HTML, reply_markup=kb_settings())

    elif text == "⏱ Интервал":
        u["state"] = "SET_INTERVAL"
        await update.message.reply_text("Введите количество секунд (минимум 10):", reply_markup=ReplyKeyboardMarkup([["10", "30", "60"]], resize_keyboard=True))

    elif text == "🚆 Поезд":
        u["state"] = "SET_TRAIN"
        await update.message.reply_text("Введите номер поезда (например 728) или нажмите кнопку:", reply_markup=ReplyKeyboardMarkup([["❌ Любой"]], resize_keyboard=True))

    elif text == "💺 Тип мест":
        await update.message.reply_text("Какие места искать?", reply_markup=kb_seats())

    elif text == "🔽 Только нижние":
        u["lower_only"] = True
        await update.message.reply_text("✅ Теперь ищем только нижние места.", reply_markup=kb_settings())

    elif text == "🔄 Любые":
        u["lower_only"] = False
        await update.message.reply_text("✅ Теперь ищем любые места.", reply_markup=kb_settings())

    elif text == "🔙 Назад":
        u["state"] = "IDLE"
        await update.message.reply_text("Главное меню:", reply_markup=kb_main())

    elif text == "🛑 Остановить":
        if u["task"]: u["task"].cancel()
        await update.message.reply_text("⏹ Мониторинг полностью остановлен.", reply_markup=kb_main())

    elif text == "📜 История":
        if not u["history"]:
            await update.message.reply_text("История находок пока пуста.")
        else:
            msg = "<b>ПОСЛЕДНИЕ НАХОДКИ:</b>\n\n" + "\n".join(u["history"][:10])
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

    elif "pass.rw.by" in text:
        u["url"] = text
        u["alerted"] = set()
        if u["task"]: u["task"].cancel()
        u["task"] = asyncio.create_task(do_monitor(chat_id, ctx.application))
        await update.message.reply_text("🚀 Ссылка принята! Начинаю мониторинг...", reply_markup=kb_main())

# ── МОНИТОРИНГ ────────────────────────────────────────────────────────────────
async def do_monitor(chat_id, app):
    u = get_user(chat_id)
    from playwright.async_api import async_playwright
    pw = None; browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        while True:
            try:
                await page.goto(u["url"], timeout=60000, wait_until="networkidle")
                await asyncio.sleep(2)
                html = await page.content()
                rvs = re.findall(r'name="route"\s+value="([^"]+)"', html)
                found = []
                for rv in rvs:
                    info = parse_rv(rv, u["car_types"])
                    if not info["train"]: continue
                    if u["trains"] and info["train"] not in u["trains"]: continue
                    if (info["has_lower"] if u["lower_only"] else info["has_any"]):
                        found.append(info)

                new = [f for f in found if f["train"] not in u["alerted"]]
                if new:
                    for f in new:
                        msg = (f"❗ <b>БИЛЕТЫ В ПРОДАЖЕ</b>\n\n"
                               f"🚆 Поезд: <b>{f['train']}</b>\n"
                               f"📍 {f['from']} ➡ {f['to']}\n"
                               f"🕒 Отправление: {f['dep']}")
                        await app.bot.send_message(chat_id, msg, parse_mode=ParseMode.HTML)
                        u["history"].insert(0, f"✅ {datetime.datetime.now().strftime('%H:%M')} - Поезд {f['train']}")
                    u["alerted"].update(f["train"] for f in new)
                else:
                    u["alerted"] &= {f["train"] for f in found}
            except Exception as e:
                log.error(f"Ошибка парсинга: {e}")
            await asyncio.sleep(u["interval"])
    finally:
        if browser: await browser.close()
        if pw: await pw.stop()

# ── СТАРТ ─────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Добро пожаловать в БЖД Хакер!</b>\n\n"
        "Просто отправьте мне ссылку на расписание с сайта pass.rw.by, и я начну искать билеты.\n\n"
        "Используйте <b>Настройки</b>, чтобы изменить интервал или номер поезда.",
        parse_mode=ParseMode.HTML, reply_markup=kb_main())

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
