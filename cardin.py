import asyncio
import random
import logging
from datetime import datetime
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    FSInputFile
)

from questions import QUESTIONS

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

TIMER_SECONDS = 10
ROUNDS = 5

logging.basicConfig(level=logging.INFO)

# ========== ХРАНИЛИЩЕ СТАТИСТИКИ В ПАМЯТИ ==========
# stats[user_id] = {'wins': 0, 'losses': 0, 'total': 0, 'username': '@...'}
stats = {}

def update_user_info(user_id: int, username: str):
    if user_id not in stats:
        stats[user_id] = {'wins': 0, 'losses': 0, 'total': 0, 'username': username}
    else:
        stats[user_id]['username'] = username

def update_stats(user_id: int, win: bool = None, draw: bool = False):
    if user_id not in stats:
        stats[user_id] = {'wins': 0, 'losses': 0, 'total': 0, 'username': ''}
    if draw:
        stats[user_id]['total'] += 1
    else:
        if win:
            stats[user_id]['wins'] += 1
        else:
            stats[user_id]['losses'] += 1
        stats[user_id]['total'] += 1

def get_stats(user_id: int):
    return stats.get(user_id, {'wins': 0, 'losses': 0, 'total': 0, 'username': ''})

def get_top():
    # Сортируем по количеству побед и берём топ-10
    sorted_users = sorted(stats.items(), key=lambda x: x[1]['wins'], reverse=True)
    return [(uid, data['wins'], data['losses'], data['total']) for uid, data in sorted_users[:10]]

# ========== ИНИЦИАЛИЗАЦИЯ ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ========== СОСТОЯНИЯ FSM ==========
class DuelStates(StatesGroup):
    answering = State()
    waiting_username = State()

# ========== ХРАНИЛИЩЕ АКТИВНЫХ ДУЭЛЕЙ ==========
duels = {}
matchmaking_queue = []

# ========== КЛАВИАТУРА ГЛАВНОГО МЕНЮ ==========
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти соперника")],
            [KeyboardButton(text="⚔️ Вызвать друга")],
            [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="🏆 Топ игроков")],
            [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="❌ Выйти из очереди")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_username(user: types.User) -> str:
    return f"@{user.username}" if user.username else user.full_name

# ========== ФРАЗЫ ДЛЯ ВЕРДИКТОВ ==========
WINNER_PHRASES = [
    "Ты не просто аутист, ты — главный аутист вселенной! Кардин снял бы перед тобой шляпу, если бы она у него была. А пока — иди слушать AUTISM на репите.",
    "Ты знаешь даже, сколько у Unki см. Ты — легенда. Твой уровень аутизма зашкаливает. Кардин бы тобой гордился, но он слишком занят диссом на JDflag’а.",
    "Ты размазал оппонента как ФакШизу в батле. Аутисты в топе, дауны — в ауте. Кардин бы апнул тебя в друзья."
]

LOSER_PHRASES = [
    "Ты — даун, и это не шутка. Ты как JDflag — пытаешься быть крутым, но выходит только смешно. Слушай альбом чаще, может, поумнеешь.",
    "Ты проиграл. Ты — даун дня. Даже ФакШиза выглядит лучше на фоне тебя. Совет: перестань слушать попсу и включи AUTISM.",
    "Ты — воплощение всего, что Кардин ненавидит. Ты медленный, как Unki с его 11 см. Иди тренируйся, даун.",
    "Ты проиграл, потому что твой уровень даунства зашкаливает. Ты как Issey — копируешь, но без души. Иди в попсу, там твоё место.",
    "Ты — даун. Кардин бы тебя задиссил в треке, но ему жаль тратить на тебя время. Хотя бы не позорься больше.",
    "Ты — аутсайдер. Твой мозг работает как виндовс 98 — всё тормозит. В следующий раз попробуй хотя бы загуглить ответы, даун."
]

DRAW_POSITIVE = [
    "Оба — настоящие аутисты! Вы набрали больше 4 правильных ответов — это уровень самого Кардина. Он бы вами гордился!",
    "Ничья, но какая! Вы оба — топ-аутисты. Кардин бы поставил вас в один ряд с собой. Продолжайте в том же духе!",
    "Вы оба — аутисты высшей пробы! Счёт ровный, но качество ответов — на высоте. Кардин бы апнул вас обоих."
]

DRAW_NEUTRAL = [
    "Ничья! Вы оба — ни рыба ни мясо. Кардин бы вас не заметил. Определитесь уже, кто вы.",
    "Вы оба — посредственности. Кардин бы вас не заметил. Вы как два Unki — одинаково бесполезны. Идите играйте в дурака онлайн.",
    "Ничья — это как трек «БОЛЬ И АЛКОГОЛЬ»: и больно, и пьяно, и непонятно. Вы оба — не дотянули до аутизма, но и до даунов вам далеко."
]

