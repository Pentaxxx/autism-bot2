import asyncio
import random
import sqlite3
import logging
from datetime import datetime
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

from questions import QUESTIONS

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8664506441:AAEq9dQVSMFpAxD6BUfkWd6Ny7kBU5np_p0"   # ЗАМЕНИ НА СВОЙ ТОКЕН
TIMER_SECONDS = 10
ROUNDS = 5

logging.basicConfig(level=logging.INFO)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('duel_stats.db')
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_duels INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def update_user_info(user_id: int, username: str):
    conn = sqlite3.connect('duel_stats.db')
    cur = conn.cursor()
    cur.execute('INSERT OR REPLACE INTO users (user_id, username, wins, losses, total_duels) '
                'VALUES (?, ?, COALESCE((SELECT wins FROM users WHERE user_id=?), 0), '
                'COALESCE((SELECT losses FROM users WHERE user_id=?), 0), '
                'COALESCE((SELECT total_duels FROM users WHERE user_id=?), 0))',
                (user_id, username, user_id, user_id, user_id))
    conn.commit()
    conn.close()

def get_user_id_by_username(username: str):
    conn = sqlite3.connect('duel_stats.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM users WHERE username = ?', (username,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def update_stats(user_id: int, win: bool):
    conn = sqlite3.connect('duel_stats.db')
    cur = conn.cursor()
    cur.execute('SELECT wins, losses FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    if row:
        wins, losses = row
        if win:
            wins += 1
        else:
            losses += 1
        cur.execute('UPDATE users SET wins = ?, losses = ?, total_duels = total_duels + 1 WHERE user_id = ?',
                    (wins, losses, user_id))
    else:
        if win:
            cur.execute('INSERT INTO users (user_id, wins, losses, total_duels) VALUES (?, ?, ?, ?)',
                        (user_id, 1, 0, 1))
        else:
            cur.execute('INSERT INTO users (user_id, wins, losses, total_duels) VALUES (?, ?, ?, ?)',
                        (user_id, 0, 1, 1))
    conn.commit()
    conn.close()

def get_stats(user_id: int):
    conn = sqlite3.connect('duel_stats.db')
    cur = conn.cursor()
    cur.execute('SELECT wins, losses, total_duels FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'wins': row[0], 'losses': row[1], 'total': row[2]}
    return {'wins': 0, 'losses': 0, 'total': 0}

def get_top():
    conn = sqlite3.connect('duel_stats.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id, wins, losses, total_duels FROM users ORDER BY wins DESC LIMIT 10')
    rows = cur.fetchall()
    conn.close()
    return rows

# ========== ИНИЦИАЛИЗАЦИЯ ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ========== СОСТОЯНИЯ FSM ==========
class DuelStates(StatesGroup):
    answering = State()

# ========== ХРАНИЛИЩЕ АКТИВНЫХ ДУЭЛЕЙ ==========
duels = {}
matchmaking_queue = []

def get_username(user: types.User) -> str:
    return f"@{user.username}" if user.username else user.full_name

# ========== НОВЫЕ ФРАЗЫ ДЛЯ ВЕРДИКТОВ ==========
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

DRAW_PHRASES = [
    "Ничья! Вы оба — ни аутисты, ни дауны. Вы как Серёга, который не может выбрать игру — вечно на грани. Определитесь уже, кто вы.",
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
        return random.choice(DRAW_PHRASES)

# ========== ФУНКЦИЯ ЗАПУСКА ДУЭЛИ ==========
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

    await bot.send_message(challenger_id, f"🔥 Найден соперник: {get_username(opponent)}! Дуэль начинается.")
    await bot.send_message(opponent_id, f"🔥 Найден соперник: {get_username(challenger)}! Дуэль начинается.")

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
                    "Команды:\n"
                    "/battle @username — вызвать оппонента\n"
                    "/find — найти случайного соперника\n"
                    "/cancel — выйти из очереди поиска\n"
                    "/stats — твоя статистика\n"
                    "/top — топ игроков\n"
                    "/help — помощь"
        )
    else:
        await message.answer(
            "👋 Привет! Я — Аутизм бот по вселенной cardinparis.\n"
            "Команды:\n"
            "/battle @username — вызвать оппонента\n"
            "/find — найти случайного соперника\n"
            "/cancel — выйти из очереди поиска\n"
            "/stats — твоя статистика\n"
            "/top — топ игроков\n"
            "/help — помощь"
        )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    await message.answer(
        "Правила:\n"
        "1. Вызови друга /battle @username\n"
        "   или найди случайного соперника через /find\n"
        "2. Соперник принимает вызов (при /battle) или сразу начинается дуэль (при /find)\n"
        "3. 5 вопросов, 10 секунд на ответ\n"
        "4. Кто быстрее и правильнее — побеждает\n"
        "Удачи!"
    )

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    stats = get_stats(message.from_user.id)
    await message.answer(
        f"Твоя статистика:\n"
        f"Побед: {stats['wins']}\n"
        f"Поражений: {stats['losses']}\n"
        f"Всего дуэлей: {stats['total']}"
    )

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    rows = get_top()
    if not rows:
        await message.answer("Пока никто не играл. Будь первым!")
        return
    text = "Топ игроков по победам:\n\n"
    for i, (user_id, wins, losses, total) in enumerate(rows, 1):
        try:
            user = await bot.get_chat(user_id)
            name = user.full_name
        except:
            name = f"ID:{user_id}"
        text += f"{i}. {name} — {wins} побед, {losses} поражений (всего {total})\n"
    await message.answer(text)

# ========== ВЫЗОВ ПО ЮЗЕРНЕЙМУ ==========
@dp.message(Command("battle"))
async def cmd_battle(message: types.Message):
    update_user_info(message.from_user.id, message.from_user.username or "")
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Укажи противника: /battle @username")
        return

    target_username = args[1].replace('@', '').strip()
    if not target_username:
        await message.answer("Укажи корректный username.")
        return

    opponent_id = get_user_id_by_username(target_username)
    if not opponent_id:
        await message.answer(
            f"Не могу найти пользователя @{target_username} в своей базе.\n"
            "Убедись, что он написал мне команду /start (или любое сообщение) хотя бы один раз."
        )
        return

    try:
        opponent = await bot.get_chat(opponent_id)
    except:
        await message.answer("Не удалось получить данные пользователя. Попробуй позже.")
        return

    if opponent_id == message.from_user.id:
        await message.answer("Нельзя вызвать самого себя.")
        return

    # Проверяем активные дуэли (игнорируем завершённые)
    for d in duels.values():
        if d.get('finished', False):
            continue
        if message.from_user.id in (d['challenger_id'], d['opponent_id']):
            await message.answer("Ты уже в активной дуэли. Закончи её сначала.")
            return
        if opponent_id in (d['challenger_id'], d['opponent_id']):
            await message.answer("Оппонент сейчас в активной дуэли. Подожди.")
            return

    if message.from_user.id in matchmaking_queue:
        await message.answer("Ты уже в очереди поиска. Используй /cancel, чтобы выйти.")
        return
    if opponent_id in matchmaking_queue:
        await message.answer("Оппонент сейчас в очереди поиска. Подожди.")
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
    await message.answer(f"Вызов отправлен @{target_username}.")

    async def decline_timeout():
        await asyncio.sleep(30)
        duel = duels.get(challenger.id)
        if duel and duel['stage'] == 'waiting_accept' and not duel.get('finished'):
            await bot.send_message(challenger.id, "Противник не ответил. Он даун.")
            await bot.send_message(opponent_id, "Ты не принял вызов. Ты даун.")
            duels.pop(challenger.id, None)
    asyncio.create_task(decline_timeout())

# ========== СЛУЧАЙНЫЙ ПОДБОР ==========
@dp.message(Command("find"))
async def cmd_find(message: types.Message):
    user_id = message.from_user.id
    update_user_info(user_id, message.from_user.username or "")

    # Проверяем активные дуэли (игнорируем завершённые)
    for d in duels.values():
        if d.get('finished', False):
            continue
        if user_id in (d['challenger_id'], d['opponent_id']):
            await message.answer("Ты уже в активной дуэли. Закончи её сначала.")
            return

    if user_id in matchmaking_queue:
        await message.answer("Ты уже в очереди. Ожидай соперника.")
        return

    matchmaking_queue.append(user_id)
    await message.answer("Ты добавлен в очередь поиска соперника. Как только найдётся второй игрок, дуэль начнётся автоматически.")

    if len(matchmaking_queue) >= 2:
        player1 = matchmaking_queue.pop(0)
        player2 = matchmaking_queue.pop(0)
        success = await start_duel(player1, player2)
        if not success:
            await bot.send_message(player1, "Не удалось начать дуэль. Попробуй снова.")
            await bot.send_message(player2, "Не удалось начать дуэль. Попробуй снова.")
            matchmaking_queue.append(player1)
            matchmaking_queue.append(player2)

# ========== ВЫХОД ИЗ ОЧЕРЕДИ ==========
@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in matchmaking_queue:
        matchmaking_queue.remove(user_id)
        await message.answer("Ты вышел из очереди.")
    else:
        await message.answer("Ты не в очереди.")

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
    await bot.send_message(duel['challenger_id'], f"🔥 {callback.from_user.full_name} принял вызов!")

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
    await bot.send_message(duel['challenger_id'], "Противник отказался. Ищи другого.")
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
                    await bot.send_message(user_id, "⏰ Время вышло!")
            duel['round'] += 1
            await start_round(challenger_id)

    duel['timer_task'] = asyncio.create_task(timeout_round())

# ========== ОБРАБОТКА КНОПОК ==========
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
            await bot.send_message(user_id, "✅ Верно!")
        else:
            await bot.send_message(user_id, "❌ Неверно.")

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
                await bot.send_message(duel['challenger_id'], msg)
                await bot.send_message(duel['opponent_id'], msg)
            elif ans_c['correct'] and not ans_o['correct']:
                duel['scores'][duel['challenger_id']] += 1
                await bot.send_message(duel['challenger_id'], "Ты ответил правильно, соперник ошибся. Ты получаешь очко!")
                await bot.send_message(duel['opponent_id'], "Ты ошибся, соперник ответил правильно. Он получает очко.")
            elif ans_o['correct'] and not ans_c['correct']:
                duel['scores'][duel['opponent_id']] += 1
                await bot.send_message(duel['opponent_id'], "Ты ответил правильно, соперник ошибся. Ты получаешь очко!")
                await bot.send_message(duel['challenger_id'], "Ты ошибся, соперник ответил правильно. Он получает очко.")
            else:
                await bot.send_message(duel['challenger_id'], "Оба неправильно. Без очков.")
                await bot.send_message(duel['opponent_id'], "Оба неправильно. Без очков.")

            score_c = duel['scores'][duel['challenger_id']]
            score_o = duel['scores'][duel['opponent_id']]
            await bot.send_message(duel['challenger_id'], f"Счёт: {duel['challenger_name']} {score_c} — {score_o} {duel['opponent_name']}")
            await bot.send_message(duel['opponent_id'], f"Счёт: {duel['challenger_name']} {score_c} — {score_o} {duel['opponent_name']}")

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
            update_stats(duel['challenger_id'], True)
            update_stats(duel['opponent_id'], False)
        elif score_o > score_c:
            update_stats(duel['opponent_id'], True)
            update_stats(duel['challenger_id'], False)

        verdict = get_winner_verdict(score_c, score_o, duel['challenger_name'], duel['opponent_name'])
        await bot.send_message(duel['challenger_id'], verdict)
        await bot.send_message(duel['opponent_id'], verdict)

        await dp.storage.clear()
    finally:
        duels.pop(challenger_id, None)

# ========== ЗАПУСК ==========
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())