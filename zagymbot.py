import os
import psycopg2
from psycopg2.extras import RealDictCursor
import datetime as dt
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
    raise ValueError("BOT_TOKEN не установлен в переменных окружения!")

# --- ID канала из переменных окружения ---
CHANNEL_ID = int(os.environ.get('CHANNEL_ID', '-1003560266967'))

# --- Состояния для разговора ---
SELECTING_CLASS, SELECTING_DATE, ENTERING_NAME, REQUESTING_PHONE, SELECTING_BOOKING_TO_CANCEL = range(5)

# --- Подключение к базе данных (PostgreSQL) ---
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
            total_spots INTEGER DEFAULT 12,
            booked_spots INTEGER DEFAULT 0,
            UNIQUE(workout_type, day, time)
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
            booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active'
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# --- Заполнение базы данных расписанием ---
def populate_initial_data():
    conn = get_db_connection()
    cursor = conn.cursor()

    workout_types = [
        'Йога', 'Интервальная тренировка', 'Пилатес', 'Здоровая спина',
        'Бокс', 'Бедра ягодицы пресс', 'Стретчинг', 'Стретчинг+ягодицы',
        'Бокс 8-10 дети', 'Total body'
    ]

    cursor.execute('SELECT COUNT(*) FROM workout_types')
    count = cursor.fetchone()['count']

    if count == 0:
        for wt in workout_types:
            try:
                cursor.execute('INSERT INTO workout_types (name) VALUES (%s) ON CONFLICT (name) DO NOTHING', (wt,))
            except Exception as e:
                logger.error(f"Ошибка при добавлении типа {wt}: {e}")

    schedule_data = [
        ('Интервальная тренировка', 'Понедельник', '9:30-10:30', 'сила + кардио'),
        ('Стретчинг', 'Понедельник', '11:00-12:00', ''),
        ('Здоровая спина', 'Понедельник', '18:00-19:00', ''),
        ('Пилатес', 'Понедельник', '19:00-20:00', ''),
        ('Бокс', 'Понедельник', '20:00-21:00', ''),
        ('Бедра ягодицы пресс', 'Понедельник', '20:00-21:00', ''),

        ('Бокс', 'Вторник', '10:00-11:00', ''),
        ('Стретчинг', 'Вторник', '11:00-12:00', ''),
        ('Бокс 8-10 дети', 'Вторник', '15:00-16:00', ''),
        ('Стретчинг', 'Вторник', '19:00-20:00', ''),
        ('Total body', 'Вторник', '20:00-21:00', ''),

        ('Пилатес', 'Среда', '9:30-10:30', ''),
        ('Здоровая спина', 'Среда', '18:00-19:00', ''),
        ('Пилатес', 'Среда', '19:00-20:00', ''),
        ('Бокс', 'Среда', '20:00-21:00', ''),
        ('Бедра ягодицы пресс', 'Среда', '20:00-21:00', ''),

        ('Йога', 'Четверг', '8:30-9:30', ''),
        ('Пилатес', 'Четверг', '11:00-12:00', 'осанка и мягкое укрепление'),
        ('Бокс 8-10 дети', 'Четверг', '15:00-16:00', ''),
        ('Стретчинг+ягодицы', 'Четверг', '18:00-19:00', ''),
        ('Здоровая спина', 'Четверг', '19:00-20:00', ''),
        ('Бокс', 'Четверг', '20:00-21:00', ''),

        ('Бокс', 'Пятница', '8:30-9:30', ''),
        ('Бедра ягодицы пресс', 'Пятница', '9:30-10:30', ''),
        ('Бокс', 'Пятница', '18:00-19:00', ''),
        ('Total body', 'Пятница', '18:00-19:00', ''),

        ('Здоровая спина', 'Суббота', '9:00-10:00', ''),
        ('Бокс', 'Суббота', '10:00-11:00', ''),
        ('Пилатес', 'Суббота', '11:00-12:00', ''),
        ('Бокс 8-10 дети', 'Суббота', '13:00-14:00', ''),
        ('Total body', 'Суббота', '14:00-15:00', ''),
        ('Стретчинг', 'Суббота', '15:00-16:00', ''),

        ('Бокс', 'Воскресенье', '11:00-12:00', ''),
        ('Йога', 'Воскресенье', '13:00-14:00', ''),
        ('Пилатес', 'Воскресенье', '14:00-15:00', 'осанка и мягкое укрепление'),
    ]

    cursor.execute('SELECT COUNT(*) FROM schedule')
    count = cursor.fetchone()['count']

    if count == 0:
        for workout_type, day, session_time, description in schedule_data:
            cursor.execute('''
                INSERT INTO schedule (workout_type, day, time, description, total_spots, booked_spots)
                VALUES (%s, %s, %s, %s, 12, 0)
            ''', (workout_type, day, session_time, description))

    conn.commit()
    conn.close()
    logger.info("Расписание загружено")

