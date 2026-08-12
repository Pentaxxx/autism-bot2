import asyncio
import random
import logging
from datetime import datetime
import os
from aiohttp import web

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
# ВАЖНО: токен больше не хранится в коде.
# 1. Зайди в @BotFather -> /revoke -> получи НОВЫЙ токен (старый, что был здесь, считай слитым).
# 2. На Render зайди в Environment -> Add Environment Variable -> BOT_TOKEN = <новый токен>.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

TIMER_SECONDS = 10
ROUNDS = 5

# URL, по которому Render разворачивает сервис (Render сам подставляет эту переменную).
# Нужен, чтобы бот сам себя пинговал и не давал Render "усыпить" бесплатный тариф.
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL")
PING_INTERVAL = 10 * 60  # 10 минут — с запасом до 15-минутного таймаута Render

logging.basicConfig(level=logging.INFO)

# ========== ХРАНИЛИЩЕ СТАТИСТИКИ В ПАМЯТИ ==========
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
    sorted_users = sorted(stats.items(), key=lambda x: x[1]['wins'], reverse=True)
    return [(uid, data['wins'], data['losses'], data['total']) for uid, data in sorted_users[:10]]

# ========== ИНИЦИАЛИЗАЦИЯ ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

class DuelStates(StatesGroup):
    answering = State()
    waiting_username = State()

duels = {}
matchmaking_queue = []

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

# ========== КОМАНДЫ ==========
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

@dp.message(Command("battle"))
async def cmd_battle(message: types.Message, state: FSMContext):
    await state.clear()
    args = message.text.split()
    if len(args) >= 2:
        target_username = args[1].replace('@', '').strip()
        if target_username:
            await start_battle_by_username(message, target_username)
            return
    await message.answer("Введите username соперника (например, @mfjrq):")
    await state.set_state(DuelStates.waiting_username)

@dp.message(StateFilter(DuelStates.waiting_username), F.text)
async def process_username_input(message: types.Message, state: FSMContext):
    username = message.text.replace('@', '').strip()
    if not username:
        await message.answer("Введите корректный username (например, @mfjrq).")
        return
    await state.clear()
    await start_battle_by_username(message, username)

async def start_battle_by_username(message: types.Message, target_username: str):
    update_user_info(message.from_user.id, message.from_user.username or "")
    opponent_id = None
    for uid, data in stats.items():
        if data.get('username', '').lower() == target_username.lower():
            opponent_id = uid
            break
    if not opponent_id:
        await message.answer(
            f"Не могу найти пользователя @{target_username} в своей базе.\n"
            "Убедись, что он написал мне команду /start (или любое сообщение) хотя бы один раз.",
            reply_markup=get_main_keyboard()
        )
        return

    try:
        opponent = await bot.get_chat(opponent_id)
    except:
        await message.answer("Не удалось получить данные пользователя. Попробуй позже.", reply_markup=get_main_keyboard())
        return

    if opponent_id == message.from_user.id:
        await message.answer("Нельзя вызвать самого себя.", reply_markup=get_main_keyboard())
        return

    for d in duels.values():
        if d.get('finished', False):
            continue
        if message.from_user.id in (d['challenger_id'], d['opponent_id']):
            await message.answer("Ты уже в активной дуэли. Закончи её сначала.", reply_markup=get_main_keyboard())
            return
        if opponent_id in (d['challenger_id'], d['opponent_id']):
            await message.answer("Оппонент сейчас в активной дуэли. Подожди.", reply_markup=get_main_keyboard())
            return

    if message.from_user.id in matchmaking_queue:
        await message.answer("Ты уже в очереди поиска. Используй /cancel, чтобы выйти.", reply_markup=get_main_keyboard())
        return
    if opponent_id in matchmaking_queue:
        await message.answer("Оппонент сейчас в очереди поиска. Подожди.", reply_markup=get_main_keyboard())
        return

    challenger = message.from_user
    duels[challenger.id] = {
        'challenger_id': challenger.id,
        'opponent_id': opponent_id,
        'challenger_name': get_username(challenger),
        'opponent_name': get_username(opponent),
        'stage': 'waiting_accept',
        'created_at': datetime.now(),
        'questions': random.sample(QUESTIONS, ROUNDS),
        'round': 0,
        'scores': {challenger.id: 0, opponent_id: 0},
        'answers': {},
        'answered': set(),
        'timer_task': None,
        'finished': False,
        'messages': {},
        'round_finished': False,
        'lock': asyncio.Lock()
    }

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{challenger.id}")],
        [InlineKeyboardButton(text="❌ Отказаться", callback_data=f"decline_{challenger.id}")]
    ])

    await bot.send_message(
        opponent_id,
        f"🔥 {challenger.full_name} вызывает тебя на Аутизм бот!",
        reply_markup=keyboard
    )
    await message.answer(f"Вызов отправлен @{target_username}.", reply_markup=get_main_keyboard())

    async def decline_timeout():
        await asyncio.sleep(30)
        duel = duels.get(challenger.id)
        if duel and duel['stage'] == 'waiting_accept' and not duel.get('finished'):
            await bot.send_message(challenger.id, "Противник не ответил. Он даун.", reply_markup=get_main_keyboard())
            await bot.send_message(opponent_id, "Ты не принял вызов. Ты даун.", reply_markup=get_main_keyboard())
            duels.pop(challenger.id, None)
    asyncio.create_task(decline_timeout())

