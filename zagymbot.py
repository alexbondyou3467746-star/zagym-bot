import asyncio
import sqlite3
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

# --- Токен бота (твой) ---
TOKEN = '7672809145:AAECMCLJZuXWt1y2HUA5aRervNPcvuXr9W0'

# --- ID канала для уведомлений (с -100 в начале) ---
CHANNEL_ID = -1003560266967

# --- Состояния для разговора (ConversationHandler) ---
SELECTING_CLASS, SELECTING_DATE, ENTERING_NAME, REQUESTING_PHONE = range(4)

# --- Инициализация базы данных ---
def init_database():
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    
    # Таблица для пользователей (для рассылки)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            subscribed BOOLEAN DEFAULT 1,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица для типов тренировок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workout_types (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        )
    ''')
    
    # Таблица для расписания (с description)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY,
            workout_type TEXT NOT NULL,
            day TEXT NOT NULL,
            time TEXT NOT NULL,
            description TEXT,
            total_spots INTEGER DEFAULT 12,
            booked_spots INTEGER DEFAULT 0,
            UNIQUE(workout_type, day, time)
        )
    ''')
    
    # Таблица для записей пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            workout_type TEXT NOT NULL,
            day TEXT NOT NULL,
            time TEXT NOT NULL,
            booking_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# --- Функция для сохранения пользователя ---
def save_user(user_id, username, first_name, last_name):
    """Сохранить пользователя в базу данных"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, subscribed)
        VALUES (?, ?, ?, ?, 1)
    ''', (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

# --- Функция для получения всех подписанных пользователей ---
def get_subscribed_users():
    """Получить всех пользователей, подписанных на рассылку"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE subscribed = 1')
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users

# --- Функция для отписки от рассылки ---
def unsubscribe_user(user_id):
    """Отписать пользователя от рассылки"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET subscribed = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# --- Функция для подписки на рассылку ---
def subscribe_user(user_id):
    """Подписать пользователя на рассылку"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET subscribed = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# --- Заполнение базы данных начальными данными (НОВОЕ РАСПИСАНИЕ) ---