# --- Сброс мест каждое воскресенье ---
async def reset_weekly_spots(context: ContextTypes.DEFAULT_TYPE):
    """Обнулить места и записи — только по воскресеньям"""
    if dt.datetime.now().weekday() != 6:  # 6 = воскресенье
        logger.info("Сегодня не воскресенье, сброс мест не выполняется")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE schedule SET booked_spots = 0')
    cursor.execute("UPDATE bookings SET status = 'expired' WHERE status = 'active'")
    conn.commit()
    conn.close()
    logger.info("🔄 Еженедельный сброс мест выполнен (воскресенье)")

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
        logger.info(f"Новый пользователь сохранен: {user_id}")

    conn.commit()
    conn.close()

def get_subscribed_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE subscribed = TRUE')
    users = [row['user_id'] for row in cursor.fetchall()]
    conn.close()
    logger.info(f"Найдено подписанных пользователей: {len(users)}")
    return users

def unsubscribe_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET subscribed = FALSE WHERE user_id = %s', (user_id,))
    conn.commit()
    conn.close()
    logger.info(f"Пользователь {user_id} отписался от рассылки")

def subscribe_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET subscribed = TRUE WHERE user_id = %s', (user_id,))
    conn.commit()
    conn.close()
    logger.info(f"Пользователь {user_id} подписался на рассылку")

# --- Функции для работы с расписанием и записями ---
def get_workout_types():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM workout_types ORDER BY name')
    types = [row['name'] for row in cursor.fetchall()]
    conn.close()
    return types

def get_sessions_by_type(workout_type):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT day, time, total_spots, booked_spots, id 
        FROM schedule 
        WHERE workout_type = %s 
        ORDER BY 
            CASE day
                WHEN 'Понедельник' THEN 1
                WHEN 'Вторник' THEN 2
                WHEN 'Среда' THEN 3
                WHEN 'Четверг' THEN 4
                WHEN 'Пятница' THEN 5
                WHEN 'Суббота' THEN 6
                WHEN 'Воскресенье' THEN 7
            END,
            time
    ''', (workout_type,))
    sessions = [(row['day'], row['time'], row['total_spots'], row['booked_spots'], row['id']) for row in cursor.fetchall()]
    conn.close()
    return sessions

def get_user_bookings(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, workout_type, day, time 
        FROM bookings 
        WHERE user_id = %s AND status = 'active'
        ORDER BY booking_date
    ''', (user_id,))
    bookings = [(row['id'], row['workout_type'], row['day'], row['time']) for row in cursor.fetchall()]
    conn.close()
    return bookings