# ========== ОБРАБОТЧИКИ КНОПОК МЕНЮ ==========
@dp.message(F.text == "🔍 Найти соперника")
async def button_find(message: types.Message):
    await cmd_find(message)

@dp.message(F.text == "⚔️ Вызвать друга")
async def button_battle(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Введите username соперника (например, @mfjrq):")
    await state.set_state(DuelStates.waiting_username)

@dp.message(F.text == "📊 Моя статистика")
async def button_stats(message: types.Message):
    await cmd_stats(message)

@dp.message(F.text == "🏆 Топ игроков")
async def button_top(message: types.Message):
    await cmd_top(message)

@dp.message(F.text == "❓ Помощь")
async def button_help(message: types.Message):
    await cmd_help(message)

@dp.message(F.text == "❌ Выйти из очереди")
async def button_cancel(message: types.Message):
    await cmd_cancel(message)

@dp.message(Command("find"))
async def cmd_find(message: types.Message):
    user_id = message.from_user.id
    update_user_info(user_id, message.from_user.username or "")

    for d in duels.values():
        if d.get('finished', False):
            continue
        if user_id in (d['challenger_id'], d['opponent_id']):
            await message.answer("Ты уже в активной дуэли. Закончи её сначала.", reply_markup=get_main_keyboard())
            return

    if user_id in matchmaking_queue:
        await message.answer("Ты уже в очереди. Ожидай соперника.", reply_markup=get_main_keyboard())
        return

    matchmaking_queue.append(user_id)
    await message.answer("Ты добавлен в очередь поиска соперника. Как только найдётся второй игрок, дуэль начнётся автоматически.", reply_markup=get_main_keyboard())

    if len(matchmaking_queue) >= 2:
        player1 = matchmaking_queue.pop(0)
        player2 = matchmaking_queue.pop(0)
        success = await start_duel(player1, player2)
        if not success:
            await bot.send_message(player1, "Не удалось начать дуэль. Попробуй снова.", reply_markup=get_main_keyboard())
            await bot.send_message(player2, "Не удалось начать дуэль. Попробуй снова.", reply_markup=get_main_keyboard())
            matchmaking_queue.append(player1)
            matchmaking_queue.append(player2)

# ========== ПРИНЯТИЕ / ОТКАЗ ==========
@dp.callback_query(F.data.startswith("accept_"))
async def accept_duel(callback: types.CallbackQuery):
    update_user_info(callback.from_user.id, callback.from_user.username or "")
    challenger_id = int(callback.data.split("_")[1])
    duel = duels.get(challenger_id)
    if not duel or duel['stage'] != 'waiting_accept':
        await callback.answer("Дуэль уже неактивна.")
        return
    if duel['opponent_id'] != callback.from_user.id:
        await callback.answer("Это не тебе.")
        return

    duel['stage'] = 'answering'
    await callback.message.edit_text("✅ Вызов принят! Начинаем.")
    await bot.send_message(duel['challenger_id'], f"🔥 {callback.from_user.full_name} принял вызов!", reply_markup=get_main_keyboard())

    await dp.storage.set_state(duel['challenger_id'], DuelStates.answering)
    await dp.storage.set_state(duel['opponent_id'], DuelStates.answering)

    await start_round(challenger_id)

@dp.callback_query(F.data.startswith("decline_"))
async def decline_duel(callback: types.CallbackQuery):
    update_user_info(callback.from_user.id, callback.from_user.username or "")
    challenger_id = int(callback.data.split("_")[1])
    duel = duels.get(challenger_id)
    if not duel or duel['stage'] != 'waiting_accept':
        await callback.answer("Дуэль уже неактивна.")
        return
    if duel['opponent_id'] != callback.from_user.id:
        await callback.answer("Это не тебе.")
        return

    duel['finished'] = True
    await callback.message.edit_text("❌ Ты отказался. Ты даун.")
    await bot.send_message(duel['challenger_id'], "Противник отказался. Ищи другого.", reply_markup=get_main_keyboard())
    duels.pop(challenger_id, None)

# ========== ЗАПУСК РАУНДА ==========
async def start_round(challenger_id: int):
    duel = duels.get(challenger_id)
    if not duel or duel['finished']:
        return
    if duel['round'] >= ROUNDS:
        await finish_duel(challenger_id)
        return

    q = duel['questions'][duel['round']]
    duel['answers'] = {}
    duel['answered'] = set()
    duel['messages'] = {}
    duel['round_finished'] = False

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for i, opt in enumerate(q['options']):
        button = InlineKeyboardButton(
            text=f"{i+1}️⃣ {opt}",
            callback_data=f"answer_{challenger_id}_{i}"
        )
        keyboard.inline_keyboard.append([button])

    text = f"Раунд {duel['round']+1} из {ROUNDS}\n\n"
    text += f"❓ {q['question']}\n\n"
    text += f"⏳ У тебя {TIMER_SECONDS} секунд."

    for user_id in (duel['challenger_id'], duel['opponent_id']):
        msg = await bot.send_message(user_id, text, reply_markup=keyboard)
        duel['messages'][user_id] = msg.message_id

    async def timeout_round():
        await asyncio.sleep(TIMER_SECONDS)
        duel = duels.get(challenger_id)
        if duel and duel['stage'] == 'answering' and not duel['round_finished'] and duel['round'] < ROUNDS:
            for user_id in (duel['challenger_id'], duel['opponent_id']):
                if user_id not in duel['answered']:
                    try:
                        await bot.edit_message_reply_markup(
                            chat_id=user_id,
                            message_id=duel['messages'][user_id],
                            reply_markup=None
                        )
                    except:
                        pass
                    await bot.send_message(user_id, "⏰ Время вышло!", reply_markup=get_main_keyboard())
            duel['round'] += 1
            await start_round(challenger_id)

    duel['timer_task'] = asyncio.create_task(timeout_round())

# ========== ОБРАБОТКА КНОПОК ОТВЕТОВ ==========
@dp.callback_query(F.data.startswith("answer_"))
async def handle_answer_button(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    challenger_id = int(parts[1])
    option_index = int(parts[2])

    user_id = callback.from_user.id
    duel = duels.get(challenger_id)
    if not duel or duel['finished'] or duel['stage'] != 'answering':
        await callback.answer("Дуэль уже завершена или неактивна.")
        return

    if 'lock' not in duel:
        duel['lock'] = asyncio.Lock()

    async with duel['lock']:
        if duel.get('round_finished', False):
            await callback.answer("Этот раунд уже завершён.")
            return

        if duel['round'] >= ROUNDS:
            await callback.answer("Раунды закончились.")
            return

        if user_id in duel['answered']:
            await callback.answer("Ты уже ответил на этот вопрос!", show_alert=True)
            return

        if user_id not in (duel['challenger_id'], duel['opponent_id']):
            await callback.answer("Ты не участвуешь в этой дуэли.")
            return

        q = duel['questions'][duel['round']]
        is_correct = (option_index == q['correct'])

        duel['answers'][user_id] = {
            'correct': is_correct,
            'time': datetime.now()
        }
        duel['answered'].add(user_id)

        try:
            await bot.edit_message_reply_markup(
                chat_id=user_id,
                message_id=duel['messages'][user_id],
                reply_markup=None
            )
        except:
            pass

        await callback.answer("Ответ принят!")

        if is_correct:
            await bot.send_message(user_id, "✅ Верно!", reply_markup=get_main_keyboard())
        else:
            await bot.send_message(user_id, "❌ Неверно.", reply_markup=get_main_keyboard())

        if len(duel['answered']) == 2:
            duel['round_finished'] = True

            if duel['timer_task'] and not duel['timer_task'].done():
                duel['timer_task'].cancel()

            ans_c = duel['answers'][duel['challenger_id']]
            ans_o = duel['answers'][duel['opponent_id']]

            if ans_c['correct'] and ans_o['correct']:
                if ans_c['time'] < ans_o['time']:
                    duel['scores'][duel['challenger_id']] += 1
                    winner = duel['challenger_name']
                else:
                    duel['scores'][duel['opponent_id']] += 1
                    winner = duel['opponent_name']
                msg = f"Оба правильно, но {winner} быстрее! Очко забирает {winner}."
                await bot.send_message(duel['challenger_id'], msg, reply_markup=get_main_keyboard())
                await bot.send_message(duel['opponent_id'], msg, reply_markup=get_main_keyboard())
            elif ans_c['correct'] and not ans_o['correct']:
                duel['scores'][duel['challenger_id']] += 1
                await bot.send_message(duel['challenger_id'], "Ты ответил правильно, соперник ошибся. Ты получаешь очко!", reply_markup=get_main_keyboard())
                await bot.send_message(duel['opponent_id'], "Ты ошибся, соперник ответил правильно. Он получает очко.", reply_markup=get_main_keyboard())
            elif ans_o['correct'] and not ans_c['correct']:
                duel['scores'][duel['opponent_id']] += 1
                await bot.send_message(duel['opponent_id'], "Ты ответил правильно, соперник ошибся. Ты получаешь очко!", reply_markup=get_main_keyboard())
                await bot.send_message(duel['challenger_id'], "Ты ошибся, соперник ответил правильно. Он получает очко.", reply_markup=get_main_keyboard())
            else:
                await bot.send_message(duel['challenger_id'], "Оба неправильно. Без очков.", reply_markup=get_main_keyboard())
                await bot.send_message(duel['opponent_id'], "Оба неправильно. Без очков.", reply_markup=get_main_keyboard())

            score_c = duel['scores'][duel['challenger_id']]
            score_o = duel['scores'][duel['opponent_id']]
            await bot.send_message(duel['challenger_id'], f"Счёт: {duel['challenger_name']} {score_c} — {score_o} {duel['opponent_name']}", reply_markup=get_main_keyboard())
            await bot.send_message(duel['opponent_id'], f"Счёт: {duel['challenger_name']} {score_c} — {score_o} {duel['opponent_name']}", reply_markup=get_main_keyboard())

            duel['round'] += 1
            await start_round(challenger_id)

# ========== ЗАВЕРШЕНИЕ ДУЭЛИ ==========
async def finish_duel(challenger_id: int):
    duel = duels.get(challenger_id)
    if not duel or duel['finished']:
        return
    duel['finished'] = True

    try:
        for user_id, msg_id in list(duel.get('messages', {}).items()):
            try:
                await bot.edit_message_reply_markup(
                    chat_id=user_id,
                    message_id=msg_id,
                    reply_markup=None
                )
            except:
                pass

        score_c = duel['scores'][duel['challenger_id']]
        score_o = duel['scores'][duel['opponent_id']]
        if score_c > score_o:
            update_stats(duel['challenger_id'], win=True)
            update_stats(duel['opponent_id'], win=False)
        elif score_o > score_c:
            update_stats(duel['opponent_id'], win=True)
            update_stats(duel['challenger_id'], win=False)
        else:
            update_stats(duel['challenger_id'], draw=True)
            update_stats(duel['opponent_id'], draw=True)

        verdict = get_winner_verdict(score_c, score_o, duel['challenger_name'], duel['opponent_name'])
        await bot.send_message(duel['challenger_id'], verdict, reply_markup=get_main_keyboard())
        await bot.send_message(duel['opponent_id'], verdict, reply_markup=get_main_keyboard())

        await dp.storage.set_state(duel['challenger_id'], None)
        await dp.storage.set_state(duel['opponent_id'], None)
    except Exception as e:
        logging.error(f"Ошибка при завершении дуэли {challenger_id}: {e}")
    finally:
        duels.pop(challenger_id, None)

# ========== ВЕБ-СЕРВЕР ДЛЯ ПИНГА ==========
async def health_check(request):
    return web.Response(text="I'm alive!")

async def run_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
    await site.start()
    logging.info(f"Web server started on port {int(os.environ.get('PORT', 10000))}")
    # Бесконечное удержание
    await asyncio.Event().wait()

async def self_ping_loop():
    """Периодически пингует свой же публичный URL, чтобы Render не усыплял сервис
    из-за отсутствия входящего трафика (бесплатный тариф засыпает через ~15 минут простоя)."""
    if not SELF_URL:
        logging.warning(
            "RENDER_EXTERNAL_URL не найден — self-ping отключён. "
            "Если хостинг не Render, настрой внешний пинг (UptimeRobot/cron-job.org) "
            "на URL этого сервиса каждые 5-10 минут."
        )
        return

    import aiohttp as _aiohttp
    async with _aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            try:
                async with session.get(SELF_URL, timeout=_aiohttp.ClientTimeout(total=15)) as resp:
                    logging.info(f"Self-ping: {resp.status}")
            except Exception as e:
                logging.warning(f"Self-ping не удался: {e}")

async def run_bot_with_restart():
    """Поллинг с автоперезапуском: если start_polling упадёт по сетевой ошибке,
    бот не остаётся выключенным навсегда, а переподключается через паузу."""
    backoff = 5
    while True:
        try:
            await dp.start_polling(bot)
            # Если start_polling вышел без исключения (штатная остановка) — выходим.
            break
        except Exception as e:
            logging.error(f"Поллинг упал: {e}. Перезапуск через {backoff} сек.")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

async def main():
    # Запускаем веб-сервер в фоне (health-check)
    asyncio.create_task(run_web_server())
    # Запускаем self-ping, чтобы платформа не усыпляла сервис
    asyncio.create_task(self_ping_loop())
    # Запускаем бота с автоперезапуском при сбоях
    await run_bot_with_restart()

if __name__ == "__main__":
    asyncio.run(main())