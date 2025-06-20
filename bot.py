import logging
import os
import json
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import asyncio
import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация с проверкой и значениями по умолчанию
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not API_TOKEN:
    logger.error("Токен бота не найден. Проверьте файл .env или переменные окружения.")
    raise ValueError("Токен бота не найден. Проверьте файл .env или переменные окружения.")

try:
    ADMINS = list(map(int, os.getenv('TELEGRAM_ADMINS', '').split(','))) if os.getenv('TELEGRAM_ADMINS') else []
    if not ADMINS:
        logger.warning("Администраторы не указаны. Бот будет работать без админ-панели.")
except ValueError:
    logger.error("Некорректный формат ID администраторов в TELEGRAM_ADMINS")
    ADMINS = []

DONATE_URL = os.getenv('DONATE_URL', 'https://example.com/donate')


# Загрузка данных из файла
def load_data() -> Dict[str, Any]:
    default_data = {
        'bookings': {},
        'tables': {'available': 10, 'total': 10},
        'reviews': {}
    }

    try:
        if not os.path.exists('data.json'):
            logger.warning("Файл данных не найден, создается новый")
            save_data(default_data)
            return default_data

        with open('data.json', 'r', encoding='utf-8') as f:
            data = json.load(f)

            # Проверяем структуру данных
            if not all(key in data for key in ['bookings', 'tables', 'reviews']):
                logger.warning("Неполная структура данных, восстанавливаем")
                for key in default_data:
                    if key not in data:
                        data[key] = default_data[key]
                save_data(data)

            return data

    except json.JSONDecodeError:
        logger.error("Ошибка чтения JSON, создаем новый файл данных")
        save_data(default_data)
        return default_data
    except Exception as e:
        logger.error(f"Критическая ошибка загрузки данных: {e}, используем данные по умолчанию")
        return default_data


# Сохранение данных в файл
def save_data(data: Dict[str, Any]):
    try:
        with open('data.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")


# Инициализация данных
data = load_data()
bookings_db = data['bookings']
tables_db = data['tables']
reviews_db = data['reviews']

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# Состояния для FSM
class BookingStates(StatesGroup):
    waiting_for_booking_type = State()
    waiting_for_cottage_date = State()
    waiting_for_cottage_guests = State()
    waiting_for_table_date = State()
    waiting_for_table_guests = State()
    waiting_for_contact = State()
    waiting_for_admin_comment = State()
    waiting_for_tables_count = State()
    waiting_for_review_rating = State()
    waiting_for_review_text = State()


# Клавиатуры
def get_main_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Забронировать коттедж")],
            [KeyboardButton(text="🍾 Забронировать столик")],
            [KeyboardButton(text="💸 Оставить чаевые")],
            [KeyboardButton(text="⭐️ Оставить отзыв")]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_admin_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="📋 Список бронирований")],
            [KeyboardButton(text="❌ Отменить бронирование")],
            [KeyboardButton(text="✏️ Изменить кол-во столиков")],
            [KeyboardButton(text="🔙 В главное меню")]
        ],
        resize_keyboard=True
    )
    return keyboard


def get_cancel_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Отменить")]],
        resize_keyboard=True
    )
    return keyboard


def get_review_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⭐️ 1"), KeyboardButton(text="⭐️ 2")],
            [KeyboardButton(text="⭐️ 3"), KeyboardButton(text="⭐️ 4")],
            [KeyboardButton(text="⭐️ 5"), KeyboardButton(text="🔙 Отменить")]
        ],
        resize_keyboard=True
    )
    return keyboard


# Middleware для проверки администратора
async def admin_check_middleware(handler, event, data):
    if hasattr(event, 'from_user') and event.from_user.id not in ADMINS:
        if isinstance(event, types.CallbackQuery):
            await event.answer("⛔ Доступ запрещен!")
        return
    return await handler(event, data)


dp.callback_query.middleware(admin_check_middleware)