def get_winner_verdict(score_c, score_o, name_c, name_o):
    if score_c > score_o:
        winner_name = name_c
        loser_name = name_o
        winner_phrase = random.choice(WINNER_PHRASES)
        loser_phrase = random.choice(LOSER_PHRASES)
        return (f"Победитель — {winner_name}!\n"
                f"{winner_phrase}\n"
                f"{loser_name} — {loser_phrase}")
    elif score_o > score_c:
        winner_name = name_o
        loser_name = name_c
        winner_phrase = random.choice(WINNER_PHRASES)
        loser_phrase = random.choice(LOSER_PHRASES)
        return (f"Победитель — {winner_name}!\n"
                f"{winner_phrase}\n"
                f"{loser_name} — {loser_phrase}")
    else:
        total = score_c + score_o
        if total >= 8:
            return random.choice(DRAW_POSITIVE)
        else:
            return random.choice(DRAW_NEUTRAL)

async def start_duel(challenger_id: int, opponent_id: int):
    for d in duels.values():
        if d.get('finished', False):
            continue
        if challenger_id in (d['challenger_id'], d['opponent_id']) or opponent_id in (d['challenger_id'], d['opponent_id']):
            return False

    challenger = await bot.get_chat(challenger_id)
    opponent = await bot.get_chat(opponent_id)

    duels[challenger_id] = {
        'challenger_id': challenger_id,
        'opponent_id': opponent_id,
        'challenger_name': get_username(challenger),
        'opponent_name': get_username(opponent),
        'stage': 'answering',
        'created_at': datetime.now(),
        'questions': random.sample(QUESTIONS, ROUNDS),
        'round': 0,
        'scores': {challenger_id: 0, opponent_id: 0},
        'answers': {},
        'answered': set(),
        'timer_task': None,
        'finished': False,
        'messages': {},
        'round_finished': False,
        'lock': asyncio.Lock()
    }

    await dp.storage.set_state(challenger_id, DuelStates.answering)
    await dp.storage.set_state(opponent_id, DuelStates.answering)

    await bot.send_message(challenger_id, f"🔥 Найден соперник: {get_username(opponent)}! Дуэль начинается.", reply_markup=get_main_keyboard())
    await bot.send_message(opponent_id, f"🔥 Найден соперник: {get_username(challenger)}! Дуэль начинается.", reply_markup=get_main_keyboard())

    await start_round(challenger_id)
    return True

# ========== КОМАНДЫ И ОБРАБОТЧИКИ КНОПОК ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    if os.path.exists("start.jpg"):
        photo = FSInputFile("start.jpg")
        await message.answer_photo(
            photo,
            caption="👋 Привет! Я — Аутизм бот по вселенной cardinparis.\n"
                    "Используй кнопки внизу для навигации.",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "👋 Привет! Я — Аутизм бот по вселенной cardinparis.\n"
            "Используй кнопки внизу для навигации.",
            reply_markup=get_main_keyboard()
        )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    await message.answer(
        "Правила:\n"
        "1. Найди соперника через кнопку «Найти соперника»\n"
        "   или вызови друга через «Вызвать друга»\n"
        "2. В дуэли будет 5 вопросов, на каждый по 10 секунд\n"
        "3. Кто быстрее и правильнее — побеждает\n"
        "Удачи!",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    user_stats = get_stats(message.from_user.id)
    await message.answer(
        f"Твоя статистика:\n"
        f"Побед: {user_stats['wins']}\n"
        f"Поражений: {user_stats['losses']}\n"
        f"Всего дуэлей: {user_stats['total']}",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    rows = get_top()
    if not rows:
        await message.answer("Пока никто не играл. Будь первым!", reply_markup=get_main_keyboard())
        return
    text = "Топ игроков по победам:\n\n"
    for i, (user_id, wins, losses, total) in enumerate(rows, 1):
        name = stats.get(user_id, {}).get('username', f"ID:{user_id}")
        if not name:
            try:
                user = await bot.get_chat(user_id)
                name = user.full_name
            except:
                name = f"ID:{user_id}"
        text += f"{i}. {name} — {wins} побед, {losses} поражений (всего {total})\n"
    await message.answer(text, reply_markup=get_main_keyboard())

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in matchmaking_queue:
        matchmaking_queue.remove(user_id)
        await message.answer("Ты вышел из очереди.", reply_markup=get_main_keyboard())
    else:
        await message.answer("Ты не в очереди.", reply_markup=get_main_keyboard())

# ... (остальной код полностью такой же, как в предыдущей версии, 
#      но без SQLite — все функции, начиная с @dp.message(Command("battle")) 
#      и до конца, остаются без изменений, потому что они не работают с БД)