def populate_initial_data():
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    
    # Типы тренировок из расписания
    workout_types = [
        'Йога', 'Интервальная тренировка', 'Пилатес', 'Здоровая спина',
        'Бокс', 'Бедра ягодицы пресс', 'Стретчинг', 'Стретчинг+ягодицы',
        'Бокс 8-10 дети', 'Total body'
    ]
    
    # ОЧИЩАЕМ все старые типы тренировок
    cursor.execute('DELETE FROM workout_types')
    
    # Добавляем только правильные типы
    for wt in workout_types:
        try:
            cursor.execute('INSERT OR IGNORE INTO workout_types (name) VALUES (?)', (wt,))
        except:
            pass
    
    # НОВОЕ расписание (обновленное)
    schedule_data = [
        # Понедельник
        ('Интервальная тренировка', 'Понедельник', '9:30-10:30', 'сила + кардио'),
        ('Стретчинг', 'Понедельник', '11:00-12:00', ''),
        ('Здоровая спина', 'Понедельник', '18:00-19:00', ''),
        ('Пилатес', 'Понедельник', '19:00-20:00', ''),
        ('Бокс', 'Понедельник', '20:00-21:00', ''),
        ('Бедра ягодицы пресс', 'Понедельник', '20:00-21:00', ''),
        
        # Вторник
        ('Бокс', 'Вторник', '10:00-11:00', ''),
        ('Стретчинг', 'Вторник', '11:00-12:00', ''),
        ('Бокс 8-10 дети', 'Вторник', '15:00-16:00', ''),
        ('Стретчинг', 'Вторник', '19:00-20:00', ''),
        
        # Среда
        ('Пилатес', 'Среда', '9:30-10:30', ''),
        ('Здоровая спина', 'Среда', '18:00-19:00', ''),
        ('Пилатес', 'Среда', '19:00-20:00', ''),
        ('Бокс', 'Среда', '20:00-21:00', ''),
        ('Бедра ягодицы пресс', 'Среда', '20:00-21:00', ''),
        
        # Четверг
        ('Йога', 'Четверг', '8:30-9:30', ''),
        ('Пилатес', 'Четверг', '11:00-12:00', 'осанка и мягкое укрепление'),
        ('Бокс 8-10 дети', 'Четверг', '15:00-16:00', ''),
        ('Стретчинг+ягодицы', 'Четверг', '18:00-19:00', ''),
        ('Здоровая спина', 'Четверг', '19:00-20:00', ''),
        ('Бокс', 'Четверг', '20:00-21:00', ''),
        
        # Пятница
        ('Бокс', 'Пятница', '8:30-9:30', ''),
        ('Бедра ягодицы пресс', 'Пятница', '9:30-10:30', ''),
        ('Бокс', 'Пятница', '18:00-19:00', ''),
        ('Total body', 'Пятница', '18:00-19:00', ''),
        
        # Суббота
        ('Здоровая спина', 'Суббота', '9:00-10:00', ''),
        ('Бокс', 'Суббота', '10:00-11:00', ''),
        ('Пилатес', 'Суббота', '11:00-12:00', ''),
        ('Бокс 8-10 дети', 'Суббота', '13:00-14:00', ''),
        ('Total body', 'Суббота', '14:00-15:00', ''),
        ('Стретчинг', 'Суббота', '15:00-16:00', ''),
        
        # Воскресенье
        ('Бокс', 'Воскресенье', '11:00-12:00', ''),
        ('Йога', 'Воскресенье', '13:00-14:00', ''),
        ('Пилатес', 'Воскресенье', '14:00-15:00', 'осанка и мягкое укрепление'),
    ]
    
    # Очищаем существующие данные и добавляем новые
    cursor.execute('DELETE FROM schedule')
    for workout_type, day, time, description in schedule_data:
        cursor.execute('''
            INSERT INTO schedule (workout_type, day, time, description, total_spots, booked_spots)
            VALUES (?, ?, ?, ?, 12, 0)
        ''', (workout_type, day, time, description))
    
    conn.commit()
    conn.close()