def cancel_booking(booking_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT workout_type, day, time FROM bookings WHERE id = %s AND status = %s', (booking_id, 'active'))
    booking = cursor.fetchone()

    if not booking:
        conn.close()
        return False, "Запись не найдена или уже отменена"

    cursor.execute('UPDATE bookings SET status = %s WHERE id = %s', ('cancelled', booking_id))
    cursor.execute('''
        UPDATE schedule 
        SET booked_spots = booked_spots - 1 
        WHERE workout_type = %s AND day = %s AND time = %s
    ''', (booking['workout_type'], booking['day'], booking['time']))

    conn.commit()
    conn.close()
    return True, (booking['workout_type'], booking['day'], booking['time'])

def get_tomorrow_schedule():
    tomorrow = dt.datetime.now() + dt.timedelta(days=1)
    days_map = {
        0: 'Понедельник', 1: 'Вторник', 2: 'Среда', 3: 'Четверг',
        4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'
    }
    tomorrow_day = days_map[tomorrow.weekday()]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT workout_type, time, description, id, booked_spots, total_spots
        FROM schedule 
        WHERE day = %s 
        ORDER BY time
    ''', (tomorrow_day,))
    sessions = [
        (row['workout_type'], row['time'], row['description'], row['id'], row['booked_spots'], row['total_spots'])
        for row in cursor.fetchall()
    ]
    conn.close()
    return tomorrow_day, sessions

def book_session(session_id, user_id, user_name, phone):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT workout_type, day, time, booked_spots, total_spots FROM schedule WHERE id = %s', (session_id,))
    session = cursor.fetchone()

    if not session:
        conn.close()
        return False, "Сессия не найдена"

    workout_type = session['workout_type']
    day = session['day']
    session_time = session['time']
    booked_spots = session['booked_spots']
    total_spots = session['total_spots']

    if booked_spots >= total_spots:
        conn.close()
        return False, "Нет свободных мест"

    cursor.execute('UPDATE schedule SET booked_spots = booked_spots + 1 WHERE id = %s', (session_id,))
    cursor.execute('''
        INSERT INTO bookings (user_id, user_name, phone, workout_type, day, time, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'active')
    ''', (user_id, user_name, phone, workout_type, day, session_time))

    conn.commit()
    conn.close()
    return True, (workout_type, day, session_time, total_spots - (booked_spots + 1))

# --- Ежедневная рассылка в 15:00 ---
async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🚀 ЗАПУСК ЕЖЕДНЕВНОЙ РАССЫЛКИ (15:00)")

    try:
        tomorrow_day, sessions = get_tomorrow_schedule()
        logger.info(f"📅 Завтра: {tomorrow_day}, найдено тренировок: {len(sessions)}")

        if not sessions:
            logger.warning("❌ Нет тренировок на завтра — рассылка не отправлена")
            return

        message = f"🟠 Расписание на завтра! {tomorrow_day}:\n\n"
        for workout_type, session_time, description, session_id, booked_spots, total_spots in sessions:
            formatted_time = session_time.replace(':', '.')
            available = total_spots - booked_spots
            status = "✅" if available > 0 else "❌"
            message += f"⏰ {formatted_time}\n"
            message += f"{status} {workout_type}"
            if description:
                message += f"\n  {description}"
            message += f"\n  Свободно мест: {available}/{total_spots}"
            message += "\n\n"
        message += "Желаем успехов в фитнесе! 💪\nЗаписаться можно через бота 👇"

        users = get_subscribed_users()
        logger.info(f"📢 Подписанных пользователей: {len(users)}")

        if not users:
            logger.warning("❌ Нет подписанных пользователей — рассылка не отправлена")
            return

        sent_count = 0
        for user_id in users:
            try:
                await context.bot.send_message(chat_id=user_id, text=message)
                sent_count += 1
                logger.info(f"✅ Рассылка отправлена пользователю {user_id}")
            except Exception as e:
                logger.error(f"❌ Не удалось отправить сообщение пользователю {user_id}: {e}")
                if "Forbidden" in str(e) or "blocked" in str(e):
                    unsubscribe_user(user_id)

        logger.info(f"✅ Рассылка завершена. Отправлено {sent_count} из {len(users)} сообщений")

    except Exception as e:
        logger.error(f"💥 КРИТИЧЕСКАЯ ОШИБКА в рассылке: {e}")
        import traceback
        traceback.print_exc()

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

🟠 Понедельник 
•••
9.30 - 10.30
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

🟠 Вторник 
•••
10.00 - 11.00
Бокс

11.00 - 12.00
Стретчинг

15.00 - 16.00
Бокс 8-10 дети

19.00 - 20.00
Стретчинг

20.00 - 21.00
Total body

🟠 Среда
•••
9.30 - 10.30
Пилатес

18.00 - 19.00
Здоровая спина

19.00 - 20.00
Пилатес

20.00 - 21.00
Бокс

20.00 - 21.00
Бедра ягодицы пресс

🟠 Четверг
•••
8.30 - 9.30
Йога

11.00 - 12.00
Пилатес
(осанка и мягкое укрепление)

15.00 - 16.00
Бокс 8-10 дети

18.00 - 19.00
Стретчинг+ягодицы

19.00 - 20.00
Здоровая спина

20.00 - 21.00
Бокс

🟠 Пятница
•••
8.30 - 09.30
Бокс

9.30 - 10.30
Бедра, ягодицы, пресс

18.00 - 19.00
Бокс

18.00 - 19.00
Total body

🟠 Суббота
•••
9.00 - 10.00
Здоровая спина

10.00 - 11.00
Бокс

11.00 - 12.00
Пилатес

13.00 - 14.00
Бокс 8-10 дети

14.00 - 15.00
Total body

15.00 - 16.00
Стретчинг

🟠 Воскресенье 
•••
11.00 - 12.00
Бокс

13.00 - 14.00
Йога

14.00 - 15.00
Пилатес
(осанка и мягкое укрепление)
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

FAQ_MESSAGE = "❓ Выберите вопрос, ответ на который хотите получить 👇:"

FAQ_ANSWER_1 = """
❓ **Персональный абонемент**

Персональный абонемент это вход в клуб для клиентов которые занимаются персонально с тренером и дополнительно оплачивают индивидуальную тренировку, по этим абонементам нельзя посещать групповые занятия!
"""

FAQ_ANSWER_2 = """
❓ **Что такое Тотал Боди?**

Total body — это тренировка, которая одновременно прорабатывает все основные мышечные группы тела, включая руки, ноги, ягодицы, спину и пресс.
"""

SUBSCRIBE_MESSAGE = """
📢 **Управление рассылкой**

Каждый день в 15:00 мы присылаем расписание тренировок на завтра.
"""

# --- Клавиатуры ---
def get_main_keyboard():
    keyboard = [
        ["📝 Записаться", "📅 Узнать расписание"],
        ["💰 Абонементы", "❓ Частые вопросы"],
        ["👤 Задать вопрос менеджеру", "📢 Рассылка"],
        ["❌ Мои записи / Отмена"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_subscription_keyboard(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT subscribed FROM users WHERE user_id = %s', (user_id,))
    result = cursor.fetchone()
    conn.close()

    subscribed = result['subscribed'] if result else True

    keyboard = []
    if subscribed:
        keyboard.append([InlineKeyboardButton("🔕 Отписаться от рассылки", callback_data="unsubscribe")])
    else:
        keyboard.append([InlineKeyboardButton("🔔 Подписаться на рассылку", callback_data="subscribe")])

    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_workout_types_keyboard():
    workout_types = get_workout_types()
    workout_emojis = {
        'Йога': '🧘', 'Интервальная тренировка': '⚡', 'Пилатес': '🧘',
        'Здоровая спина': '💪', 'Бокс': '🥊', 'Бедра ягодицы пресс': '🍑',
        'Стретчинг': '🧘', 'Стретчинг+ягодицы': '🍑', 'Бокс 8-10 дети': '👶',
        'Total body': '💪'
    }

    keyboard = []
    for i in range(0, len(workout_types), 2):
        row = []
        wt1 = workout_types[i]
        emoji1 = workout_emojis.get(wt1, '🏋️')
        row.append(InlineKeyboardButton(f"{emoji1} {wt1}", callback_data=f"type_{wt1}"))
        if i + 1 < len(workout_types):
            wt2 = workout_types[i + 1]
            emoji2 = workout_emojis.get(wt2, '🏋️')
            row.append(InlineKeyboardButton(f"{emoji2} {wt2}", callback_data=f"type_{wt2}"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_sessions_keyboard(workout_type):
    sessions = get_sessions_by_type(workout_type)
    keyboard = []

    for day, session_time, total_spots, booked_spots, session_id in sessions:
        available = total_spots - booked_spots
        status = "✅" if available > 0 else "❌"
        formatted_time = session_time.replace(':', '.')
        button_text = f"{status} {day} - {formatted_time} (свободно: {available}/{total_spots})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"session_{session_id}")])

    keyboard.append([InlineKeyboardButton("« 🔙 К типам тренировок", callback_data="back_to_types")])
    keyboard.append([InlineKeyboardButton("« 🔙 В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_phone_keyboard():
    keyboard = [
        [KeyboardButton("📱 Отправить номер телефона", request_contact=True)],
        [KeyboardButton("🔙 Вернуться назад")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

def get_faq_keyboard():
    keyboard = [
        [InlineKeyboardButton("❓ №1. Персональный абонемент", callback_data="faq_1")],
        [InlineKeyboardButton("❓ №2. Что такое Тотал Боди?", callback_data="faq_2")],
        [InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_main_keyboard():
    keyboard = [[InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")]]
    return InlineKeyboardMarkup(keyboard)

def get_my_bookings_keyboard(user_id):
    bookings = get_user_bookings(user_id)
    if not bookings:
        return None

    keyboard = []
    for booking_id, workout_type, day, session_time in bookings:
        formatted_time = session_time.replace(':', '.')
        button_text = f"❌ {workout_type} - {day} {formatted_time}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"cancel_{booking_id}")])

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
        await update.message.reply_text(
            "👤 Свяжитесь с нашим менеджером — @ZaGymclub и мы ответим вам в ближайшее время!",
            reply_markup=get_back_to_main_keyboard()
        )
        return ConversationHandler.END

    elif text == "📢 Рассылка":
        await update.message.reply_text(SUBSCRIBE_MESSAGE, reply_markup=get_subscription_keyboard(user_id))
        return ConversationHandler.END

    elif text == "❌ Мои записи / Отмена":
        bookings = get_user_bookings(user_id)
        if not bookings:
            await update.message.reply_text(
                "❌ У вас нет активных записей.\n\nЧтобы записаться, нажмите «📝 Записаться»",
                reply_markup=get_main_keyboard()
            )
            return ConversationHandler.END

        keyboard = get_my_bookings_keyboard(user_id)
        await update.message.reply_text(
            "📋 Ваши активные записи:\n\nВыберите запись, которую хотите отменить:",
            reply_markup=keyboard
        )
        return SELECTING_BOOKING_TO_CANCEL

    else:
        await update.message.reply_text("Пожалуйста, воспользуйтесь кнопками меню.", reply_markup=get_main_keyboard())
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
            workout_type, day, session_time = result
            formatted_time = session_time.replace(':', '.')
            await query.edit_message_text(
                f"✅ Запись успешно отменена!\n\n"
                f"🏋️ {workout_type}\n📅 {day}\n⏰ {formatted_time}\n\n"
                f"Место освобождено. Если хотите, можете записаться снова.",
                reply_markup=get_back_to_main_keyboard()
            )
        else:
            await query.edit_message_text(f"❌ {result}", reply_markup=get_back_to_main_keyboard())
        return ConversationHandler.END

    elif query.data.startswith("type_"):
        workout_type = query.data[5:]
        context.user_data['selected_workout_type'] = workout_type
        sessions = get_sessions_by_type(workout_type)
        if not sessions:
            await query.edit_message_text(
                f"Для '{workout_type}' нет доступных сессий.",
                reply_markup=get_back_to_main_keyboard()
            )
            return SELECTING_CLASS
        await query.edit_message_text(
            f"Выберите дату для {workout_type}:",
            reply_markup=get_sessions_keyboard(workout_type)
        )
        return SELECTING_DATE

    elif query.data.startswith("session_"):
        session_id = int(query.data[8:])
        context.user_data['selected_session_id'] = session_id

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT workout_type, day, time FROM schedule WHERE id = %s', (session_id,))
        row = cursor.fetchone()
        conn.close()

        workout_type = row['workout_type']
        day = row['day']
        session_time = row['time']
        formatted_time = session_time.replace(':', '.')

        await query.edit_message_text(
            f"Вы выбрали:\n🏋️ {workout_type}\n📅 {day}\n⏰ {formatted_time}\n\nВведите ваше имя:",
            reply_markup=get_back_to_main_keyboard()
        )
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
    user_name = update.message.text.strip()

    if len(user_name) < 2 or len(user_name) > 50:
        await update.message.reply_text(
            "Введите корректное имя (2-50 символов):",
            reply_markup=get_back_to_main_keyboard()
        )
        return ENTERING_NAME

    context.user_data['user_name'] = user_name
    await update.message.reply_text(
        f"Спасибо, {user_name}! Теперь отправьте номер телефона:",
        reply_markup=get_phone_keyboard()
    )
    return REQUESTING_PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"🔍 handle_phone вызван! Текст: {update.message.text}")

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

    logger.info(f"user_id={user_id}, user_name={user_name}, session_id={session_id}, phone={phone}")

    if not session_id:
        await update.message.reply_text("Ошибка. Начните запись заново.", reply_markup=get_main_keyboard())
        context.user_data.clear()
        return ConversationHandler.END

    success, result = book_session(session_id, user_id, user_name, phone)
    logger.info(f"Результат записи: success={success}, result={result}")

    if success:
        workout_type, day, session_time, remaining = result
        formatted_time = session_time.replace(':', '.')

        await update.message.reply_text(
            f"✅ **Вы записаны!**\n\n"
            f"🏋️ {workout_type}\n📅 {day}\n⏰ {formatted_time}\n"
            f"📊 Осталось мест: {remaining}\n\nЖдем вас! 💪",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )

        try:
            channel_message = (
                "📢 **НОВАЯ ЗАПИСЬ** 📢\n\n"
                f"👤 {user_name}\n📞 {phone}\n"
                f"🏋️ {workout_type}\n📆 {day}\n⏱️ {formatted_time}"
            )
            await context.bot.send_message(chat_id=CHANNEL_ID, text=channel_message, parse_mode='Markdown')
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

# --- Запуск ---
def main():
    try:
        init_database()
        populate_initial_data()
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")

    application = Application.builder().token(TOKEN).build()

    booking_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^(📝 Записаться)$'), handle_reply_buttons)],
        states={
            SELECTING_CLASS: [CallbackQueryHandler(handle_inline_buttons, pattern='^type_|^back_to_')],
            SELECTING_DATE: [CallbackQueryHandler(handle_inline_buttons, pattern='^session_|^back_to_')],
            ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            REQUESTING_PHONE: [MessageHandler(filters.CONTACT | filters.TEXT & ~filters.COMMAND, handle_phone)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(booking_conv)
    application.add_handler(CallbackQueryHandler(handle_inline_buttons))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_buttons))

    job_queue = application.job_queue
    if job_queue:
        tz = pytz.timezone('Europe/Minsk')

        # Ежедневная рассылка в 15:00 по Минску
        job_queue.run_daily(
            send_daily_schedule,
            time=dt.time(hour=15, minute=0, tzinfo=tz)
        )
        logger.info("📅 Ежедневная рассылка настроена на 15:00 по Минску")

        # Сброс мест каждое воскресенье в 23:59 по Минску
        job_queue.run_daily(
            reset_weekly_spots,
            time=dt.time(hour=23, minute=59, tzinfo=tz)
        )
        logger.info("🔄 Сброс мест настроен на 23:59 по Минску (проверка: воскресенье)")
    else:
        logger.warning("⚠️ job_queue недоступен! Установите: pip install 'python-telegram-bot[job-queue]'")

    logger.info("🚀 Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