# Вспомогательные функции
def is_date_available(booking_type: str, date: str) -> bool:
    """Проверяет доступность даты для бронирования"""
    if booking_type == 'table':
        return tables_db['available'] > 0

    # Для коттеджей можно добавить проверку на конкретные даты
    booked_dates = [b['date'] for b in bookings_db.values()
                   if b['type'] == 'cottage' and b['status'] == 'confirmed']
    return date not in booked_dates


async def notify_admins(message: str, booking_id: Optional[str] = None):
    """Отправляет уведомление всем администраторам"""
    for admin_id in ADMINS:
        try:
            keyboard = None
            if booking_id:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{booking_id}")],
                    [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{booking_id}")]
                ])

            await bot.send_message(admin_id, message, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления администратору {admin_id}: {e}")


# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    try:
        if message.from_user.id in ADMINS:
            await message.answer("👋 Добро пожаловать в админ-панель ClubOK!", reply_markup=get_admin_menu())
        else:
            await message.answer("🎉 Добро пожаловать в ClubOK!", reply_markup=get_main_menu())
    except Exception as e:
        logger.error(f"Ошибка в cmd_start: {e}")
        await message.answer("⚠️ Произошла ошибка. Пожалуйста, попробуйте позже.")


@dp.message(F.text == "💸 Оставить чаевые")
async def donate_handler(message: types.Message):
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💖 Перейти к оплате", url=DONATE_URL)]
        ])
        await message.answer("💌 Благодарим за вашу щедрость!", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в donate_handler: {e}")


# Бронирование коттеджа
@dp.message(F.text == "🏠 Забронировать коттедж")
async def book_cottage_start(message: types.Message, state: FSMContext):
    try:
        await state.set_state(BookingStates.waiting_for_cottage_date)
        await message.answer("📅 На какую дату вы хотите забронировать коттедж? (ДД.ММ.ГГГГ)",
                           reply_markup=get_cancel_keyboard())
    except Exception as e:
        logger.error(f"Ошибка в book_cottage_start: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_cottage_date)
async def process_cottage_date(message: types.Message, state: FSMContext):
    try:
        if message.text == "🔙 Отменить":
            await state.clear()
            await message.answer("❌ Бронирование отменено.", reply_markup=get_main_menu())
            return

        try:
            booking_date = datetime.datetime.strptime(message.text, "%d.%m.%Y").date()
        except ValueError:
            await message.answer("❌ Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ:")
            return

        today = datetime.date.today()

        if booking_date < today:
            await message.answer("❌ Нельзя забронировать коттедж на прошедшую дату. Введите корректную дату:")
            return

        if (booking_date - today).days > 365:
            await message.answer("❌ Бронирование возможно только на даты в течение года. Введите другую дату:")
            return

        if not is_date_available('cottage', message.text):
            await message.answer("❌ К сожалению, коттедж на эту дату уже забронирован. Выберите другую дату:")
            return

        await state.update_data(booking_date=message.text)
        await state.set_state(BookingStates.waiting_for_cottage_guests)
        await message.answer("👥 Укажите количество гостей:", reply_markup=get_cancel_keyboard())
    except Exception as e:
        logger.error(f"Ошибка в process_cottage_date: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_cottage_guests)
async def process_cottage_guests(message: types.Message, state: FSMContext):
    try:
        if message.text == "🔙 Отменить":
            await state.clear()
            await message.answer("❌ Бронирование отменено.", reply_markup=get_main_menu())
            return

        if not message.text.isdigit():
            await message.answer("❌ Пожалуйста, введите число:", reply_markup=get_cancel_keyboard())
            return

        guests = int(message.text)
        if guests < 1 or guests > 20:
            await message.answer("❌ Количество гостей должно быть от 1 до 20. Введите корректное число:",
                               reply_markup=get_cancel_keyboard())
            return

        await state.update_data(guests=guests, booking_type="cottage")
        await state.set_state(BookingStates.waiting_for_contact)

        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Отправить контакт", request_contact=True)],
                [KeyboardButton(text="📞 Ввести номер вручную")],
                [KeyboardButton(text="🔙 Отменить")]
            ],
            resize_keyboard=True
        )

        await message.answer("📞 Пожалуйста, поделитесь вашим контактом для подтверждения бронирования:",
                           reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в process_cottage_guests: {e}")
        await state.clear()


# Бронирование столика
@dp.message(F.text == "🍾 Забронировать столик")
async def book_table_start(message: types.Message, state: FSMContext):
    try:
        if tables_db['available'] <= 0:
            await message.answer("😔 К сожалению, сейчас нет свободных столиков.")
            return

        await state.set_state(BookingStates.waiting_for_table_date)
        await message.answer("📅 На какую дату вы хотите забронировать столик? (ДД.ММ.ГГГГ)",
                           reply_markup=get_cancel_keyboard())
    except Exception as e:
        logger.error(f"Ошибка в book_table_start: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_table_date)
async def process_table_date(message: types.Message, state: FSMContext):
    try:
        if message.text == "🔙 Отменить":
            await state.clear()
            await message.answer("❌ Бронирование отменено.", reply_markup=get_main_menu())
            return

        try:
            booking_date = datetime.datetime.strptime(message.text, "%d.%m.%Y").date()
        except ValueError:
            await message.answer("❌ Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ:")
            return

        today = datetime.date.today()

        if booking_date < today:
            await message.answer("❌ Нельзя забронировать столик на прошедшую дату. Введите корректную дату:")
            return

        if (booking_date - today).days > 365:
            await message.answer("❌ Бронирование возможно только на даты в течение года. Введите другую дату:")
            return

        await state.update_data(booking_date=message.text)
        await state.set_state(BookingStates.waiting_for_table_guests)
        await message.answer("👥 Укажите количество гостей:", reply_markup=get_cancel_keyboard())
    except Exception as e:
        logger.error(f"Ошибка в process_table_date: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_table_guests)
async def process_table_guests(message: types.Message, state: FSMContext):
    try:
        if message.text == "🔙 Отменить":
            await state.clear()
            await message.answer("❌ Бронирование отменено.", reply_markup=get_main_menu())
            return

        if not message.text.isdigit():
            await message.answer("❌ Пожалуйста, введите число:", reply_markup=get_cancel_keyboard())
            return

        guests = int(message.text)
        if guests < 1 or guests > 10:
            await message.answer("❌ Количество гостей за 1 стол должно быть от 1 до 10. Введите корректное число:",
                               reply_markup=get_cancel_keyboard())
            return

        await state.update_data(guests=guests, booking_type="table")
        await state.set_state(BookingStates.waiting_for_contact)

        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Отправить контакт", request_contact=True)],
                [KeyboardButton(text="📞 Ввести номер вручную")],
                [KeyboardButton(text="🔙 Отменить")]
            ],
            resize_keyboard=True
        )

        await message.answer("📞 Пожалуйста, поделитесь вашим контактом для подтверждения бронирования:",
                           reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в process_table_guests: {e}")
        await state.clear()


# Обработка контакта
@dp.message(BookingStates.waiting_for_contact, F.contact)
async def process_contact(message: types.Message, state: FSMContext):
    try:
        contact = message.contact
        await _save_booking(message, state, contact.phone_number)
    except Exception as e:
        logger.error(f"Ошибка в process_contact: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_contact)
async def process_contact_manual(message: types.Message, state: FSMContext):
    try:
        if message.text == "🔙 Отменить":
            await state.clear()
            await message.answer("❌ Бронирование отменено.", reply_markup=get_main_menu())
            return

        if message.text == "📞 Ввести номер вручную":
            await message.answer("📱 Введите ваш номер телефона в формате +79991234567:",
                               reply_markup=get_cancel_keyboard())
            return

        # Валидация номера телефона
        phone = ''.join(filter(str.isdigit, message.text))
        if len(phone) < 11:
            await message.answer("❌ Неверный формат телефона. Пожалуйста, введите номер в формате +79991234567:")
            return

        await _save_booking(message, state, phone)
    except Exception as e:
        logger.error(f"Ошибка в process_contact_manual: {e}")
        await state.clear()


async def _save_booking(message: types.Message, state: FSMContext, phone: str):
    """Общая функция для сохранения бронирования"""
    user_data = await state.get_data()

    # Проверка на дублирование бронирования
    existing_booking = next((b for b in bookings_db.values()
                           if b['user_id'] == message.from_user.id
                           and b['date'] == user_data['booking_date']
                           and b['type'] == user_data['booking_type']
                           and b['status'] == 'pending'), None)
    if existing_booking:
        await message.answer("⚠️ У вас уже есть ожидающее бронирование на эту дату.")
        await state.clear()
        return

    booking_id = str(datetime.datetime.now().timestamp()).replace('.', '')[-8:]
    booking_details = {
        'id': booking_id,
        'type': user_data['booking_type'],
        'date': user_data['booking_date'],
        'guests': user_data['guests'],
        'user_id': message.from_user.id,
        'user_name': message.from_user.full_name,
        'phone': phone,
        'status': 'pending',
        'created_at': datetime.datetime.now().isoformat()
    }

    bookings_db[booking_id] = booking_details
    save_data({'bookings': bookings_db, 'tables': tables_db, 'reviews': reviews_db})

    # Уведомление администраторов
    await notify_admins(
        f"📌 Новая заявка на бронирование:\n\n"
        f"🔹 Тип: {'Коттедж' if booking_details['type'] == 'cottage' else 'Столик'}\n"
        f"🔹 Дата: {booking_details['date']}\n"
        f"🔹 Гостей: {booking_details['guests']}\n"
        f"🔹 Клиент: {booking_details['user_name']}\n"
        f"🔹 Телефон: {booking_details['phone']}\n\n"
        f"ID брони: {booking_id}",
        booking_id
    )

    await state.clear()
    await message.answer("✅ Ваша заявка принята! Ожидайте подтверждения от администратора.",
                       reply_markup=get_main_menu())


# Система отзывов
@dp.message(F.text == "⭐️ Оставить отзыв")
async def start_review(message: types.Message, state: FSMContext):
    try:
        # Проверяем, есть ли у пользователя подтвержденные бронирования
        has_confirmed_bookings = any(
            b['user_id'] == message.from_user.id and b['status'] == 'confirmed'
            for b in bookings_db.values()
        )

        if not has_confirmed_bookings:
            await message.answer("❌ Вы можете оставить отзыв только после посещения нашего заведения.")
            return

        await state.set_state(BookingStates.waiting_for_review_rating)
        await message.answer("Оцените ваш визит от 1 до 5 звезд:", reply_markup=get_review_keyboard())
    except Exception as e:
        logger.error(f"Ошибка в start_review: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_review_rating, F.text.regexp(r'⭐️ [1-5]'))
async def process_review_rating(message: types.Message, state: FSMContext):
    try:
        rating = int(message.text.split()[1])
        await state.update_data(rating=rating)
        await state.set_state(BookingStates.waiting_for_review_text)
        await message.answer("Напишите ваш отзыв (или нажмите 'Пропустить'):",
                           reply_markup=get_cancel_keyboard())
    except Exception as e:
        logger.error(f"Ошибка в process_review_rating: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_review_text)
async def process_review_text(message: types.Message, state: FSMContext):
    try:
        if message.text == "🔙 Отменить":
            await state.clear()
            await message.answer("❌ Отзыв не сохранен.", reply_markup=get_main_menu())
            return

        user_data = await state.get_data()
        review_id = str(datetime.datetime.now().timestamp()).replace('.', '')[-8:]

        reviews_db[review_id] = {
            'user_id': message.from_user.id,
            'user_name': message.from_user.full_name,
            'rating': user_data['rating'],
            'text': message.text if message.text != "Пропустить" else "",
            'date': datetime.datetime.now().isoformat()
        }

        save_data({'bookings': bookings_db, 'tables': tables_db, 'reviews': reviews_db})

        await state.clear()
        await message.answer("Спасибо за ваш отзыв!", reply_markup=get_main_menu())

        # Уведомление администраторов о новом отзыве
        review_text = f"⭐️ Новый отзыв!\n\nОценка: {user_data['rating']}/5"
        if reviews_db[review_id]['text']:
            review_text += f"\nОтзыв: {reviews_db[review_id]['text']}"

        await notify_admins(review_text)
    except Exception as e:
        logger.error(f"Ошибка в process_review_text: {e}")
        await state.clear()


# Админ-команды
@dp.message(F.text == "📊 Статистика")
async def show_stats(message: types.Message):
    try:
        if message.from_user.id not in ADMINS:
            return

        pending = sum(1 for b in bookings_db.values() if b['status'] == 'pending')
        confirmed = sum(1 for b in bookings_db.values() if b['status'] == 'confirmed')
        rejected = sum(1 for b in bookings_db.values() if b['status'] == 'rejected')

        # Статистика отзывов
        if reviews_db:
            avg_rating = sum(r['rating'] for r in reviews_db.values()) / len(reviews_db)
            reviews_count = len(reviews_db)
        else:
            avg_rating = 0
            reviews_count = 0

        await message.answer(
            f"📊 Статистика:\n\n"
            f"📌 Бронирования:\n"
            f"⏳ Ожидают: {pending}\n"
            f"✅ Подтверждены: {confirmed}\n"
            f"❌ Отклонены: {rejected}\n\n"
            f"🍾 Столики:\n"
            f"Доступно: {tables_db['available']}/{tables_db['total']}\n\n"
            f"⭐️ Отзывы:\n"
            f"Средний рейтинг: {avg_rating:.1f}/5\n"
            f"Всего отзывов: {reviews_count}"
        )
    except Exception as e:
        logger.error(f"Ошибка в show_stats: {e}")


@dp.message(F.text == "📋 Список бронирований")
async def list_bookings(message: types.Message):
    try:
        if message.from_user.id not in ADMINS:
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for booking in sorted(bookings_db.values(),
                            key=lambda x: x['created_at'],
                            reverse=True)[:10]:
            status_icon = "🟡" if booking['status'] == 'pending' else "🟢" if booking['status'] == 'confirmed' else "🔴"
            btn_text = f"{status_icon} {'Коттедж' if booking['type'] == 'cottage' else 'Столик'} на {booking['date']}"
            keyboard.inline_keyboard.append(
                [InlineKeyboardButton(text=btn_text, callback_data=f"info_{booking['id']}")]
            )

        await message.answer("📋 Последние бронирования:", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в list_bookings: {e}")


@dp.message(F.text == "❌ Отменить бронирование")
async def cancel_booking_start(message: types.Message):
    try:
        if message.from_user.id not in ADMINS:
            return

        active_bookings = [b for b in bookings_db.values() if b['status'] == 'confirmed']
        if not active_bookings:
            await message.answer("❌ Нет активных бронирований для отмены.")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for booking in sorted(active_bookings,
                            key=lambda x: x['created_at'],
                            reverse=True)[:10]:
            btn_text = f"{'Коттедж' if booking['type'] == 'cottage' else 'Столик'} на {booking['date']} ({booking['guests']} чел.)"
            keyboard.inline_keyboard.append(
                [InlineKeyboardButton(text=btn_text, callback_data=f"cancel_{booking['id']}")]
            )

        await message.answer("📋 Выберите бронирование для отмены:", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка в cancel_booking_start: {e}")


@dp.message(F.text == "✏️ Изменить кол-во столиков")
async def change_tables_start(message: types.Message, state: FSMContext):
    try:
        if message.from_user.id not in ADMINS:
            return

        await state.set_state(BookingStates.waiting_for_tables_count)
        await message.answer(f"✏️ Текущее количество столиков: {tables_db['total']}\n"
                           f"Доступно: {tables_db['available']}\n\n"
                           f"Введите новое общее количество столиков:")
    except Exception as e:
        logger.error(f"Ошибка в change_tables_start: {e}")
        await state.clear()


@dp.message(BookingStates.waiting_for_tables_count)
async def process_tables_count(message: types.Message, state: FSMContext):
    try:
        if not message.text.isdigit():
            await message.answer("❌ Пожалуйста, введите число:")
            return

        new_count = int(message.text)
        if new_count < 1:
            await message.answer("❌ Количество столиков должно быть положительным числом. Введите корректное значение:")
            return

        # Проверяем, что новое количество не меньше уже забронированных столиков
        booked_tables = tables_db['total'] - tables_db['available']
        if new_count < booked_tables:
            await message.answer(f"❌ Нельзя установить меньше {booked_tables} столиков (уже забронировано).")
            return

        old_count = tables_db['total']
        tables_db['total'] = new_count
        tables_db['available'] = new_count - booked_tables
        save_data({'bookings': bookings_db, 'tables': tables_db, 'reviews': reviews_db})

        await state.clear()
        await message.answer(
            f"✅ Количество столиков изменено. Теперь доступно {tables_db['available']}/{tables_db['total']}",
            reply_markup=get_admin_menu())
    except Exception as e:
        logger.error(f"Ошибка в process_tables_count: {e}")
        await state.clear()


# Callback-обработчики
@dp.callback_query(F.data.startswith('confirm_'))
async def process_confirm(callback: types.CallbackQuery):
    try:
        booking_id = callback.data.split('_')[1]
        booking = bookings_db.get(booking_id)

        if not booking:
            await callback.answer("❌ Бронирование не найдено!")
            return

        if booking['status'] != 'pending':
            await callback.answer("ℹ️ Это бронирование уже обработано!")
            return

        if booking['type'] == 'table' and tables_db['available'] <= 0:
            await callback.answer("❌ Нет свободных столиков!")
            return

        booking['status'] = 'confirmed'
        if booking['type'] == 'table':
            tables_db['available'] -= 1
        save_data({'bookings': bookings_db, 'tables': tables_db, 'reviews': reviews_db})

        await callback.message.edit_text(
            f"✅ Бронирование {booking_id} подтверждено!\n\n" + callback.message.text.split('\n\n')[1]
        )

        try:
            await bot.send_message(
                booking['user_id'],
                f"🎉 Ваше бронирование подтверждено!\n\n"
                f"🔹 Тип: {'Коттедж' if booking['type'] == 'cottage' else 'Столик'}\n"
                f"🔹 Дата: {booking['date']}\n"
                f"🔹 Гостей: {booking['guests']}\n\n"
                f"Ждем вас в ClubOK!"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки подтверждения пользователю {booking['user_id']}: {e}")

        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в process_confirm: {e}")
        await callback.answer("⚠️ Произошла ошибка")


@dp.callback_query(F.data.startswith('reject_'))
async def process_reject(callback: types.CallbackQuery, state: FSMContext):
    try:
        booking_id = callback.data.split('_')[1]
        booking = bookings_db.get(booking_id)

        if not booking:
            await callback.answer("❌ Бронирование не найдено!")
            return

        if booking['status'] != 'pending':
            await callback.answer("ℹ️ Это бронирование уже обработано!")
            return

        booking['status'] = 'rejected'
        save_data({'bookings': bookings_db, 'tables': tables_db, 'reviews': reviews_db})

        await callback.message.edit_text(
            f"❌ Бронирование {booking_id} отклонено!\n\n" + callback.message.text.split('\n\n')[1]
        )

        await state.set_state(BookingStates.waiting_for_admin_comment)
        await state.update_data(booking_id=booking_id)

        await callback.message.answer("📝 Укажите причину отказа (это сообщение увидит клиент):")
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в process_reject: {e}")
        await callback.answer("⚠️ Произошла ошибка")


@dp.callback_query(F.data.startswith('cancel_'))
async def process_cancel(callback: types.CallbackQuery, state: FSMContext):
    try:
        booking_id = callback.data.split('_')[1]
        booking = bookings_db.get(booking_id)

        if not booking:
            await callback.answer("❌ Бронирование не найдено!")
            return

        if booking['status'] != 'confirmed':
            await callback.answer("ℹ️ Это бронирование уже отменено или не подтверждено!")
            return

        await state.set_state(BookingStates.waiting_for_admin_comment)
        await state.update_data(booking_id=booking_id)
        await callback.message.answer("📝 Укажите причину отмены (это сообщение увидит клиент):")
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в process_cancel: {e}")
        await callback.answer("⚠️ Произошла ошибка")


@dp.callback_query(F.data.startswith('info_'))
async def show_booking_info(callback: types.CallbackQuery):
    try:
        booking_id = callback.data.split('_')[1]
        booking = bookings_db.get(booking_id)

        if not booking:
            await callback.answer("❌ Бронирование не найдено!")
            return

        status_map = {
            'pending': '⏳ Ожидает подтверждения',
            'confirmed': '✅ Подтверждено',
            'rejected': '❌ Отклонено'
        }

        await callback.message.answer(
            f"📋 Информация о бронировании:\n\n"
            f"🔹 ID: {booking['id']}\n"
            f"🔹 Тип: {'Коттедж' if booking['type'] == 'cottage' else 'Столик'}\n"
            f"🔹 Дата: {booking['date']}\n"
            f"🔹 Гостей: {booking['guests']}\n"
            f"🔹 Клиент: {booking['user_name']}\n"
            f"🔹 Телефон: {booking['phone']}\n"
            f"🔹 Статус: {status_map.get(booking['status'], booking['status'])}\n"
            f"🔹 Создано: {datetime.datetime.fromisoformat(booking['created_at']).strftime('%d.%m.%Y %H:%M')}"
        )
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в show_booking_info: {e}")
        await callback.answer("⚠️ Произошла ошибка")


@dp.message(BookingStates.waiting_for_admin_comment)
async def process_admin_comment(message: types.Message, state: FSMContext):
    try:
        user_data = await state.get_data()
        booking_id = user_data['booking_id']
        booking = bookings_db.get(booking_id)

        if booking:
            if booking['status'] != 'rejected':
                if booking['type'] == 'table':
                    tables_db['available'] += 1
                booking['status'] = 'rejected'
                save_data({'bookings': bookings_db, 'tables': tables_db, 'reviews': reviews_db})

            try:
                await bot.send_message(
                    booking['user_id'],
                    f"😔 К сожалению, ваше бронирование отклонено.\n\n"
                    f"🔹 Тип: {'Коттедж' if booking['type'] == 'cottage' else 'Столик'}\n"
                    f"🔹 Дата: {booking['date']}\n"
                    f"🔹 Гостей: {booking['guests']}\n\n"
                    f"Причина: {message.text}\n\n"
                    f"Вы можете создать новое бронирование."
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления об отказе пользователю {booking['user_id']}: {e}")

            await message.answer(
                f"✅ Клиент уведомлен об отмене бронирования.\n"
                f"ID: {booking_id}\n"
                f"Причина: {message.text}",
                reply_markup=get_admin_menu()
            )
        else:
            await message.answer("❌ Бронирование не найдено!", reply_markup=get_admin_menu())

        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка в process_admin_comment: {e}")
        await state.clear()

# Запуск бота
async def main():
    try:
        logger.info("Starting bot...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
    finally:
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == '__main__':
    asyncio.run(main())