# --- Функция для получения расписания на завтра ---
def get_tomorrow_schedule():
    """Получить расписание на завтрашний день"""
    # Определяем день недели для завтра
    tomorrow = datetime.now() + timedelta(days=1)
    days_map = {
        0: 'Понедельник',
        1: 'Вторник', 
        2: 'Среда',
        3: 'Четверг',
        4: 'Пятница',
        5: 'Суббота',
        6: 'Воскресенье'
    }
    tomorrow_day = days_map[tomorrow.weekday()]
    
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT workout_type, time, description, id, booked_spots, total_spots
        FROM schedule 
        WHERE day = ? 
        ORDER BY time
    ''', (tomorrow_day,))
    sessions = cursor.fetchall()
    conn.close()
    
    return tomorrow_day, sessions

# --- Функция для форматирования расписания на завтра (с точками) ---
def format_tomorrow_schedule():
    """Форматировать расписание на завтра для рассылки (с точками в времени)"""
    tomorrow_day, sessions = get_tomorrow_schedule()
    
    if not sessions:
        return None
    
    message = f"🟠 Расписание в Фитнес-клубе на завтра! {tomorrow_day}:\n\n"
    
    for workout_type, time, description, session_id, booked_spots, total_spots in sessions:
        # Заменяем двоеточия на точки для красивого отображения
        formatted_time = time.replace(':', '.')
        message += f"⏰ {formatted_time}\n"
        message += f"• {workout_type}"
        if description:
            message += f"\n  {description}"
        message += "\n\n"
    
    message += "Желаем успехов в фитнесе! 💪"
    
    return message, sessions, tomorrow_day

# --- Функция для создания клавиатуры с тренировками на завтра ---
def get_tomorrow_workouts_keyboard(sessions):
    """Создать клавиатуру с тренировками на завтра"""
    keyboard = []
    
    for workout_type, time, description, session_id, booked_spots, total_spots in sessions:
        available = total_spots - booked_spots
        status = "✅" if available > 0 else "❌"
        # В кнопках тоже используем время с точками для красоты
        formatted_time = time.replace(':', '.')
        button_text = f"{status} {workout_type} - {formatted_time} (свободно: {available}/{total_spots})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"session_{session_id}")])
    
    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

# --- Функция для ежедневной рассылки ---
async def send_daily_schedule(context: ContextTypes.DEFAULT_TYPE):
    """Отправить расписание на завтра всем подписанным пользователям"""
    logger.info("Запуск ежедневной рассылки расписания")
    
    # Получаем расписание на завтра
    result = format_tomorrow_schedule()
    if not result:
        logger.info("Нет тренировок на завтра")
        return
    
    message, sessions, tomorrow_day = result
    keyboard = get_tomorrow_workouts_keyboard(sessions)
    
    # Получаем всех подписанных пользователей
    users = get_subscribed_users()
    logger.info(f"Найдено {len(users)} подписанных пользователей")
    
    sent_count = 0
    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message,
                reply_markup=keyboard
            )
            sent_count += 1
            logger.info(f"Расписание отправлено пользователю {user_id}")
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            # Если пользователь заблокировал бота, отписываем его
            if "Forbidden" in str(e):
                unsubscribe_user(user_id)
                logger.info(f"Пользователь {user_id} отписан от рассылки (заблокировал бота)")
    
    logger.info(f"Рассылка завершена. Отправлено {sent_count} сообщений")

# --- Данные (сообщения) ---

WELCOME_MESSAGE = (
    "🏋️ **Добро пожаловать в фитнес центр Za Gym!** 🏋️\n\n"
    "В главном меню Вы можете:\n"
    "📝 Записаться на тренировку\n"
    "📅 Узнать расписание\n"
    "💰 Посмотреть абонементы\n"
    "❓ Задать вопрос\n\n"
    "📢 Ежедневно в 15:00 мы присылаем расписание на завтра!"
)

# НОВОЕ расписание в формате с точками (обновленное)
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

🏃‍♂️ **Абонемент для персональных клиентов**
•••
• Абонемент персональный на 8 посещений 115 BYN
• Абонемент персональный на 12 посещений 145 BYN

🏋️ **Разовые посещения с тренером**
•••
• Персональная тренировка — 45-50 BYN
• Парная тренировка — 70-80 BYN
• Трио тренировка — 90 BYN
"""

FAQ_MESSAGE = "❓ Выберите вопрос, ответ на который хотите получить 👇:"

FAQ_ANSWER_1 = """
❓ **Персональный абонемент**

Персональный абонемент это вход в клуб для клиентов которые занимаются персонально с тренером и дополнительно оплачивают индивидуальную тренировку, по этим абонементам нельзя посещать групповые занятия!

С уважением, Zagym 💪
"""

FAQ_ANSWER_2 = """
❓ **Что такое Тотал Боди?**

Total body (или «фулбоди») — это тренировка, которая одновременно прорабатывает все основные мышечные группы тела, включая руки, ноги, ягодицы, спину и пресс. Такие занятия сочетают силовые и аэробные упражнения, могут проводиться с использованием собственного веса или дополнительного оборудования (гантели, бодибары, фитнес-резинки), помогают укрепить мышцы, улучшить выносливость и сжечь калории.
"""

SUBSCRIBE_MESSAGE = """
📢 **Управление рассылкой**

Каждый день в 15:00 мы присылаем расписание тренировок на завтра.

Вы можете подписаться или отписаться от рассылки в любой момент.
"""

# --- Функции для создания клавиатур ---

def get_main_keyboard():
    """Главное меню (Reply-кнопки внизу экрана)"""
    keyboard = [
        ["📝 Записаться", "📅 Узнать расписание"],
        ["💰 Абонементы", "❓ Частые вопросы"],
        ["👤 Задать вопрос менеджеру", "📢 Рассылка"]
    ]
    return ReplyKeyboardMarkup(
        keyboard, 
        resize_keyboard=True,
        one_time_keyboard=False
    )

def get_subscription_keyboard(user_id):
    """Клавиатура для управления подпиской"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT subscribed FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    subscribed = result[0] if result else 1
    
    keyboard = []
    if subscribed:
        keyboard.append([InlineKeyboardButton("🔕 Отписаться от рассылки", callback_data="unsubscribe")])
    else:
        keyboard.append([InlineKeyboardButton("🔔 Подписаться на рассылку", callback_data="subscribe")])
    
    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_workout_types_keyboard():
    """Клавиатура с типами тренировок"""
    workout_types = get_workout_types()
    
    # Эмодзи для разных типов тренировок
    workout_emojis = {
        'Йога': '🧘',
        'Интервальная тренировка': '⚡',
        'Пилатес': '🧘',
        'Здоровая спина': '💪',
        'Бокс': '🥊',
        'Бедра ягодицы пресс': '🍑',
        'Стретчинг': '🧘',
        'Стретчинг+ягодицы': '🍑',
        'Бокс 8-10 дети': '👶',
        'Total body': '💪'
    }
    
    keyboard = []
    # Группируем по 2 кнопки в ряд
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
    
    # Добавляем кнопки навигации
    keyboard.append([InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_sessions_keyboard(workout_type):
    """Клавиатура с доступными сессиями для конкретного типа тренировки"""
    sessions = get_sessions_by_type(workout_type)
    keyboard = []
    
    for day, time, total_spots, booked_spots, session_id in sessions:
        available = total_spots - booked_spots
        status = "✅" if available > 0 else "❌"
        # В кнопках тоже используем время с точками
        formatted_time = time.replace(':', '.')
        button_text = f"{status} {day} - {formatted_time} (свободно: {available}/{total_spots})"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"session_{session_id}")])
    
    # Добавляем кнопки навигации
    keyboard.append([InlineKeyboardButton("« 🔙 К типам тренировок", callback_data="back_to_types")])
    keyboard.append([InlineKeyboardButton("« 🔙 В главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(keyboard)

def get_phone_keyboard():
    """Клавиатура для запроса номера телефона с кнопкой возврата"""
    keyboard = [
        [KeyboardButton("📱 Отправить номер телефона", request_contact=True)],
        [KeyboardButton("🔙 Вернуться назад")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

def get_faq_keyboard():
    """Инлайн-клавиатура для вопросов FAQ"""
    keyboard = [
        [InlineKeyboardButton("❓ №1. Персональный абонемент", callback_data="faq_1")],
        [InlineKeyboardButton("❓ №2. Что такое Тотал Боди?", callback_data="faq_2")],
        [InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_to_main_keyboard():
    """Клавиатура с кнопкой возврата в главное меню"""
    keyboard = [
        [InlineKeyboardButton("« 🔙 Назад в главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Функции для работы с базой данных ---

def get_workout_types():
    """Получить все типы тренировок"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM workout_types ORDER BY name')
    types = [row[0] for row in cursor.fetchall()]
    conn.close()
    return types

def get_sessions_by_type(workout_type):
    """Получить все сессии для конкретного типа тренировки (без дубликатов)"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT day, time, total_spots, booked_spots, id 
        FROM schedule 
        WHERE workout_type = ? 
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
    sessions = cursor.fetchall()
    conn.close()
    return sessions

def book_session(session_id, user_id, user_name, phone):
    """Забронировать место на тренировку"""
    conn = sqlite3.connect('fitness_bot.db')
    cursor = conn.cursor()
    
    # Проверяем, есть ли свободные места
    cursor.execute('SELECT workout_type, day, time, booked_spots, total_spots FROM schedule WHERE id = ?', (session_id,))
    session = cursor.fetchone()
    
    if not session:
        conn.close()
        return False, "Сессия не найдена"
    
    workout_type, day, time, booked_spots, total_spots = session
    
    if booked_spots >= total_spots:
        conn.close()
        return False, "Нет свободных мест"
    
    # Увеличиваем счетчик забронированных мест
    cursor.execute('UPDATE schedule SET booked_spots = booked_spots + 1 WHERE id = ?', (session_id,))
    
    # Сохраняем запись пользователя
    cursor.execute('''
        INSERT INTO bookings (user_id, user_name, phone, workout_type, day, time)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, user_name, phone, workout_type, day, time))
    
    conn.commit()
    conn.close()
    
    return True, (workout_type, day, time, total_spots - (booked_spots + 1))

