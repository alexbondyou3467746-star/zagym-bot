import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, time
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import logging

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Токен бота из переменных окружения ---
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise ValueError("BOT_TOKEN не установлен!")

# --- ID канала ---
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', '-1003560266967'))

# --- Состояния для разговора ---
SELECTING_CLASS, SELECTING_WEEK, SELECTING_DATE, ENTERING_NAME, REQUESTING_PHONE, SELECTING_BOOKING_TO_CANCEL = range(6)

# --- ID пользователей с правами ---
DEVELOPER_ID = 7073843771
OWNER_ID = 188328400

# --- Расписание (шаблон: день недели, время, тип, описание) ---
SCHEDULE_TEMPLATE = [
    ('Понедельник', '9:20-10:30', 'Интервальная тренировка', 'сила + кардио'),
    ('Понедельник', '11:00-12:00', 'Стретчинг', ''),
    ('Понедельник', '18:00-19:00', 'Здоровая спина', ''),
    ('Понедельник', '19:00-20:00', 'Пилатес', ''),
    ('Понедельник', '20:00-21:00', 'Бокс', ''),
    ('Понедельник', '20:00-21:00', 'Бедра ягодицы пресс', ''),
    ('Вторник', '10:00-11:00', 'Бокс', ''),
    ('Вторник', '11:00-12:00', 'Стретчинг', ''),
    ('Вторник', '19:00-20:00', 'Стретчинг', ''),
    ('Вторник', '20:00-21:00', 'Total body', ''),
    ('Среда', '9:20-10:30', 'Пилатес', ''),
    ('Среда', '18:00-19:00', 'Здоровая спина', ''),
    ('Среда', '19:00-20:00', 'Пилатес', ''),
    ('Среда', '20:00-21:00', 'Бокс', ''),
    ('Среда', '20:00-21:00', 'Бедра ягодицы пресс', ''),
    ('Четверг', '8:00-9:00', 'Йога', ''),
    ('Четверг', '9:20-10:20', 'Пилатес', 'осанка и мягкое укрепление'),
    ('Четверг', '18:00-19:00', 'Стретчинг+ягодицы', ''),
    ('Четверг', '19:00-20:00', 'Здоровая спина', ''),
    ('Четверг', '20:00-21:00', 'Бокс', ''),
    ('Пятница', '8:30-9:30', 'Бокс', ''),
    ('Пятница', '9:20-10:30', 'Бедра ягодицы пресс', ''),
    ('Пятница', '18:00-19:00', 'Бокс', ''),
    ('Суббота', '9:00-10:00', 'Здоровая спина', ''),
    ('Суббота', '10:00-11:00', 'Бокс', ''),
    ('Суббота', '11:00-12:00', 'Пилатес', ''),
    ('Суббота', '12:00-13:00', 'Total body', ''),
    ('Суббота', '13:00-14:00', 'Бокс 8-10 дети', ''),
    ('Суббота', '13:00-14:00', 'Стретчинг', ''),
    ('Воскресенье', '11:00-12:00', 'Бокс', ''),
]

WEEKDAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']

def get_week_start():
    today = datetime.now().date()
    return today - timedelta(days=today.weekday())

# --- Подключение к базе данных ---
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        import sqlite3
        logger.warning("DATABASE_URL не найден, используем SQLite")
        return sqlite3.connect('fitness_bot.db')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)