# --- Обработчики ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    await update.message.reply_text(
        WELCOME_MESSAGE,
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

async def handle_reply_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на Reply-кнопки (текстовые сообщения)"""
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "📝 Записаться":
        await update.message.reply_text(
            "Выберите тип тренировки:",
            reply_markup=get_workout_types_keyboard()
        )
        return SELECTING_CLASS
        
    elif text == "📅 Узнать расписание":
        await update.message.reply_text(
            SCHEDULE_MESSAGE,
            reply_markup=get_back_to_main_keyboard()
        )
        return ConversationHandler.END
        
    elif text == "💰 Абонементы":
        await update.message.reply_text(
            MEMBERSHIP_MESSAGE,
            reply_markup=get_back_to_main_keyboard()
        )
        return ConversationHandler.END
        
    elif text == "❓ Частые вопросы":
        await update.message.reply_text(
            FAQ_MESSAGE,
            reply_markup=get_faq_keyboard()
        )
        return ConversationHandler.END
        
    elif text == "👤 Задать вопрос менеджеру":
        await update.message.reply_text(
            "👤 Свяжитесь с нашим менеджером — @ZaGymclub и мы ответим вам в ближайшее время!",
            reply_markup=get_back_to_main_keyboard()
        )
        return ConversationHandler.END
        
    elif text == "📢 Рассылка":
        await update.message.reply_text(
            SUBSCRIBE_MESSAGE,
            reply_markup=get_subscription_keyboard(user_id)
        )
        return ConversationHandler.END
        
    else:
        await update.message.reply_text(
            "Пожалуйста, воспользуйтесь кнопками меню.",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

async def handle_inline_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на инлайн-кнопки"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    # Обработка подписки/отписки
    if query.data == "subscribe":
        subscribe_user(user_id)
        await query.edit_message_text(
            "✅ Вы успешно подписались на ежедневную рассылку!\n\n"
            "Теперь каждый день в 15:00 вы будете получать расписание на завтра.",
            reply_markup=get_back_to_main_keyboard()
        )
        return ConversationHandler.END
        
    elif query.data == "unsubscribe":
        unsubscribe_user(user_id)
        await query.edit_message_text(
            "🔕 Вы отписались от ежедневной рассылки.\n\n"
            "Вы всегда можете снова подписаться через меню.",
            reply_markup=get_back_to_main_keyboard()
        )
        return ConversationHandler.END
    
    # Обработка кнопок типов тренировок
    elif query.data.startswith("type_"):
        workout_type = query.data[5:]  # Убираем "type_"
        context.user_data['selected_workout_type'] = workout_type
        
        # Получаем сессии для этого типа тренировки
        sessions = get_sessions_by_type(workout_type)
        
        if not sessions:
            await query.edit_message_text(
                f"Для тренировок типа '{workout_type}' пока нет доступных сессий.",
                reply_markup=get_back_to_main_keyboard()
            )
            return SELECTING_CLASS
        
        # Формируем сообщение с информацией о тренировках
        message = f"### {workout_type}:\n\n"
        
        # Группируем по дням
        days_order = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
        sessions_by_day = {}
        
        for day, time, total_spots, booked_spots, session_id in sessions:
            if day not in sessions_by_day:
                sessions_by_day[day] = []
            sessions_by_day[day].append((time, total_spots, booked_spots, session_id))
        
        for day in days_order:
            if day in sessions_by_day:
                message += f"\n⭕ {day}\n"
                for time, total_spots, booked_spots, session_id in sessions_by_day[day]:
                    available = total_spots - booked_spots
                    # Здесь тоже заменяем на точки для красоты
                    formatted_time = time.replace(':', '.')
                    message += f"{formatted_time}\nСвободных мест: {available} из {total_spots}\n\n"
        
        message += "Выберите удобную для вас дату: 🪙"
        
        await query.edit_message_text(
            message,
            reply_markup=get_sessions_keyboard(workout_type)
        )
        return SELECTING_DATE
    
    # Обработка выбора конкретной сессии
    elif query.data.startswith("session_"):
        session_id = int(query.data[8:])  # Убираем "session_"
        context.user_data['selected_session_id'] = session_id
        
        # Получаем информацию о сессии
        conn = sqlite3.connect('fitness_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT workout_type, day, time FROM schedule WHERE id = ?', (session_id,))
        workout_type, day, time = cursor.fetchone()
        conn.close()
        
        context.user_data['selected_workout_type'] = workout_type
        context.user_data['selected_day'] = day
        context.user_data['selected_time'] = time
        
        # Форматируем время для отображения
        formatted_time = time.replace(':', '.')
        
        await query.edit_message_text(
            f"Вы выбрали:\n"
            f"🏋️ Тренировка: {workout_type}\n"
            f"📅 День: {day}\n"
            f"⏰ Время: {formatted_time}\n\n"
            f"Для записи введите ваше имя:",
            reply_markup=get_back_to_main_keyboard()
        )
        return ENTERING_NAME
    
    # Обработка FAQ
    elif query.data == "faq_1":
        await query.edit_message_text(FAQ_ANSWER_1)
        await query.message.reply_text(
            "Вернуться к вопросам?",
            reply_markup=get_faq_keyboard()
        )
        return ConversationHandler.END
        
    elif query.data == "faq_2":
        await query.edit_message_text(FAQ_ANSWER_2)
        await query.message.reply_text(
            "Вернуться к вопросам?",
            reply_markup=get_faq_keyboard()
        )
        return ConversationHandler.END
    
    # Навигация
    elif query.data == "back_to_types":
        await query.edit_message_text(
            "Выберите тип тренировки:",
            reply_markup=get_workout_types_keyboard()
        )
        return SELECTING_CLASS
        
    elif query.data == "back_to_main":
        # Возвращаемся в главное меню
        await query.edit_message_text(WELCOME_MESSAGE)
        await query.message.reply_text(
            "Выберите действие:",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода имени"""
    user_name = update.message.text.strip()
    
    if len(user_name) < 2 or len(user_name) > 50:
        await update.message.reply_text(
            "Пожалуйста, введите корректное имя (от 2 до 50 символов):",
            reply_markup=get_back_to_main_keyboard()
        )
        return ENTERING_NAME
    
    context.user_data['user_name'] = user_name
    
    await update.message.reply_text(
        f"Спасибо, {user_name}! Теперь отправьте ваш номер телефона:",
        reply_markup=get_phone_keyboard()
    )
    return REQUESTING_PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик получения номера телефона"""
    
    # Обработка кнопки "Вернуться назад"
    if update.message.text == "🔙 Вернуться назад":
        # Очищаем данные пользователя
        context.user_data.clear()
        await update.message.reply_text(
            "Возвращаемся к выбору типа тренировки:",
            reply_markup=get_workout_types_keyboard()
        )
        return SELECTING_CLASS
    
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        # Если пользователь ввел телефон текстом
        phone = update.message.text.strip()
        # Простая валидация
        if not phone.replace('+', '').replace('-', '').replace(' ', '').isdigit():
            await update.message.reply_text(
                "Пожалуйста, отправьте корректный номер телефона:",
                reply_markup=get_phone_keyboard()
            )
            return REQUESTING_PHONE
    
    # Получаем данные из контекста
    user_id = update.effective_user.id
    user_name = context.user_data.get('user_name', 'Не указано')
    session_id = context.user_data.get('selected_session_id')
    
    if not session_id:
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, начните запись заново.",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    
    # Бронируем место
    success, result = book_session(session_id, user_id, user_name, phone)
    
    if success:
        workout_type, day, time, remaining = result
        formatted_time = time.replace(':', '.')
        
        # Отправляем подтверждение пользователю
        await update.message.reply_text(
            f"✅ **Вы успешно записаны!**\n\n"
            f"🏋️ Тренировка: {workout_type}\n"
            f"📅 День: {day}\n"
            f"⏰ Время: {formatted_time}\n"
            f"📊 Осталось мест: {remaining}\n\n"
            f"Ждем вас в Za Gym! 💪",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        
        # Отправляем уведомление в канал
        try:
            channel_message = (
                "📢 **НОВАЯ ЗАПИСЬ НА ТРЕНИРОВКУ** 📢\n\n"
                f"👤 **Имя:** {user_name}\n"
                f"📞 **Телефон:** `{phone}`\n"
                f"🏋️ **Тренировка:** {workout_type}\n"
                f"📆 **День:** {day}\n"
                f"⏱️ **Время:** {formatted_time}\n"
                f"🆔 **User ID:** `{user_id}`\n\n"
                f"✅ Осталось мест: {remaining}"
            )
            
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=channel_message,
                parse_mode='Markdown'
            )
            print(f"✅ Уведомление отправлено в канал {CHANNEL_ID}")
            
        except Exception as e:
            print(f"❌ Ошибка при отправке в канал: {e}")
            # Сохраняем в лог на случай ошибки
            with open('bookings.log', 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now()}: {user_name} | {phone} | {workout_type} | {day} {time}\n")
        
    else:
        # Если мест нет или другая ошибка
        await update.message.reply_text(
            f"❌ {result}\n\nПопробуйте выбрать другую дату.",
            reply_markup=get_main_keyboard()
        )
    
    # Очищаем данные пользователя
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена действия"""
    await update.message.reply_text(
        "❌ Действие отменено. Возвращаю в главное меню.",
        reply_markup=get_main_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- Запуск бота ---
def main():
    # Инициализируем базу данных
    init_database()
    populate_initial_data()
    
    # Создаем приложение с поддержкой JobQueue
    application = Application.builder().token(TOKEN).build()
    
    # Создаем ConversationHandler для процесса записи
    booking_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^(📝 Записаться)$'), handle_reply_buttons)
        ],
        states={
            SELECTING_CLASS: [
                CallbackQueryHandler(handle_inline_buttons, pattern='^type_|^back_to_')
            ],
            SELECTING_DATE: [
                CallbackQueryHandler(handle_inline_buttons, pattern='^session_|^back_to_')
            ],
            ENTERING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)
            ],
            REQUESTING_PHONE: [
                MessageHandler(filters.CONTACT | filters.TEXT & ~filters.COMMAND, handle_phone)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(booking_conv)
    application.add_handler(CallbackQueryHandler(handle_inline_buttons))  # Для FAQ и навигации
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_buttons))  # Другие Reply-кнопки
    
    # Настраиваем ежедневную рассылку (в 15:00 каждый день)
    job_queue = application.job_queue
    if job_queue:
        # Устанавливаем время для рассылки (15:00 по Минску)
        tz = pytz.timezone('Europe/Minsk')
        job_queue.run_daily(send_daily_schedule, time=time(hour=15, minute=0, tzinfo=tz))
        print("📅 Ежедневная рассылка настроена на 15:00")
    else:
        print("⚠️ JobQueue не доступна. Рассылка не будет работать.")
        print("💡 Установите: pip install 'python-telegram-bot[job-queue]'")
    
    # Запускаем бота
    print("🚀 Бот запущен... Ищи его в Telegram: @zagymfitnessbot")
    print(f"📢 Уведомления будут отправляться в канал с ID: {CHANNEL_ID}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()