# --- Инициализация базы данных ---
def init_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            subscribed BOOLEAN DEFAULT TRUE,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workout_types (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            id SERIAL PRIMARY KEY,
            workout_type TEXT NOT NULL,
            day TEXT NOT NULL,
            time TEXT NOT NULL,
            description TEXT,
            date DATE NOT NULL,
            total_spots INTEGER DEFAULT 12,
            booked_spots INTEGER DEFAULT 0,
            UNIQUE(workout_type, day, time, date)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            user_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            workout_type TEXT NOT NULL,
            day TEXT NOT NULL,
            time TEXT NOT NULL,
            date DATE NOT NULL,
            booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active'
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# --- Генерация расписания на 4 недели вперёд ---
def generate_schedule():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM schedule')
    cursor.execute('DELETE FROM workout_types')
    
    workout_types = set()
    for day, time, wtype, desc in SCHEDULE_TEMPLATE:
        workout_types.add(wtype)
    
    for wt in workout_types:
        try:
            cursor.execute('INSERT INTO workout_types (name) VALUES (%s) ON CONFLICT (name) DO NOTHING', (wt,))
        except Exception as e:
            logger.error(f"Ошибка при добавлении типа {wt}: {e}")
    
    week_start = get_week_start()
    dates = []
    for week in range(4):
        for day_offset in range(7):
            date = week_start + timedelta(days=week * 7 + day_offset)
            dates.append(date)
    
    for date in dates:
        day_name = WEEKDAYS[date.weekday()]
        for template_day, time, wtype, desc in SCHEDULE_TEMPLATE:
            if template_day == day_name:
                cursor.execute('''
                    INSERT INTO schedule (workout_type, day, time, description, date, total_spots, booked_spots)
                    VALUES (%s, %s, %s, %s, %s, 12, 0)
                ''', (wtype, day_name, time, desc, date))
    
    conn.commit()
    conn.close()
    logger.info("Расписание сгенерировано на 4 недели вперёд")

# --- Сброс мест ---
def reset_weekly_spots():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE schedule SET booked_spots = 0')
    conn.commit()
    conn.close()
    logger.info("🔄 Сброс мест выполнен")

# --- Функции для работы с пользователями ---
def save_user(user_id, username, first_name, last_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE user_id = %s', (user_id,))
    exists = cursor.fetchone()
    if not exists:
        cursor.execute('''
            INSERT INTO users (user_id, username, first_name, last_name, subscribed)
            VALUES (%s, %s, %s, %s, TRUE)
        ''', (user_id, username, first_name, last_name))
        logger.info(f"✅ Новый пользователь {user_id}")
    conn.commit()
    conn.close()

def get_subscribed_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE subscribed = TRUE')
    users = [row['user_id'] for row in cursor.fetchall()]
    conn.close()
    logger.info(f"📢 Подписанных пользователей: {len(users)}")
    return users

def unsubscribe_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET subscribed = FALSE WHERE user_id = %s', (user_id,))
    conn.commit()
    conn.close()

def subscribe_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET subscribed = TRUE WHERE user_id = %s', (user_id,))
    conn.commit()
    conn.close()

# --- Функции для работы с расписанием ---
def get_workout_types():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM workout_types ORDER BY name')
    types = [row['name'] for row in cursor.fetchall()]
    conn.close()
    return types

def get_sessions_by_type_and_week(workout_type, week_offset):
    """Получить сессии для типа тренировки на конкретную неделю"""
    week_start = get_week_start() + timedelta(days=week_offset * 7)
    week_end = week_start + timedelta(days=6)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT day, time, total_spots, booked_spots, id, date
        FROM schedule 
        WHERE workout_type = %s AND date >= %s AND date <= %s
        ORDER BY date, time
    ''', (workout_type, week_start, week_end))
    rows = cursor.fetchall()
    conn.close()
    
    sessions = []
    for row in rows:
        sessions.append((row['day'], row['time'], row['total_spots'], row['booked_spots'], row['id'], row['date']))
    
    return sessions

def get_weeks():
    """Получить список недель с датами"""
    week_start = get_week_start()
    weeks = []
    for i in range(4):
        start = week_start + timedelta(days=i * 7)
        end = start + timedelta(days=6)
        weeks.append((start, end))
    return weeks

def get_user_bookings(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, workout_type, day, time, date
        FROM bookings 
        WHERE user_id = %s AND status = 'active' AND date >= CURRENT_DATE
        ORDER BY date, time
    ''', (user_id,))
    bookings = [(row['id'], row['workout_type'], row['day'], row['time'], row['date']) for row in cursor.fetchall()]
    conn.close()
    return bookings

def cancel_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT workout_type, day, time, date FROM bookings WHERE id = %s AND status = %s', (booking_id, 'active'))
    booking = cursor.fetchone()
    if not booking:
        conn.close()
        return False, "Запись не найдена"
    cursor.execute('UPDATE bookings SET status = %s WHERE id = %s', ('cancelled', booking_id))
    cursor.execute('''
        UPDATE schedule 
        SET booked_spots = booked_spots - 1 
        WHERE workout_type = %s AND day = %s AND time = %s AND date = %s
    ''', (booking['workout_type'], booking['day'], booking['time'], booking['date']))
    conn.commit()
    conn.close()
    return True, (booking['workout_type'], booking['day'], booking['time'], booking['date'])

def get_tomorrow_schedule():
    tomorrow = datetime.now().date() + timedelta(days=1)
    tomorrow_day = WEEKDAYS[tomorrow.weekday()]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT workout_type, time, description, id, booked_spots, total_spots
        FROM schedule 
        WHERE day = %s AND date = %s
        ORDER BY time
    ''', (tomorrow_day, tomorrow))
    sessions = [(row['workout_type'], row['time'], row['description'], row['id'], row['booked_spots'], row['total_spots']) for row in cursor.fetchall()]
    conn.close()
    return tomorrow_day, tomorrow, sessions

def book_session(session_id, user_id, user_name, phone):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT workout_type, day, time, booked_spots, total_spots, date FROM schedule WHERE id = %s', (session_id,))
    session = cursor.fetchone()
    if not session:
        conn.close()
        return False, "Сессия не найдена"
    workout_type, day, time, booked_spots, total_spots, date = session['workout_type'], session['day'], session['time'], session['booked_spots'], session['total_spots'], session['date']
    if booked_spots >= total_spots:
        conn.close()
        return False, "Нет свободных мест"
    cursor.execute('UPDATE schedule SET booked_spots = booked_spots + 1 WHERE id = %s', (session_id,))
    cursor.execute('''
        INSERT INTO bookings (user_id, user_name, phone, workout_type, day, time, date, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
    ''', (user_id, user_name, phone, workout_type, day, time, date))
    conn.commit()
    conn.close()
    return True, (workout_type, day, time, date, total_spots - (booked_spots + 1))

# --- ЕЖЕДНЕВНАЯ РАССЫЛКА ---
async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🚀 ЗАПУСК ЕЖЕДНЕВНОЙ РАССЫЛКИ")
    
    try:
        tomorrow_day, tomorrow_date, sessions = get_tomorrow_schedule()
        
        if not sessions:
            logger.warning("❌ Нет тренировок на завтра")
            return
        
        date_str = tomorrow_date.strftime('%d.%m')
        short_day = tomorrow_day[:2]
        
        message = f"🟠 Расписание на завтра! {short_day} {date_str}:\n\n"
        for workout_type, time, description, session_id, booked_spots, total_spots in sessions:
            formatted_time = time.replace(':', '.')
            message += f"⏰ {formatted_time}\n"
            message += f"• {workout_type}"
            if description:
                message += f"\n  {description}"
            message += "\n\n"
        message += "Желаем успехов в фитнесе! 💪"
        
        keyboard = []
        for workout_type, time, description, session_id, booked_spots, total_spots in sessions:
            available = total_spots - booked_spots
            status = "✅" if available > 0 else "❌"
            formatted_time = time.replace(':', '.')
            button_text = f"{status} {workout_type} - {formatted_time} ({available}/{total_spots})"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"session_{session_id}")])
        keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
        
        users = get_subscribed_users()
        if not users:
            logger.warning("❌ Нет подписанных пользователей")
            return
        
        sent = 0
        for user_id in users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                sent += 1
                logger.info(f"✅ Отправлено {user_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка {user_id}: {e}")
                if "Forbidden" in str(e):
                    unsubscribe_user(user_id)
        logger.info(f"✅ Рассылка завершена: {sent}/{len(users)}")
    except Exception as e:
        logger.error(f"💥 Ошибка: {e}")

# --- КОМАНДА ДЛЯ СТАТИСТИКИ (только для владелицы) ---
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Эта команда только для владелицы зала.")
        return
    
    today = datetime.now().date()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT workout_type, time, total_spots, booked_spots, date
        FROM schedule 
        WHERE date = %s
        ORDER BY time
    ''', (today,))
    sessions = cursor.fetchall()
    conn.close()
    
    if not sessions:
        await update.message.reply_text(f"📅 На сегодня ({today.strftime('%d.%m')}) тренировок нет.")
        return
    
    message = f"📊 **Статистика на сегодня ({today.strftime('%d.%m')})**\n\n"
    for row in sessions:
        available = row['total_spots'] - row['booked_spots']
        formatted_time = row['time'].replace(':', '.')
        message += f"⏰ {formatted_time} — {row['workout_type']}\n"
        message += f"   🪑 Свободно: {available} из {row['total_spots']}\n\n"
    
    await update.message.reply_text(message, parse_mode='Markdown')

# --- КОМАНДА ДЛЯ ОБНУЛЕНИЯ МЕСТ (только для владелицы) ---
async def reset_spots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Эта команда только для владелицы зала.")
        return
    
    reset_weekly_spots()
    await update.message.reply_text("✅ Все места на тренировки обнулены! Теперь везде 12 свободных мест.")

# --- КОМАНДА ДЛЯ РУЧНОЙ РАССЫЛКИ (только для разработчика) ---
async def send_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("⛔ У вас нет прав.")
        return
    
    logger.info(f"🔔 Команда /send_now от пользователя {update.effective_user.id}")
    await update.message.reply_text("📤 Отправляю расписание на завтра всем подписанным пользователям...")
    await send_daily_schedule(context)
    await update.message.reply_text("✅ Рассылка завершена!")

# --- КОМАНДА ДЛЯ ПОДПИСКИ ВСЕХ ПОЛЬЗОВАТЕЛЕЙ (только для разработчика) ---
async def subscribe_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != DEVELOPER_ID:
        await update.message.reply_text("⛔ У вас нет прав.")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET subscribed = TRUE')
    conn.commit()
    conn.close()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    count = cursor.fetchone()['count']
    conn.close()
    
    await update.message.reply_text(f"✅ {count} пользователей подписаны на рассылку!")

# --- Текстовые сообщения ---
WELCOME_MESSAGE = (
    "🏋️ **Добро пожаловать в фитнес центр Za Gym!** 🏋️\n\n"
    "В главном меню Вы можете:\n"
    "📝 Записаться на тренировку\n"
    "📅 Узнать расписание\n"
    "💰 Посмотреть абонементы\n"
    "❓ Задать вопрос\n"
    "❌ Отменить запись\n\n"
    "📢 Ежедневно в 15:00 мы присылаем расписание на завтра!"
)

SCHEDULE_MESSAGE = """
Расписание тренировок Za Gym

🟠 Понедельник (Пн)
•••
9.20 - 10.30
Интервальная тренировка
сила + кардио

11.00 - 12.00
Стретчинг

18.00 - 19.00
Здоровая спина

19.00 - 20.00
Пилатес

20.00 - 21.00
Бокс

20.00 - 21.00
Бедра ягодицы пресс

🟠 Вторник (Вт)
•••
10.00 - 11.00
Бокс

11.00 - 12.00
Стретчинг

19.00 - 20.00
Стретчинг

20.00 - 21.00
Total body

🟠 Среда (Ср)
•••
9.20 - 10.30
Пилатес

18.00 - 19.00
Здоровая спина

19.00 - 20.00
Пилатес

20.00 - 21.00
Бокс

20.00 - 21.00
Бедра ягодицы пресс

🟠 Четверг (Чт)
•••
8.00 - 9.00
Йога

9.20 - 10.20
Пилатес
(осанка и мягкое укрепление)

18.00 - 19.00
Стретчинг+ягодицы

19.00 - 20.00
Здоровая спина

20.00 - 21.00
Бокс

🟠 Пятница (Пт)
•••
8.30 - 9.30
Бокс

9.20 - 10.30
Бедра, ягодицы, пресс

18.00 - 19.00
Бокс

🟠 Суббота (Сб)
•••
9.00 - 10.00
Здоровая спина

10.00 - 11.00
Бокс

11.00 - 12.00
Пилатес

12.00 - 13.00
Total Body

13.00 - 14.00
Бокс 8-10 дети

13.00 - 14.00
Стретчинг

🟠 Воскресенье (Вс)
•••
11.00 - 12.00
Бокс
"""

MEMBERSHIP_MESSAGE = """
💰 **Прайс-лист Za Gym** 💰

⚡️ **Разовые Посещения**
•••
• Разовое Посещение 17 BYN
🕒 Пн - Пт 7.00 - 16.00
      Сб 9.00-18.00
      Вс 9.00-15.00

• Разовое Посещение 22 BYN
🕒 Пн - Пт 16.00-22.00

🎯 **Абонементы на определенное количество посещений**
•••
• Абонемент на 8 посещений 115 BYN
🕒 Пн - Пт 7.00 - 16.00
      Сб 9.00-18.00
      Вс 9.00-15.00

• Абонемент на 8 посещений 145 BYN
🕒 Пн - Пт 07.00-22.00
      Сб 9.00-18.00
      Вс 9.00-15.00

• Абонемент на 12 посещений 145 BYN
🕒 Пн - Пт 7.00 - 16.00
      Сб 9.00-18.00
      Вс 9.00-15.00

• Абонемент на 12 посещений 175 BYN
🕒 Пн - Пт 07.00-22.00
      Сб 9.00-18.00
      Вс 9.00-15.00

🎫 **Безлимитные решения**
•••
• Безлимит на 1 месяц — 185 BYN
• Безлимит на 3 месяца — 430 BYN
• Безлимит на 6 месяцев — 730 BYN
• Безлимит на 12 месяцев — 1300 BYN
"""

FAQ_MESSAGE = "❓ Выберите вопрос:"

FAQ_ANSWER_1 = "❓ **Персональный абонемент**\n\nЭто вход в клуб для клиентов, которые занимаются персонально с тренером. По этим абонементам нельзя посещать групповые занятия!"

FAQ_ANSWER_2 = "❓ **Что такое Тотал Боди?**\n\nTotal body — это тренировка, которая прорабатывает все основные мышечные группы тела."

SUBSCRIBE_MESSAGE = "📢 **Управление рассылкой**\n\nКаждый день в 15:00 мы присылаем расписание."

# --- Клавиатуры ---
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["📝 Записаться", "📅 Узнать расписание"],
        ["💰 Абонементы", "❓ Частые вопросы"],
        ["👤 Задать вопрос менеджеру", "📢 Рассылка"],
        ["❌ Мои записи / Отмена"]
    ], resize_keyboard=True)

def get_subscription_keyboard(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT subscribed FROM users WHERE user_id = %s', (user_id,))
    result = cursor.fetchone()
    conn.close()
    subscribed = result['subscribed'] if result else True
    keyboard = [[InlineKeyboardButton("🔕 Отписаться от рассылки" if subscribed else "🔔 Подписаться на рассылку", callback_data="unsubscribe" if subscribed else "subscribe")]]
    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_workout_types_keyboard():
    types = get_workout_types()
    emojis = {
        'Йога': '🧘', 'Интервальная тренировка': '⚡', 'Пилатес': '🧘',
        'Здоровая спина': '💪', 'Бокс': '🥊', 'Бедра ягодицы пресс': '🍑',
        'Стретчинг': '🧘', 'Стретчинг+ягодицы': '🍑', 'Бокс 8-10 дети': '👶',
        'Total body': '💪'
    }
    keyboard = []
    for i in range(0, len(types), 2):
        row = [InlineKeyboardButton(f"{emojis.get(types[i], '🏋️')} {types[i]}", callback_data=f"type_{types[i]}")]
        if i + 1 < len(types):
            row.append(InlineKeyboardButton(f"{emojis.get(types[i+1], '🏋️')} {types[i+1]}", callback_data=f"type_{types[i+1]}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_weeks_keyboard(workout_type):
    """Клавиатура с выбором недели"""
    weeks = get_weeks()
    keyboard = []
    
    labels = ['Текущая неделя', 'Следующая неделя', 'Через неделю', 'Через 2 недели']
    
    for i, (start, end) in enumerate(weeks):
        start_str = start.strftime('%d.%m')
        end_str = end.strftime('%d.%m')
        label = f"{labels[i]} ({start_str} - {end_str})"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"week_{workout_type}_{i}")])
    
    keyboard.append([InlineKeyboardButton("« 🔙 К типам тренировок", callback_data="back_to_types")])
    keyboard.append([InlineKeyboardButton("« 🔙 В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_sessions_keyboard(workout_type, week_offset):
    """Клавиатура с тренировками на выбранную неделю"""
    sessions = get_sessions_by_type_and_week(workout_type, week_offset)
    keyboard = []
    
    for day, time, total_spots, booked_spots, session_id, date in sessions:
        available = total_spots - booked_spots
        status = "✅" if available > 0 else "❌"
        date_str = date.strftime('%d.%m')
        short_day = day[:2]
        button_text = f"{status} {short_day} {date_str} - {time.replace(':', '.')} ({available}/{total_spots})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"session_{session_id}")])
    
    keyboard.append([InlineKeyboardButton("« 🔙 К выбору недели", callback_data=f"back_to_weeks_{workout_type}")])
    keyboard.append([InlineKeyboardButton("« 🔙 В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_phone_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 Отправить номер телефона", request_contact=True)], ["🔙 Вернуться назад"]], resize_keyboard=True, one_time_keyboard=True)

def get_faq_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❓ №1. Персональный абонемент", callback_data="faq_1")],
        [InlineKeyboardButton("❓ №2. Что такое Тотал Боди?", callback_data="faq_2")],
        [InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")]
    ])

def get_back_to_main_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")]])

def get_my_bookings_keyboard(user_id):
    bookings = get_user_bookings(user_id)
    if not bookings:
        return None
    keyboard = []
    for booking_id, workout_type, day, time, date in bookings:
        date_str = date.strftime('%d.%m')
        short_day = day[:2]
        keyboard.append([InlineKeyboardButton(f"❌ {workout_type} - {short_day} {date_str} {time.replace(':', '.')}", callback_data=f"cancel_{booking_id}")])
    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

# --- Обработчики ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name, user.last_name)
    await update.message.reply_text(WELCOME_MESSAGE, reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def handle_reply_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "📝 Записаться":
        await update.message.reply_text("Выберите тип тренировки:", reply_markup=get_workout_types_keyboard())
        return SELECTING_CLASS
    elif text == "📅 Узнать расписание":
        await update.message.reply_text(SCHEDULE_MESSAGE, reply_markup=get_back_to_main_keyboard())
        return ConversationHandler.END
    elif text == "💰 Абонементы":
        await update.message.reply_text(MEMBERSHIP_MESSAGE, reply_markup=get_back_to_main_keyboard())
        return ConversationHandler.END
    elif text == "❓ Частые вопросы":
        await update.message.reply_text(FAQ_MESSAGE, reply_markup=get_faq_keyboard())
        return ConversationHandler.END
    elif text == "👤 Задать вопрос менеджеру":
        await update.message.reply_text("👤 Свяжитесь с нашим менеджером — @ZaGymclub", reply_markup=get_back_to_main_keyboard())
        return ConversationHandler.END
    elif text == "📢 Рассылка":
        await update.message.reply_text(SUBSCRIBE_MESSAGE, reply_markup=get_subscription_keyboard(user_id))
        return ConversationHandler.END
    elif text == "❌ Мои записи / Отмена":
        bookings = get_user_bookings(user_id)
        if not bookings:
            await update.message.reply_text("❌ У вас нет активных записей.", reply_markup=get_main_keyboard())
            return ConversationHandler.END
        await update.message.reply_text("📋 Ваши активные записи:", reply_markup=get_my_bookings_keyboard(user_id))
        return SELECTING_BOOKING_TO_CANCEL
    else:
        await update.message.reply_text("Пользуйтесь кнопками.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == "subscribe":
        subscribe_user(user_id)
        await query.edit_message_text("✅ Вы подписались на рассылку!", reply_markup=get_back_to_main_keyboard())
        return ConversationHandler.END
    elif query.data == "unsubscribe":
        unsubscribe_user(user_id)
        await query.edit_message_text("🔕 Вы отписались от рассылки.", reply_markup=get_back_to_main_keyboard())
        return ConversationHandler.END
    elif query.data.startswith("cancel_"):
        booking_id = int(query.data[7:])
        success, result = cancel_booking(booking_id)
        if success:
            workout_type, day, time, date = result
            date_str = date.strftime('%d.%m')
            short_day = day[:2]
            await query.edit_message_text(f"✅ Запись отменена!\n\n🏋️ {workout_type}\n📅 {short_day} {date_str}\n⏰ {time.replace(':', '.')}", reply_markup=get_back_to_main_keyboard())
        else:
            await query.edit_message_text(f"❌ {result}", reply_markup=get_back_to_main_keyboard())
        return ConversationHandler.END
    elif query.data.startswith("type_"):
        workout_type = query.data[5:]
        context.user_data['selected_workout_type'] = workout_type
        await query.edit_message_text(f"Выберите неделю для {workout_type}:", reply_markup=get_weeks_keyboard(workout_type))
        return SELECTING_WEEK
    elif query.data.startswith("week_"):
        parts = query.data.split('_')
        workout_type = parts[1]
        week_offset = int(parts[2])
        context.user_data['selected_workout_type'] = workout_type
        context.user_data['selected_week_offset'] = week_offset
        sessions = get_sessions_by_type_and_week(workout_type, week_offset)
        if not sessions:
            await query.edit_message_text(f"На выбранной неделе тренировок '{workout_type}' нет.", reply_markup=get_weeks_keyboard(workout_type))
            return SELECTING_WEEK
        await query.edit_message_text(f"Выберите дату для {workout_type}:", reply_markup=get_sessions_keyboard(workout_type, week_offset))
        return SELECTING_DATE
    elif query.data.startswith("back_to_weeks_"):
        workout_type = query.data[13:]
        await query.edit_message_text(f"Выберите неделю для {workout_type}:", reply_markup=get_weeks_keyboard(workout_type))
        return SELECTING_WEEK
    elif query.data.startswith("session_"):
        session_id = int(query.data[8:])
        context.user_data['selected_session_id'] = session_id
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT workout_type, day, time, date FROM schedule WHERE id = %s', (session_id,))
        row = cursor.fetchone()
        conn.close()
        date_str = row['date'].strftime('%d.%m')
        short_day = row['day'][:2]
        await query.edit_message_text(f"Вы выбрали:\n🏋️ {row['workout_type']}\n📅 {short_day} {date_str}\n⏰ {row['time'].replace(':', '.')}\n\nВведите ваше имя:", reply_markup=get_back_to_main_keyboard())
        return ENTERING_NAME
    elif query.data == "faq_1":
        await query.edit_message_text(FAQ_ANSWER_1)
        await query.message.reply_text("Вернуться к вопросам?", reply_markup=get_faq_keyboard())
        return ConversationHandler.END
    elif query.data == "faq_2":
        await query.edit_message_text(FAQ_ANSWER_2)
        await query.message.reply_text("Вернуться к вопросам?", reply_markup=get_faq_keyboard())
        return ConversationHandler.END
    elif query.data == "back_to_types":
        await query.edit_message_text("Выберите тип тренировки:", reply_markup=get_workout_types_keyboard())
        return SELECTING_CLASS
    elif query.data == "back_to_main":
        await query.edit_message_text(WELCOME_MESSAGE)
        await query.message.reply_text("Выберите действие:", reply_markup=get_main_keyboard())
        return ConversationHandler.END

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Введите корректное имя:", reply_markup=get_back_to_main_keyboard())
        return ENTERING_NAME
    context.user_data['user_name'] = name
    await update.message.reply_text(f"Спасибо, {name}! Теперь отправьте номер телефона:", reply_markup=get_phone_keyboard())
    return REQUESTING_PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 Вернуться назад":
        context.user_data.clear()
        await update.message.reply_text("Возвращаемся к выбору тренировки:", reply_markup=get_workout_types_keyboard())
        return SELECTING_CLASS
    
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()
        if not phone.replace('+', '').replace('-', '').replace(' ', '').isdigit():
            await update.message.reply_text("Введите корректный номер телефона:", reply_markup=get_phone_keyboard())
            return REQUESTING_PHONE
    
    user_id = update.effective_user.id
    user_name = context.user_data.get('user_name', 'Не указано')
    session_id = context.user_data.get('selected_session_id')
    
    if not session_id:
        await update.message.reply_text("Ошибка. Начните запись заново.", reply_markup=get_main_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    
    success, result = book_session(session_id, user_id, user_name, phone)
    
    if success:
        workout_type, day, time, date, remaining = result
        date_str = date.strftime('%d.%m')
        short_day = day[:2]
        await update.message.reply_text(
            f"✅ **Вы записаны!**\n\n🏋️ {workout_type}\n📅 {short_day} {date_str}\n⏰ {time.replace(':', '.')}\n📊 Осталось мест: {remaining}\n\nЖдем вас! 💪",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"📢 **НОВАЯ ЗАПИСЬ** 📢\n\n👤 {user_name}\n📞 {phone}\n🏋️ {workout_type}\n📆 {short_day} {date_str}\n⏱️ {time.replace(':', '.')}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка отправки в канал: {e}")
    else:
        await update.message.reply_text(f"❌ {result}", reply_markup=get_main_keyboard())
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=get_main_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

# --- ЗАПУСК ---
def main():
    try:
        init_database()
        generate_schedule()
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
    
    app = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^(📝 Записаться)$'), handle_reply_buttons)],
        states={
            SELECTING_CLASS: [CallbackQueryHandler(handle_inline_buttons, pattern='^type_|^back_to_')],
            SELECTING_WEEK: [CallbackQueryHandler(handle_inline_buttons, pattern='^week_|^back_to_')],
            SELECTING_DATE: [CallbackQueryHandler(handle_inline_buttons, pattern='^session_|^back_to_')],
            ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            REQUESTING_PHONE: [MessageHandler(filters.CONTACT | filters.TEXT & ~filters.COMMAND, handle_phone)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_inline_buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_buttons))
    app.add_handler(CommandHandler("send_now", send_now))
    app.add_handler(CommandHandler("subscribe_all", subscribe_all))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("reset_spots", reset_spots))
    
    jq = app.job_queue
    if jq:
        tz = pytz.timezone('Europe/Minsk')
        jq.run_daily(send_daily_schedule, time=time(hour=15, minute=0, tzinfo=tz))
        logger.info("📅 Рассылка настроена на 15:00")
        jq.run_daily(reset_weekly_spots, time=time(hour=14, minute=0, tzinfo=tz))
        logger.info("🔄 Сброс мест настроен на воскресенье 14:00")
    
    logger.info("🚀 Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
