from datetime import datetime, timedelta
import hashlib
import hmac
import http.server
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

import extras
import food_db
import menu_data

APP_VERSION = "1.1"
PORT = int(os.environ.get("PORT", 8000))
# Токен бота из @BotFather. Если задан, все запросы проверяются по подписи Telegram.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
# Строка подключения к Postgres (Neon): postgresql://user:password@host/dbname?sslmode=require
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
POOL_MAX = max(1, int(os.environ.get("POOL_MAX", "5")))
POOL = None  # создаётся в open_pool()
MAX_BODY = 5 * 1024 * 1024
INIT_DATA_MAX_AGE = 24 * 60 * 60
FEEDBACK_PER_DAY = 10
# Куда присылать новые отзывы: id вашего чата с ботом (узнать у @userinfobot). Необязательно.
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()
# Контакт оператора для политики конфиденциальности (почта или @username). Необязательно.
PRIVACY_CONTACT = os.environ.get("PRIVACY_CONTACT", "").strip()[:100]

MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
CUSTOM_MEAL_TYPES = MEAL_TYPES | {"any", "none"}  # none = не использовать в автоменю
EXCLUDABLE = {"fish", "meat", "dairy"}

SETTINGS_NUMERIC = {
    "weight": (20, 400),
    "target_weight": (20, 400),
    "target_cal": (500, 10000),
    "target_p": (0, 1000),
    "target_f": (0, 1000),
    "target_c": (0, 2000),
    "target_water": (0, 10000),
    "target_steps": (0, 100000),
    "water_glass": (100, 1000),
    "age": (10, 100),
    "height": (100, 250),
    "activity": (1.0, 2.5),
    "goal_rate": (0.1, 1.5),
    "program_day": (0, 20),
}
SETTINGS_CHOICES = {
    "sex": {"male", "female"},
    "goal": {"lose", "maintain", "gain"},
    "theme": {"light", "dark", "auto"},
}
SETTING_DEFAULTS = {
    "weight": 75.0, "target_weight": 73.0, "target_cal": 1850, "target_p": 138,
    "target_f": 62, "target_c": 185, "target_water": 2250, "target_steps": 8000,
    "water_glass": 250, "age": 30, "height": 170, "activity": 1.375, "goal_rate": 0.5,
    "program_day": 0, "sex": "male", "goal": "lose", "theme": "auto", "program": "",
    "onboarded": "0", "consent": "0",
}
INT_SETTINGS = {"target_cal", "target_p", "target_f", "target_c", "target_water",
                "target_steps", "water_glass", "age", "height", "program_day"}
SETTINGS_KEYS = set(SETTINGS_NUMERIC) | set(SETTINGS_CHOICES) | {"onboarded", "consent", "excluded", "program"}

# Названия первых 17 продуктов используются шаблонами автоменю, их менять нельзя.
# Формат: (название, ккал, белки, жиры, углеводы, категория) на 100 г. Значения приблизительные.
DEFAULT_FOODS = [
    ("Куриное филе грудки (вареное/печеное)", 165, 31.0, 3.6, 0.0, "meat"),
    ("Яйцо вареное (2 шт ~ 100г)", 155, 13.0, 11.0, 1.1, "dairy"),
    ("Творог 5%", 121, 17.0, 5.0, 3.0, "dairy"),
    ("Гречневая каша на воде", 110, 4.2, 1.1, 21.3, "grains"),
    ("Рис белый вареный", 130, 2.7, 0.3, 28.0, "grains"),
    ("Овсяная каша на воде", 88, 3.0, 1.7, 15.0, "grains"),
    ("Макароны твердых сортов (вареные)", 140, 5.5, 0.7, 28.0, "grains"),
    ("Говядина постная (отварная)", 187, 26.0, 9.0, 0.0, "meat"),
    ("Индейка филе (готовое)", 150, 29.0, 3.0, 0.0, "meat"),
    ("Рыба белая (треска/минтай)", 82, 17.5, 0.7, 0.0, "fish"),
    ("Сыр твердый 45%", 350, 25.0, 28.0, 0.5, "dairy"),
    ("Хлеб цельнозерновой", 220, 7.0, 1.5, 43.0, "grains"),
    ("Картофель отварной / запеченный", 82, 2.0, 0.4, 16.7, "grains"),
    ("Огурцы свежие", 15, 0.8, 0.1, 2.8, "veg"),
    ("Помидоры свежие", 20, 0.9, 0.2, 3.9, "veg"),
    ("Яблоко свежее", 52, 0.3, 0.2, 13.8, "fruit"),
    ("Банан", 95, 1.5, 0.2, 21.8, "fruit"),
    # мясо и птица
    ("Куриное бедро без кожи (запечённое)", 180, 25.0, 8.5, 0.0, "meat"),
    ("Говяжий фарш (приготовленный)", 250, 26.0, 15.0, 0.0, "meat"),
    ("Свинина постная (запечённая)", 210, 27.0, 11.0, 0.0, "meat"),
    ("Печень куриная (отварная)", 166, 24.0, 6.5, 1.0, "meat"),
    ("Ветчина из индейки", 110, 17.0, 4.0, 2.0, "meat"),
    ("Колбаса варёная (докторская)", 260, 13.0, 22.0, 1.5, "meat"),
    ("Сосиски молочные", 260, 11.0, 23.0, 1.5, "meat"),
    # рыба и морепродукты
    ("Лосось (запечённый)", 206, 22.0, 13.0, 0.0, "fish"),
    ("Тунец консервированный в собственном соку", 116, 26.0, 1.0, 0.0, "fish"),
    ("Скумбрия (запечённая)", 230, 20.0, 16.0, 0.0, "fish"),
    ("Сельдь слабосолёная", 217, 18.0, 16.0, 0.0, "fish"),
    ("Креветки варёные", 99, 24.0, 0.3, 0.0, "fish"),
    # молочные продукты
    ("Творог обезжиренный", 71, 16.0, 0.2, 1.3, "dairy"),
    ("Творог 9%", 159, 16.0, 9.0, 2.0, "dairy"),
    ("Молоко 2,5%", 52, 2.8, 2.5, 4.7, "dairy"),
    ("Кефир 1%", 40, 3.0, 1.0, 4.0, "dairy"),
    ("Йогурт натуральный 3,2%", 61, 3.5, 3.2, 4.7, "dairy"),
    ("Йогурт греческий 2%", 73, 10.0, 2.0, 4.0, "dairy"),
    ("Сметана 15%", 158, 2.6, 15.0, 3.0, "dairy"),
    ("Сыр моцарелла", 250, 18.0, 19.0, 2.0, "dairy"),
    ("Масло сливочное 82,5%", 748, 0.5, 82.5, 0.8, "dairy"),
    # крупы, хлеб, гарниры
    ("Рис бурый вареный", 123, 2.7, 1.0, 25.6, "grains"),
    ("Булгур варёный", 83, 3.0, 0.2, 18.6, "grains"),
    ("Киноа варёная", 120, 4.4, 1.9, 21.3, "grains"),
    ("Перловая каша на воде", 109, 3.1, 0.4, 22.2, "grains"),
    ("Пшённая каша на воде", 90, 3.0, 0.7, 17.0, "grains"),
    ("Овсяные хлопья (сухие)", 370, 13.0, 7.0, 60.0, "grains"),
    ("Мюсли без сахара", 360, 10.0, 6.0, 66.0, "grains"),
    ("Картофель жареный", 190, 3.0, 9.0, 23.0, "grains"),
    ("Картофельное пюре с молоком", 88, 2.0, 3.0, 14.0, "grains"),
    ("Хлеб белый", 265, 8.0, 3.0, 50.0, "grains"),
    ("Лаваш тонкий", 275, 9.0, 1.0, 56.0, "grains"),
    # бобовые
    ("Чечевица варёная", 116, 9.0, 0.4, 20.0, "grains"),
    ("Нут варёный", 164, 8.9, 2.6, 27.0, "grains"),
    ("Фасоль красная варёная", 127, 8.7, 0.5, 23.0, "grains"),
    ("Тофу", 76, 8.0, 4.8, 1.9, "other"),
    # овощи и зелень
    ("Морковь", 35, 1.3, 0.1, 7.0, "veg"),
    ("Капуста белая", 27, 1.8, 0.1, 4.7, "veg"),
    ("Брокколи (варёная)", 35, 2.4, 0.4, 7.0, "veg"),
    ("Цветная капуста", 30, 2.5, 0.3, 4.2, "veg"),
    ("Перец болгарский", 27, 1.3, 0.1, 5.3, "veg"),
    ("Кабачок", 24, 0.6, 0.3, 4.6, "veg"),
    ("Баклажан", 24, 1.2, 0.1, 4.5, "veg"),
    ("Свёкла варёная", 49, 1.8, 0.1, 10.0, "veg"),
    ("Лук репчатый", 41, 1.4, 0.2, 9.5, "veg"),
    ("Шпинат", 23, 2.9, 0.4, 3.6, "veg"),
    ("Салат листовой", 15, 1.4, 0.2, 2.9, "veg"),
    ("Кукуруза консервированная", 119, 2.5, 1.2, 22.0, "veg"),
    ("Горошек зелёный консервированный", 55, 3.1, 0.2, 9.0, "veg"),
    ("Авокадо", 160, 2.0, 14.7, 8.5, "fruit"),
    # фрукты и ягоды
    ("Апельсин", 47, 0.9, 0.1, 11.8, "fruit"),
    ("Мандарин", 53, 0.8, 0.3, 13.0, "fruit"),
    ("Грейпфрут", 42, 0.8, 0.1, 10.7, "fruit"),
    ("Груша", 57, 0.4, 0.1, 15.0, "fruit"),
    ("Виноград", 69, 0.7, 0.2, 18.0, "fruit"),
    ("Киви", 61, 1.1, 0.5, 15.0, "fruit"),
    ("Клубника", 32, 0.7, 0.3, 7.7, "fruit"),
    ("Черника", 57, 0.7, 0.3, 14.0, "fruit"),
    ("Арбуз", 30, 0.6, 0.2, 7.6, "fruit"),
    ("Персик", 39, 0.9, 0.3, 9.5, "fruit"),
    ("Ананас", 50, 0.5, 0.1, 13.0, "fruit"),
    ("Манго", 60, 0.8, 0.4, 15.0, "fruit"),
    ("Изюм", 299, 3.1, 0.5, 79.0, "fruit"),
    ("Курага", 241, 3.4, 0.5, 63.0, "fruit"),
    ("Финики", 282, 2.5, 0.4, 75.0, "fruit"),
    # орехи, семена, масла
    ("Миндаль", 579, 21.0, 50.0, 22.0, "other"),
    ("Грецкий орех", 654, 15.0, 65.0, 14.0, "other"),
    ("Арахис", 567, 26.0, 49.0, 16.0, "other"),
    ("Кешью", 553, 18.0, 44.0, 30.0, "other"),
    ("Арахисовая паста", 588, 25.0, 50.0, 20.0, "other"),
    ("Семена подсолнечника", 584, 21.0, 51.0, 20.0, "other"),
    ("Семена чиа", 486, 17.0, 31.0, 42.0, "other"),
    ("Масло оливковое", 884, 0.0, 100.0, 0.0, "other"),
    ("Масло подсолнечное", 899, 0.0, 99.9, 0.0, "other"),
    # сладкое и снеки
    ("Шоколад тёмный 70%", 598, 7.8, 43.0, 46.0, "other"),
    ("Шоколад молочный", 535, 7.7, 30.0, 59.0, "other"),
    ("Мёд", 304, 0.3, 0.0, 82.0, "other"),
    ("Сахар", 387, 0.0, 0.0, 100.0, "other"),
    ("Печенье овсяное", 437, 6.5, 14.0, 71.0, "other"),
    ("Мороженое пломбир", 227, 4.0, 15.0, 21.0, "other"),
    ("Круассан", 406, 8.0, 21.0, 46.0, "other"),
    ("Протеиновый батончик", 370, 30.0, 12.0, 35.0, "other"),
    # напитки
    ("Кофе чёрный без сахара", 2, 0.1, 0.0, 0.3, "other"),
    ("Чай без сахара", 1, 0.0, 0.0, 0.3, "other"),
    ("Сок апельсиновый", 45, 0.7, 0.2, 10.0, "other"),
    ("Газировка (кола)", 42, 0.0, 0.0, 10.6, "other"),
    ("Пиво светлое", 43, 0.5, 0.0, 3.6, "other"),
    ("Вино красное сухое", 85, 0.1, 0.0, 2.6, "other"),
    # готовые блюда
    ("Борщ", 50, 2.0, 2.5, 5.0, "other"),
    ("Суп куриный с лапшой", 40, 3.0, 1.5, 4.0, "other"),
    ("Плов с курицей", 140, 6.5, 5.0, 17.0, "other"),
    ("Пельмени варёные", 275, 12.0, 12.4, 29.0, "other"),
    ("Блины", 233, 6.0, 12.0, 26.0, "other"),
    ("Сырники", 220, 15.0, 9.0, 19.0, "other"),
    ("Пицца Маргарита", 266, 11.0, 10.0, 33.0, "other"),
]

# MET: затраты энергии в долях от покоя. Ккал = MET × вес (кг) × часы.
DEFAULT_WORKOUTS = [
    ("Спокойная ходьба", 3.0),
    ("Быстрая ходьба", 4.3),
    ("Бег в среднем темпе", 9.8),
    ("Велосипед, прогулочный темп", 6.8),
    ("Плавание", 6.0),
    ("Скакалка", 11.0),
    ("Йога", 2.5),
    ("Зарядка и разминка", 3.0),
    ("Домашняя тренировка с собственным весом", 5.0),
    ("Интервальная тренировка (HIIT)", 8.0),
    ("Силовая тренировка в зале", 5.0),
]

DEFAULT_EXERCISES = [
    "Приседания со штангой", "Жим ногами", "Румынская тяга", "Выпады с гантелями",
    "Болгарские приседания", "Сгибание ног лёжа", "Разгибание ног сидя", "Подъём на носки",
    "Ягодичный мостик", "Приседания", "Выпады", "Приседания с прыжком", "Выпады назад",
    "Жим лёжа", "Жим гантелей лёжа", "Жим на наклонной скамье", "Разводка гантелей",
    "Отжимания", "Отжимания на брусьях", "Отжимания узким хватом",
    "Становая тяга", "Тяга штанги в наклоне", "Тяга верхнего блока", "Тяга нижнего блока",
    "Подтягивания", "Тяга гантели в наклоне", "Гиперэкстензия",
    "Жим стоя", "Жим гантелей сидя", "Махи гантелей в стороны", "Тяга к подбородку",
    "Сгибание рук с гантелями", "Сгибание рук со штангой", "Разгибание рук на блоке",
    "Французский жим", "Обратные отжимания от стула",
    "Планка", "Боковая планка", "Скручивания", "Подъём ног в висе", "Берпи",
]


def _ex(name, sets, reps):
    return {"name": name, "sets": sets, "reps": reps}


DEFAULT_PROGRAMS = [
    {
        "id": "fullbody3",
        "name": "Фулбоди, 3 дня в неделю",
        "level": "Новичок",
        "place": "Зал",
        "desc": "Три тренировки на всё тело с днём отдыха между ними. Например, понедельник, среда, пятница.",
        "days": [
            {"name": "День A", "exercises": [
                _ex("Приседания со штангой", 3, "8"), _ex("Жим лёжа", 3, "8"),
                _ex("Тяга штанги в наклоне", 3, "8"), _ex("Планка", 3, "40 сек")]},
            {"name": "День B", "exercises": [
                _ex("Румынская тяга", 3, "8"), _ex("Жим стоя", 3, "8"),
                _ex("Тяга верхнего блока", 3, "10"), _ex("Скручивания", 3, "15")]},
            {"name": "День C", "exercises": [
                _ex("Жим ногами", 3, "10"), _ex("Жим гантелей лёжа", 3, "10"),
                _ex("Подтягивания", 3, "макс."), _ex("Сгибание рук с гантелями", 3, "12")]},
        ],
    },
    {
        "id": "upperlower4",
        "name": "Верх и низ, 4 дня в неделю",
        "level": "Средний уровень",
        "place": "Зал",
        "desc": "Два дня на верх тела и два на низ. Подходит, если базовая техника уже освоена.",
        "days": [
            {"name": "Верх A", "exercises": [
                _ex("Жим лёжа", 4, "6-8"), _ex("Тяга штанги в наклоне", 4, "8"),
                _ex("Жим стоя", 3, "8-10"), _ex("Подтягивания", 3, "макс."),
                _ex("Сгибание рук с гантелями", 3, "10-12"), _ex("Разгибание рук на блоке", 3, "10-12")]},
            {"name": "Низ A", "exercises": [
                _ex("Приседания со штангой", 4, "6-8"), _ex("Румынская тяга", 3, "8-10"),
                _ex("Жим ногами", 3, "10-12"), _ex("Выпады с гантелями", 3, "10"),
                _ex("Подъём на носки", 4, "12-15")]},
            {"name": "Верх B", "exercises": [
                _ex("Жим гантелей лёжа", 4, "8-10"), _ex("Тяга верхнего блока", 4, "10"),
                _ex("Жим гантелей сидя", 3, "10"), _ex("Отжимания на брусьях", 3, "8-12"),
                _ex("Тяга гантели в наклоне", 3, "10")]},
            {"name": "Низ B", "exercises": [
                _ex("Становая тяга", 3, "5"), _ex("Жим ногами", 3, "12"),
                _ex("Выпады с гантелями", 3, "10"), _ex("Подъём на носки", 3, "15"),
                _ex("Планка", 3, "60 сек")]},
        ],
    },
    {
        "id": "home3",
        "name": "Дома без инвентаря, 3 дня в неделю",
        "level": "Новичок",
        "place": "Дом",
        "desc": "Упражнения с собственным весом. В поле веса записывайте 0, в повторения число раз или секунд.",
        "days": [
            {"name": "День 1", "exercises": [
                _ex("Приседания", 4, "15"), _ex("Отжимания", 4, "макс."),
                _ex("Выпады", 3, "12 на ногу"), _ex("Планка", 3, "45 сек")]},
            {"name": "День 2", "exercises": [
                _ex("Ягодичный мостик", 4, "15"), _ex("Отжимания узким хватом", 3, "макс."),
                _ex("Скручивания", 3, "20"), _ex("Берпи", 3, "10")]},
            {"name": "День 3", "exercises": [
                _ex("Приседания с прыжком", 3, "12"), _ex("Обратные отжимания от стула", 3, "12"),
                _ex("Выпады назад", 3, "12"), _ex("Боковая планка", 3, "30 сек")]},
        ],
    },
]
PROGRAM_IDS = {p["id"] for p in DEFAULT_PROGRAMS}
PROGRAM_EXERCISES = {e["name"] for p in DEFAULT_PROGRAMS for d in p["days"] for e in d["exercises"]}
ALL_DEFAULT_EXERCISES = sorted(set(DEFAULT_EXERCISES) | PROGRAM_EXERCISES)

DEFAULT_FOODS += [f for f in menu_data.EXTRA_FOODS if f[0] not in {x[0] for x in DEFAULT_FOODS}]

SIDE_NAMES = ["Яблоко свежее", "Банан", "Огурцы свежие", "Помидоры свежие", "Хлеб цельнозерновой"]

MEAL_TEMPLATES = {
    "breakfast": [
        [
            ("Овсяная каша на воде", 0.55),
            ("Яйцо вареное (2 шт ~ 100г)", 0.45),
        ],
        [
            ("Яйцо вареное (2 шт ~ 100г)", 0.45),
            ("Хлеб цельнозерновой", 0.35),
            ("Сыр твердый 45%", 0.20),
        ],
        [
            ("Творог 5%", 0.60),
            ("Банан", 0.40),
        ],
    ],
    "lunch": [
        [
            ("Куриное филе грудки (вареное/печеное)", 0.45),
            ("Гречневая каша на воде", 0.45),
            ("Огурцы свежие", 0.10),
        ],
        [
            ("Индейка филе (готовое)", 0.45),
            ("Рис белый вареный", 0.45),
            ("Помидоры свежие", 0.10),
        ],
        [
            ("Рыба белая (треска/минтай)", 0.50),
            ("Картофель отварной / запеченный", 0.40),
            ("Огурцы свежие", 0.10),
        ],
    ],
    "dinner": [
        [
            ("Куриное филе грудки (вареное/печеное)", 0.55),
            ("Картофель отварной / запеченный", 0.35),
            ("Помидоры свежие", 0.10),
        ],
        [
            ("Индейка филе (готовое)", 0.55),
            ("Гречневая каша на воде", 0.35),
            ("Огурцы свежие", 0.10),
        ],
        [
            ("Рыба белая (треска/минтай)", 0.60),
            ("Рис белый вареный", 0.30),
            ("Огурцы свежие", 0.10),
        ],
    ],
    "snack": [
        [
            ("Творог 5%", 0.65),
            ("Яблоко свежее", 0.35),
        ],
        [
            ("Хлеб цельнозерновой", 0.45),
            ("Сыр твердый 45%", 0.35),
            ("Яблоко свежее", 0.20),
        ],
        [
            ("Творог 5%", 0.55),
            ("Банан", 0.45),
        ],
    ],
}

# ---------------------------------------------------------------- database

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS settings (
        telegram_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY (telegram_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS entries (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        weight DOUBLE PRECISION DEFAULT 0,
        calories INTEGER DEFAULT 0,
        steps INTEGER DEFAULT 0,
        note TEXT DEFAULT '',
        water_ml INTEGER DEFAULT 0,
        UNIQUE (telegram_id, date)
    )""",
    """CREATE TABLE IF NOT EXISTS meal_log (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        meal_type TEXT DEFAULT 'breakfast',
        food_name TEXT,
        grams DOUBLE PRECISION,
        calories DOUBLE PRECISION,
        protein DOUBLE PRECISION DEFAULT 0,
        fat DOUBLE PRECISION DEFAULT 0,
        carbs DOUBLE PRECISION DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS workout_log (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        workout_name TEXT,
        duration_min DOUBLE PRECISION,
        calories_burned DOUBLE PRECISION
    )""",
    """CREATE TABLE IF NOT EXISTS custom_foods (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        name TEXT,
        calories DOUBLE PRECISION,
        protein DOUBLE PRECISION,
        fat DOUBLE PRECISION,
        carbs DOUBLE PRECISION
    )""",
    # Миграция: в таблицах, созданных первой версией, этой колонки нет.
    "ALTER TABLE custom_foods ADD COLUMN IF NOT EXISTS meal_type TEXT DEFAULT 'none'",
    """CREATE TABLE IF NOT EXISTS strength_sets (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        date TEXT NOT NULL,
        exercise TEXT,
        weight DOUBLE PRECISION,
        reps INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS feedback (
        id BIGSERIAL PRIMARY KEY,
        telegram_id TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        kind TEXT NOT NULL DEFAULT 'idea',
        text TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_meal_user_date ON meal_log (telegram_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_workout_user_date ON workout_log (telegram_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_sets_user_date ON strength_sets (telegram_id, date)",
    "CREATE INDEX IF NOT EXISTS idx_custom_user ON custom_foods (telegram_id)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback (telegram_id, created_at)",
]

USER_TABLES = ("settings", "entries", "meal_log", "workout_log", "custom_foods",
               "strength_sets", "feedback")


def open_pool():
    """Создаёт пул соединений с Postgres. Без DATABASE_URL сервер не запускается."""
    global POOL
    if not DATABASE_URL:
        sys.exit(
            "ОШИБКА: не задана переменная окружения DATABASE_URL "
            "(строка подключения Neon, начинается с postgresql://)."
        )
    POOL = ConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=POOL_MAX,
        # Neon усыпляет бесплатную базу через 5 минут простоя: закрываем простаивающие
        # соединения заранее и проверяем соединение при выдаче из пула.
        max_idle=240,
        max_lifetime=1800,
        timeout=30,
        check=ConnectionPool.check_connection,
        # prepare_threshold=None нужен для пулера Neon (PgBouncer).
        kwargs={"connect_timeout": 15, "prepare_threshold": None},
        open=False,
    )
    try:
        POOL.open(wait=True, timeout=60)
    except PoolTimeout:
        sys.exit(
            "ОШИБКА: не удалось подключиться к базе за 60 секунд. "
            "Проверьте DATABASE_URL (пароль, адрес, ?sslmode=require)."
        )


def init_db():
    with POOL.connection() as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)


# ---------------------------------------------------------------- helpers

def valid_date(s):
    if not isinstance(s, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def need_date(s):
    if not valid_date(s):
        raise ValueError("bad date")
    return s


def num(v, lo, hi):
    x = float(v)
    if x != x or x < lo or x > hi:
        raise ValueError("out of range")
    return x


def need_text(v, max_len=100):
    s = str(v).strip()
    if not s or len(s) > max_len:
        raise ValueError("bad text")
    return s


def verify_init_data(init_data, token, max_age=INIT_DATA_MAX_AGE):
    """Проверка подписи Telegram WebApp initData. Возвращает id пользователя или None."""
    if not init_data:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop("hash", None)
        if not received:
            return None
        check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        uid = json.loads(pairs.get("user", "{}")).get("id")
        return str(uid) if uid else None
    except Exception:
        return None


def extract_user_id(init_data_str, fallback_id="default_user"):
    """Режим разработки (без BOT_TOKEN): id берётся без проверки подписи."""
    fallback_id = str(fallback_id)[:64] or "default_user"
    if not init_data_str:
        return fallback_id
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data_str, keep_blank_values=True))
        user_obj = json.loads(parsed.get("user", "{}"))
        uid = user_obj.get("id")
        return str(uid) if uid else fallback_id
    except Exception:
        return fallback_id


def get_user_settings(cur, user_id):
    cur.execute("SELECT key, value FROM settings WHERE telegram_id = %s", (user_id,))
    rows = dict(cur.fetchall())
    out = {}
    for key, default in SETTING_DEFAULTS.items():
        raw = rows.get(key)
        if raw is None:
            out[key] = default
        elif key in INT_SETTINGS:
            out[key] = int(float(raw))
        elif isinstance(default, float):
            out[key] = float(raw)
        else:
            out[key] = raw
    try:
        excluded = json.loads(rows.get("excluded", "[]"))
        if not isinstance(excluded, list):
            excluded = []
    except ValueError:
        excluded = []
    out["excluded"] = excluded
    return out


def clean_setting(key, value):
    """Возвращает строку для хранения или бросает ValueError."""
    if key in ("onboarded", "consent"):
        return "1" if str(value) == "1" else "0"
    if key == "excluded":
        if not isinstance(value, list):
            raise ValueError("excluded must be a list")
        return json.dumps([v for v in value if v in EXCLUDABLE])
    if key in SETTINGS_CHOICES:
        v = str(value)
        if v not in SETTINGS_CHOICES[key]:
            raise ValueError("bad choice")
        return v
    if key == "program":
        v = str(value)
        if v and v not in PROGRAM_IDS:
            raise ValueError("bad program")
        return v
    lo, hi = SETTINGS_NUMERIC[key]
    return str(num(value, lo, hi))


def sync_daily_calories(cur, user_id, date_str):
    cur.execute(
        "SELECT SUM(calories) FROM meal_log WHERE telegram_id = %s AND date = %s",
        (user_id, date_str),
    )
    total_cal = int(round(cur.fetchone()[0] or 0))
    cur.execute(
        """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
           VALUES (%s, %s, 0, %s, 0, '', 0)
           ON CONFLICT(telegram_id, date) DO UPDATE SET calories = excluded.calories""",
        (user_id, date_str, total_cal),
    )


def compute_streak(cur, user_id, today_str):
    """Дней подряд с любой активностью. Сегодняшний день без записей серию не обрывает."""
    cur.execute(
        """SELECT date FROM entries
             WHERE telegram_id = %s AND (calories > 0 OR water_ml > 0 OR weight > 0 OR steps > 0)
           UNION SELECT date FROM workout_log WHERE telegram_id = %s
           UNION SELECT date FROM strength_sets WHERE telegram_id = %s""",
        (user_id, user_id, user_id),
    )
    dates = {r[0] for r in cur.fetchall()}
    fmt = "%Y-%m-%d"
    day = datetime.strptime(today_str, fmt)
    if day.strftime(fmt) not in dates:
        day -= timedelta(days=1)
    streak = 0
    while day.strftime(fmt) in dates and streak < 3650:
        streak += 1
        day -= timedelta(days=1)
    return streak


# ---------------------------------------------------------------- web app

WEB_APP_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="theme-color" content="#F2F2F7">
<title>Grammika</title>
<script>
(function () {
  try {
    var p = localStorage.getItem('gm_theme') || 'auto', t = p;
    if (p === 'auto') {
      var tgw = window.Telegram && window.Telegram.WebApp;
      t = (tgw && tgw.colorScheme) || (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    }
    document.documentElement.setAttribute('data-theme', t);
  } catch (e) {}
})();
</script>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root {
  --bg: #F2F4F7; --surface: #FFFFFF; --surface-2: #F2F4F7; --line: #E6E8EC;
  --ink: #101828; --ink-2: #475467; --ink-3: #98A2B3;
  --accent: #22C08A; --accent-ink: #FFFFFF; --accent-soft: #E3F8EF;
  --prot: #FF7A6B; --fat: #FFA43A; --carb: #3D8BFF; --water: #36B5F0;
  --shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 6px 20px rgba(16, 24, 40, .05);
  --danger: #FF3B30; --danger-soft: #FFE5E3;
  --s1: #FFAD33; --s2: #2FBF71; --s3: #7B7BFF; --s4: #FF6FA8;
  --s1-soft: #FFF3E0; --s2-soft: #E2F7EB; --s3-soft: #ECECFF; --s4-soft: #FFE8F1;
  --ring-track: #EDF0F3;
  --overlay: rgba(16, 24, 40, 0.38);
  --blur-bg: rgba(242, 244, 247, 0.88);
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", Inter, "Helvetica Neue", "Segoe UI", Roboto, "Noto Sans", sans-serif;
  --round: ui-rounded, "SF Pro Rounded", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
:root[data-theme="dark"] {
  --bg: #000000; --surface: #1C1C1E; --surface-2: #2C2C2E; --line: #38383A;
  --ink: #F2F2F7; --ink-2: #C7C7CC; --ink-3: #8E8E93;
  --accent: #34D399; --accent-ink: #04241A; --accent-soft: #123026;
  --shadow: none;
  --prot: #FF7F6B; --fat: #FFB547; --carb: #6EA2FF; --water: #5AC8FA;
  --danger: #FF5A50; --danger-soft: #3A1A18;
  --s1: #FFB547; --s2: #32D27A; --s3: #8E8EFF; --s4: #FF7EB3;
  --s1-soft: #3A2A10; --s2-soft: #15301F; --s3-soft: #23234A; --s4-soft: #3D1A2A;
  --ring-track: #2C2C2E;
  --overlay: rgba(0, 0, 0, 0.6);
  --blur-bg: rgba(22, 22, 24, 0.82);
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 400 16px/1.38 var(--font); letter-spacing: -0.01em; font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; padding-bottom: calc(84px + env(safe-area-inset-bottom)); }
body.noscroll { overflow: hidden; }
button, input, select, textarea { font: inherit; color: inherit; letter-spacing: inherit; }
button { cursor: pointer; border: 0; background: none; padding: 0; }
button:focus-visible, input:focus-visible, select:focus-visible, label:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
.i { width: 20px; height: 20px; stroke: currentColor; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; flex: none; }
.vh { position: absolute; width: 1px; height: 1px; overflow: hidden; opacity: 0; pointer-events: none; }
.wrap { max-width: 560px; margin: 0 auto; padding: 0 16px; }
.num { font-family: var(--round); font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }

/* верхняя панель */
.top { position: sticky; top: 0; z-index: 20; background: var(--blur-bg); -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px); }
.top .wrap { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; height: 54px; }
.streak { justify-self: start; display: inline-flex; align-items: center; gap: 4px; height: 30px; padding: 0 11px 0 8px; border-radius: 15px; background: var(--surface); font: 600 14px var(--round); }
.streak { box-shadow: var(--shadow); }
.streak .i { width: 17px; height: 17px; color: var(--fat); fill: var(--fat); fill-opacity: .25; transform-origin: 50% 80%; }
.streak.live .i { animation: flame 1.8s ease-in-out infinite; }
@keyframes flame { 0%, 100% { transform: scale(1); filter: drop-shadow(0 0 0 rgba(255, 164, 58, 0)); } 50% { transform: scale(1.16); filter: drop-shadow(0 0 5px rgba(255, 164, 58, .65)); } }
/* появление карточек при прокрутке */
.rise { opacity: 0; transform: translateY(14px); }
.rise.in { opacity: 1; transform: none; transition: opacity .25s ease-out, transform .25s ease-out; }
.top-title { font-weight: 600; font-size: 17px; text-align: center; }
.datenav { display: flex; align-items: center; gap: 0; }
.datenav .nav { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 17px; color: var(--accent); }
.datenav .nav:active { background: var(--surface); }
.datelabel { position: relative; display: block; min-width: 112px; text-align: center; font-weight: 600; font-size: 17px; padding: 6px 4px; border-radius: 10px; }
.datelabel input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; }
.avatar { justify-self: end; position: relative; width: 32px; height: 32px; border-radius: 50%; overflow: hidden; background: var(--accent-soft); color: var(--accent); display: grid; place-items: center; font-weight: 700; font-size: 14px; }
.avatar img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; }

/* общие блоки */
.tab { display: none; padding-top: 4px; }
.tab.active { display: block; }
.h2 { margin: 28px 4px 12px; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.h2 small { font-size: 14px; font-weight: 400; color: var(--ink-3); letter-spacing: 0; }
.group { background: var(--surface); border-radius: 22px; overflow: hidden; box-shadow: var(--shadow); }
.pad { padding: 16px; }
.row { position: relative; display: flex; align-items: center; gap: 12px; min-height: 56px; padding: 10px 16px; width: 100%; text-align: left; }
.group > .row + .row::before, .group > * + .row::before, .group > .row + *::before { content: ""; position: absolute; top: 0; left: 16px; right: 0; border-top: 0.5px solid var(--line); }
.group > .row + * { position: relative; }
.group > .row.thumbed + .row.thumbed::before { left: 72px; }
button.row:active, a.row:active, label.row:active { background: var(--surface-2); }
.grow { flex: 1; min-width: 0; }
.ttl { font-weight: 600; font-size: 16px; line-height: 1.3; }
.sub { font-size: 13px; color: var(--ink-3); font-weight: 400; line-height: 1.35; }
.sub.i2 { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 2px; }
.mut { color: var(--ink-3); }
.ok { color: var(--accent); }
.bad { color: var(--danger); }
.end { margin-left: auto; font-weight: 600; white-space: nowrap; font-family: var(--round); }
.chev { color: var(--ink-3); width: 17px; height: 17px; opacity: .6; }
.ico-box { width: 32px; height: 32px; border-radius: 9px; display: grid; place-items: center; background: var(--accent); color: #fff; flex: none; }
.ico-box .i { width: 18px; height: 18px; }
.empty { padding: 20px 16px; color: var(--ink-2); font-size: 15px; }
.empty b { color: var(--ink); }

/* превью продукта: фото, упаковка или эмодзи */
.thumb { width: 44px; height: 44px; border-radius: 12px; flex: none; display: grid; place-items: center; font-size: 24px; line-height: 1; background: var(--surface-2); overflow: hidden; position: relative; }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb.pack img { object-fit: contain; background: #fff; padding: 3px; }
.thumb .cam { position: absolute; right: 2px; bottom: 2px; width: 14px; height: 14px; border-radius: 4px; background: rgba(0,0,0,.45); color: #fff; display: grid; place-items: center; }
.thumb .cam .i { width: 10px; height: 10px; stroke-width: 2.4; }

/* кнопки и поля */
.btn { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 50px; padding: 0 18px; border-radius: 14px; background: var(--accent); color: var(--accent-ink); font-weight: 600; font-size: 17px; transition: transform .12s ease, opacity .12s ease; }
.btn:active { transform: scale(.98); opacity: .85; }
.btn[disabled] { opacity: .45; }
.btn.sec { background: var(--surface-2); color: var(--ink); }
.sheet .btn.sec, .group .btn.sec { background: var(--surface-2); }
.btn.soft { background: var(--accent-soft); color: var(--accent); }
.btn.dng { background: var(--danger-soft); color: var(--danger); }
.btn.sm { height: 40px; font-size: 15px; border-radius: 12px; }
.btn.link { background: none; color: var(--accent); height: auto; padding: 6px 0; width: auto; font-weight: 500; font-size: 16px; }
.btns { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 10px; }
.stack > * + * { margin-top: 10px; }
.field { display: block; }
.field > span { display: block; font-size: 13px; font-weight: 500; color: var(--ink-3); margin: 0 4px 6px; }
input, select, textarea { width: 100%; height: 48px; padding: 0 14px; border-radius: 12px; background: var(--surface-2); border: 0; outline: none; font-weight: 400; font-size: 17px; }
.group input, .group select, .group textarea { background: var(--surface-2); }
textarea { height: 120px; padding: 12px 14px; resize: none; line-height: 1.4; }
input:focus, select:focus, textarea:focus { box-shadow: 0 0 0 2px var(--accent); }
input::placeholder, textarea::placeholder { color: var(--ink-3); }
select { appearance: none; background-image: linear-gradient(45deg, transparent 50%, var(--ink-3) 50%), linear-gradient(135deg, var(--ink-3) 50%, transparent 50%); background-position: calc(100% - 19px) 21px, calc(100% - 14px) 21px; background-size: 5px 5px; background-repeat: no-repeat; padding-right: 34px; }
.cols2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.cols3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.seg { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; padding: 2px; background: var(--surface-2); border-radius: 10px; gap: 2px; }
.seg button { height: 32px; border-radius: 8px; font-weight: 500; font-size: 14px; color: var(--ink); }
.seg button.on { background: var(--surface); font-weight: 600; box-shadow: 0 2px 6px rgba(0,0,0,.08), 0 0 0 .5px rgba(0,0,0,.04); }
:root[data-theme="dark"] .seg button.on { background: #636366; }
.chips { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: none; }
.chips::-webkit-scrollbar { display: none; }
.chip { flex: none; height: 34px; padding: 0 14px; border-radius: 17px; background: var(--surface); font-weight: 500; font-size: 14px; color: var(--ink); box-shadow: inset 0 0 0 1px var(--line); }
.sheet .chip { background: var(--surface-2); box-shadow: none; }
.chip.on, .sheet .chip.on { background: var(--ink); color: var(--surface); box-shadow: none; }
.sw { position: relative; width: 51px; height: 31px; border-radius: 16px; background: var(--line); flex: none; transition: background .2s; }
.sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 27px; height: 27px; border-radius: 50%; background: #fff; box-shadow: 0 2px 6px rgba(0,0,0,.18); transition: transform .2s; }
.sw.on { background: var(--accent); }
.sw.on::after { transform: translateX(20px); }
.radio { width: 22px; height: 22px; border-radius: 50%; border: 2px solid var(--line); flex: none; display: grid; place-items: center; }
.radio.on { border-color: var(--accent); }
.radio.on::after { content: ""; width: 10px; height: 10px; border-radius: 50%; background: var(--accent); }
.bar { height: 6px; border-radius: 3px; background: var(--ring-track); overflow: hidden; }
.bar > i { display: block; height: 100%; border-radius: 3px; transition: width .6s cubic-bezier(.3, .7, .2, 1); }

/* главный экран: кольцо калорий */
.hero { background: var(--surface); border-radius: 24px; padding: 22px 20px 20px; margin-top: 4px; box-shadow: var(--shadow); }
.ringrow { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 6px; }
.side { text-align: center; }
.side .v { font: 600 22px/1.1 var(--round); letter-spacing: -0.02em; }
.side .n { margin-top: 4px; font-size: 12px; color: var(--ink-3); display: flex; flex-direction: column; align-items: center; gap: 2px; }
.side .n .i { width: 14px; height: 14px; }
.ring { position: relative; width: 172px; height: 172px; }
.ring svg { width: 100%; height: 100%; transform: rotate(-90deg); display: block; }
.ring circle { fill: none; stroke-width: 14; stroke-linecap: round; }
.ring .trk { stroke: var(--ring-track); }
.ring .val { stroke: var(--accent); transition: stroke-dashoffset 1.1s cubic-bezier(.22, .8, .24, 1), stroke .3s; }
.ring.over .val { stroke: var(--danger); }
.ring .mid { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
.ring .big { font: 700 42px/1 var(--round); letter-spacing: -0.035em; }
.ring.over .big { color: var(--danger); }
.ring .cap { margin-top: 5px; font-size: 13px; color: var(--ink-3); }
.macros { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 22px; }
.macro .n { font-size: 13px; color: var(--ink-2); font-weight: 500; display: flex; align-items: center; gap: 5px; }
.macro .n::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--mc); }
.macro .v { margin: 3px 0 7px; font: 600 17px var(--round); }
.macro .v small { font: 400 13px var(--font); color: var(--ink-3); }
.macro .bar { height: 5px; border-radius: 3px; }
.macro .bar > i { background: var(--mc); }
.budget, .legend, .facts { display: none; }

/* приёмы пищи */
.meal { margin-top: 16px; background: var(--surface); border-radius: 22px; overflow: hidden; box-shadow: var(--shadow); }
.meal-h { display: flex; align-items: center; gap: 14px; padding: 16px 16px 16px 16px; }
.meal-h .emo { width: 48px; height: 48px; border-radius: 12px; display: grid; place-items: center; font-size: 24px; flex: none; background: var(--mc-soft); }
.meal-h .t { font-weight: 600; font-size: 17px; }
.meal-h .k { font-size: 13px; color: var(--ink-3); margin-top: 1px; }
.meal-h .k b { color: var(--ink); font-weight: 600; font-family: var(--round); }
.meal-h .kc { text-align: right; margin-left: auto; line-height: 1.1; }
.meal-h .kc b { display: block; font: 700 19px var(--round); letter-spacing: -0.02em; }
.meal-h .kc span { font-size: 12px; color: var(--ink-3); }
.meal-h .add { width: 38px; height: 38px; border-radius: 50%; display: grid; place-items: center; background: var(--accent); color: #fff; flex: none; transition: transform .14s cubic-bezier(.3, .7, .4, 1.4), box-shadow .2s; box-shadow: 0 4px 12px color-mix(in srgb, var(--accent) 35%, transparent); }
.meal-h .add .i { stroke-width: 2.6; width: 20px; height: 20px; }
.meal-h .add:active { transform: scale(.95); }
.btn:active, .chip:active, .ai-btn:active { transform: scale(.97); }
.chip, .ai-btn { transition: transform .14s ease; }
.meal .mbar { height: 4px; margin: -6px 16px 12px 78px; border-radius: 2px; background: var(--ring-track); overflow: hidden; }
.meal .mbar i { display: block; height: 100%; background: var(--mc); border-radius: 2px; }
.meal .row { padding: 10px 16px; min-height: 64px; gap: 14px; }
.meal .row .thumb { width: 48px; height: 48px; border-radius: 12px; }
.meal .row::before { content: ""; position: absolute; top: 0; left: 78px; right: 0; border-top: 0.5px solid var(--line); }
.meal .mbar + .row::before { display: none; }
.mac { display: inline-flex; gap: 8px; }
.mac span { display: inline-flex; gap: 3px; }
.mac b { font-weight: 600; }
.mac .p b { color: var(--prot); } .mac .f b { color: var(--fat); } .mac .c b { color: var(--carb); }

/* вода */
.water .top-r { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
.water .top-r b { font: 700 28px var(--round); letter-spacing: -0.02em; }
.cups { display: grid; grid-template-columns: repeat(8, 1fr); gap: 6px; margin-bottom: 14px; }
.cups i { height: 30px; border-radius: 6px 6px 9px 9px; background: var(--ring-track); position: relative; overflow: hidden; }
.cups i.on { background: var(--water); }
.cups i.half { background: linear-gradient(to top, var(--water) 50%, var(--ring-track) 50%); }
.water .bar { display: none; }
.water .btns { margin-top: 0; }
.water .btn { white-space: nowrap; gap: 4px; font-size: 15px; }
.water .btn .i { width: 16px; height: 16px; stroke-width: 2.4; }

/* нижнее меню */
.tabbar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 30; background: var(--surface); border-top: 1px solid var(--line); padding-bottom: env(safe-area-inset-bottom); }
.tabbar .wrap { display: grid; grid-template-columns: repeat(5, 1fr); padding: 0 4px; }
.tabbar button { position: relative; height: 56px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; color: var(--ink-3); font-size: 10.5px; font-weight: 500; }
.tabbar button .i { width: 25px; height: 25px; stroke-width: 1.7; }
.tabbar button.on { color: var(--accent); }
.tabbar button.on .i { stroke-width: 2.2; }

/* шторки и уведомления */
.backdrop { position: fixed; inset: 0; z-index: 95; background: var(--overlay); opacity: 0; transition: opacity .25s; }
.sheet { position: fixed; left: 0; right: 0; bottom: 0; z-index: 96; max-width: 560px; margin: 0 auto; max-height: 92vh; display: flex; flex-direction: column; background: var(--surface); border-radius: 14px 14px 0 0; transform: translateY(100%); transition: transform .32s cubic-bezier(.2, .9, .3, 1); padding-bottom: env(safe-area-inset-bottom); }
.sheet::before { content: ""; position: absolute; top: 6px; left: 50%; width: 36px; height: 5px; margin-left: -18px; border-radius: 3px; background: var(--line); }
#sheetRoot.open .backdrop { opacity: 1; }
#sheetRoot.open .sheet { transform: none; }
.sheet-h { display: flex; align-items: center; gap: 8px; padding: 20px 16px 10px 20px; }
.sheet-h h3 { margin: 0; flex: 1; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; min-width: 0; }
.x { width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; background: var(--surface-2); color: var(--ink-3); }
.x .i { width: 16px; height: 16px; stroke-width: 2.4; }
.sheet-b { overflow-y: auto; padding: 4px 16px 20px; -webkit-overflow-scrolling: touch; }
.sheet-b .group { border-radius: 14px; background: var(--surface); box-shadow: inset 0 0 0 0.5px var(--line); }
.toast { position: fixed; left: 50%; bottom: calc(92px + env(safe-area-inset-bottom)); transform: translate(-50%, 12px); z-index: 120; max-width: calc(100% - 32px); padding: 12px 18px; border-radius: 16px; background: var(--ink); color: var(--surface); font-weight: 500; font-size: 15px; opacity: 0; pointer-events: none; transition: opacity .2s, transform .2s; box-shadow: 0 8px 24px rgba(0,0,0,.18); }
.toast.show { opacity: 1; transform: translate(-50%, 0); }
.toast.err { background: var(--danger); color: #fff; }

/* таймер отдыха */
.timer { text-align: center; }
.timer .t { font: 700 56px/1.1 var(--round); letter-spacing: -0.03em; color: var(--accent); }
.timer .t.done { color: var(--danger); }
.timer .chips { justify-content: center; margin: 10px 0 12px; }

/* графики */
canvas.chart { width: 100%; height: 200px; display: block; }
.cal-bars { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; height: 140px; align-items: end; position: relative; padding-top: 6px; }
.cal-bars .col { display: flex; flex-direction: column; justify-content: flex-end; height: 100%; align-items: center; gap: 6px; }
.cal-bars .b { width: 100%; max-width: 26px; border-radius: 8px; background: var(--accent); min-height: 4px; }
.cal-bars .b.over { background: var(--danger); }
.cal-bars .b.none { background: var(--ring-track); }
.cal-bars .d { font-size: 12px; color: var(--ink-3); font-weight: 500; }
.cal-bars .col.cur .d { color: var(--ink); font-weight: 700; }
.cal-line { position: absolute; left: 0; right: 0; border-top: 1.5px dashed var(--ink-3); pointer-events: none; opacity: .6; }
.tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.tile { background: var(--surface); border-radius: 20px; padding: 16px; box-shadow: var(--shadow); }
.tile .n { font-size: 13px; color: var(--ink-3); font-weight: 500; }
.tile .v { font: 700 26px var(--round); letter-spacing: -0.03em; margin-top: 2px; }
.tile .v small { font: 400 14px var(--font); color: var(--ink-3); letter-spacing: 0; }

/* профиль */
.me { display: flex; align-items: center; gap: 14px; padding: 8px 4px 4px; }
.me .avatar { width: 64px; height: 64px; font-size: 26px; }
.me .nm { font-size: 24px; font-weight: 700; letter-spacing: -0.02em; }
.pagehead { display: flex; align-items: center; gap: 6px; margin: 2px 0 14px -6px; }
.pagehead .back { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 19px; color: var(--accent); }
.pagehead h1 { margin: 0; font-size: 28px; font-weight: 700; letter-spacing: -0.03em; }
.note { font-size: 13px; color: var(--ink-3); margin: 8px 4px 0; line-height: 1.45; }
.result { background: var(--accent-soft); border-radius: 18px; padding: 16px; }
.result .big { font: 700 34px var(--round); letter-spacing: -0.03em; color: var(--accent); }

/* первое знакомство */
.wizard { position: fixed; inset: 0; z-index: 90; background: var(--bg); overflow-y: auto; display: none; }
.wizard.open { display: block; }
.wizard .wrap { padding-top: 20px; padding-bottom: calc(28px + env(safe-area-inset-bottom)); min-height: 100%; display: flex; flex-direction: column; }
.wz-prog { display: flex; gap: 5px; margin-bottom: 28px; }
.wz-prog i { flex: 1; height: 4px; border-radius: 2px; background: var(--line); }
.wz-prog i.on { background: var(--accent); }
.wizard h1 { font-size: 30px; line-height: 1.15; letter-spacing: -0.03em; margin: 0 0 8px; font-weight: 700; }
.wizard .lead { color: var(--ink-2); margin: 0 0 22px; font-size: 17px; }
.wz-foot { margin-top: auto; padding-top: 22px; }
.opt { display: flex; align-items: center; gap: 12px; width: 100%; padding: 15px 16px; text-align: left; background: var(--surface); border: 2px solid transparent; border-radius: 16px; margin-bottom: 10px; }
.opt.on { border-color: var(--accent); background: var(--accent-soft); }
.opt .ttl { font-size: 17px; }
.loading { padding: 80px 20px; text-align: center; color: var(--ink-3); font-weight: 500; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } .rise { opacity: 1; transform: none; } }

.stepper { display: grid; grid-template-columns: 52px 1fr 52px; gap: 8px; }
.stepper input { text-align: center; font: 600 22px var(--round); height: 52px; }
.stepper .btn { height: 52px; padding: 0; }
.pv { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; padding: 14px 16px; background: var(--surface-2); border-radius: 14px; margin: 12px 0; }
.pv b { font: 700 26px var(--round); letter-spacing: -0.02em; }
.shop-row .ck { width: 22px; height: 22px; border-radius: 7px; border: 2px solid var(--line); display: grid; place-items: center; flex: none; color: transparent; }
.shop-row.done .ck { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.shop-row.done .ttl { color: var(--ink-3); text-decoration: line-through; }
.shop-row .ck .i { width: 14px; height: 14px; stroke-width: 2.6; }
.search { position: relative; margin-bottom: 12px; }
.search .i { position: absolute; left: 12px; top: 11px; color: var(--ink-3); width: 18px; height: 18px; }
.search input { padding-left: 38px; height: 40px; border-radius: 11px; font-size: 17px; }
.link-row { display: flex; justify-content: center; padding-top: 12px; }

/* выбор продукта */
.pk-head { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
.pk-head .thumb { width: 64px; height: 64px; border-radius: 16px; font-size: 34px; }
.pk-head .ttl { font-size: 19px; font-weight: 700; letter-spacing: -0.02em; }
.ai-btn { display: flex; align-items: center; gap: 12px; width: 100%; padding: 12px 14px; margin-bottom: 12px; border-radius: 14px; background: linear-gradient(120deg, var(--accent-soft), var(--s3-soft)); text-align: left; cursor: pointer; }
.ai-btn .ic { width: 38px; height: 38px; border-radius: 11px; background: var(--accent); color: #fff; display: grid; place-items: center; flex: none; }
.ai-photo { width: 100%; max-height: 220px; object-fit: cover; border-radius: 16px; display: block; margin-bottom: 12px; }
.ai-row { display: grid; grid-template-columns: 26px 1fr 84px; align-items: center; gap: 10px; padding: 10px 0; border-top: 0.5px solid var(--line); }
.ai-row:first-child { border-top: 0; }
.ai-row input[type="number"], .ai-row input.g { height: 40px; text-align: center; font: 600 16px var(--round); padding: 0 6px; }
.ck2 { width: 24px; height: 24px; border-radius: 50%; border: 2px solid var(--line); display: grid; place-items: center; color: transparent; }
.ck2.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.ck2 .i { width: 14px; height: 14px; stroke-width: 3; }
.spin { width: 28px; height: 28px; border-radius: 50%; border: 3px solid var(--ring-track); border-top-color: var(--accent); animation: spin .8s linear infinite; margin: 0 auto 12px; }
@keyframes spin { to { transform: rotate(360deg); } }
.photo-big { width: 100%; max-height: 260px; object-fit: cover; border-radius: 16px; display: block; margin-bottom: 10px; background: var(--surface-2); }

/* статистика владельца */
.sbars { display: grid; grid-template-columns: repeat(14, 1fr); gap: 4px; height: 110px; align-items: end; }
.sbars .c { display: flex; flex-direction: column; justify-content: flex-end; align-items: center; height: 100%; gap: 4px; }
.sbars .b { width: 100%; border-radius: 4px; background: var(--accent); min-height: 3px; }
.sbars .b.z { background: var(--ring-track); }
.sbars .d { font-size: 10px; color: var(--ink-3); }
.sbars .v { font: 600 10px var(--round); color: var(--ink-2); }
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></symbol>
<symbol id="i-sunrise" viewBox="0 0 24 24"><path d="M17 18a5 5 0 0 0-10 0M12 3v6M4.2 10.2l1.4 1.4M1 18h2M21 18h2M18.4 11.6l1.4-1.4M23 22H1M8 6l4-4 4 4"/></symbol>
<symbol id="i-moon" viewBox="0 0 24 24"><path d="M20.5 13.5A8.5 8.5 0 1 1 10.5 3.5a7 7 0 0 0 10 10z"/></symbol>
<symbol id="i-apple" viewBox="0 0 24 24"><path d="M12 8c-1.8-1.6-6.5-1.2-6.5 4 0 4 2.8 9 5 9 .9 0 1.1-.6 1.5-.6s.6.6 1.5.6c2.2 0 5-5 5-9 0-5.200-4.700-5.600-6.500-4zM12 8c0-2 1-4 3-5"/></symbol>
<symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
<symbol id="i-minus" viewBox="0 0 24 24"><path d="M5 12h14"/></symbol>
<symbol id="i-chev-r" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></symbol>
<symbol id="i-chev-l" viewBox="0 0 24 24"><path d="M15 6l-6 6 6 6"/></symbol>
<symbol id="i-arrow-left" viewBox="0 0 24 24"><path d="M19 12H5M12 19l-7-7 7-7"/></symbol>
<symbol id="i-flame" viewBox="0 0 24 24"><path d="M12 22c4 0 7-3 7-7 0-3-2-5-3-7-1 2-2 3-3 3 0-3-1-6-4-8 0 4-4 6-4 12 0 4 3 7 7 7z"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.300-4.300"/></symbol>
<symbol id="i-user" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></symbol>
<symbol id="i-dumbbell" viewBox="0 0 24 24"><path d="M6.500 6.500v11M17.500 6.500v11M3.500 9v6M20.500 9v6M6.500 12h11"/></symbol>
<symbol id="i-trend" viewBox="0 0 24 24"><path d="M3 17l6-6 4 4 8-8M15 7h6v6"/></symbol>
<symbol id="i-book" viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 19V5M8 7h7M8 11h5"/></symbol>
<symbol id="i-utensils" viewBox="0 0 24 24"><path d="M6 3v7a2 2 0 0 0 2 2v9M10 3v7a2 2 0 0 1-2 2M8 3v5M17 3c-2 1.500-2.500 5-2.500 7.500 0 1 .8 1.500 2.500 1.500zM17 12v9"/></symbol>
<symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></symbol>
<symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 11a8 8 0 0 0-14-4M4 4v4h4M4 13a8 8 0 0 0 14 4M20 20v-4h-4"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M5 12.500l4.500 4.500L19 7.500"/></symbol>
<symbol id="i-x" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></symbol>
<symbol id="i-phone" viewBox="0 0 24 24"><rect x="7" y="2.500" width="10" height="19" rx="2.500"/><path d="M11 18h2"/></symbol>
<symbol id="i-target" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></symbol>
<symbol id="i-sliders" viewBox="0 0 24 24"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></symbol>
<symbol id="i-download" viewBox="0 0 24 24"><path d="M12 4v11M7 10.500l5 5 5-5M5 20h14"/></symbol>
<symbol id="i-upload" viewBox="0 0 24 24"><path d="M12 20V9M7 13.500l5-5 5 5M5 4h14"/></symbol>
<symbol id="i-message" viewBox="0 0 24 24"><path d="M4 5h16v11H9.500L4 20z"/></symbol>
<symbol id="i-copy" viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2.500"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></symbol>
<symbol id="i-list" viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></symbol>
<symbol id="i-timer" viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.500 2M9 2h6"/></symbol>
<symbol id="i-spark" viewBox="0 0 24 24"><path d="M12 3l2 5.500 5.500 2-5.500 2-2 5.500-2-5.500-5.500-2 5.500-2zM19 16v4M17 18h4"/></symbol>
<symbol id="i-bolt" viewBox="0 0 24 24"><path d="M13 2L4.500 13.500H11L10 22l9-12h-6.500z"/></symbol>
<symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 3l8 3v6c0 5-3.500 8-8 9-4.500-1-8-4-8-9V6z"/></symbol>
<symbol id="i-camera" viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></symbol>
<symbol id="i-image" viewBox="0 0 24 24"><rect x="3.5" y="4.5" width="17" height="15" rx="3"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-4.500 4 3.500 3-2.500 4 3.500"/></symbol>
<symbol id="i-chart" viewBox="0 0 24 24"><path d="M5 20V10M12 20V4M19 20v-7"/></symbol>
<symbol id="i-fork" viewBox="0 0 24 24"><path d="M7 3v7a2 2 0 0 0 2 2v9M11 3v7a2 2 0 0 1-2 2M9 3v5M17 3c-2 1.500-2.500 5-2.500 7.500 0 1 .8 1.500 2.500 1.500zM17 12v9"/></symbol>
</defs></svg>
<header class="top" id="top"></header>
<main id="view"></main>
<nav class="tabbar" id="tabbar" aria-label="Разделы"></nav>
<div id="sheetRoot"></div>
<div class="wizard" id="wizard"></div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script>
'use strict';

const tg = (window.Telegram && window.Telegram.WebApp) ? window.Telegram.WebApp : null;
if (tg) { try { tg.ready(); tg.expand(); } catch (e) {} }
const initData = (tg && tg.initData) || '';
const tgUser = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || null;
const tgUserId = String((tgUser && tgUser.id) || 'default_user');
const userName = (tgUser && (tgUser.first_name || tgUser.username)) || 'Гость';

const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const ic = (n, c) => '<svg class="i ' + (c || '') + '" aria-hidden="true"><use href="#i-' + n + '"/></svg>';
const n0 = v => Math.round(Number(v) || 0).toLocaleString('ru-RU');
const n1 = v => (Math.round((Number(v) || 0) * 10) / 10).toLocaleString('ru-RU', { maximumFractionDigits: 1 });
const num = v => { const x = parseFloat(String(v === undefined || v === null ? '' : v).replace(',', '.')); return isNaN(x) ? 0 : x; };
const norm = s => String(s || '').toLowerCase().replace(/ё/g, 'е').trim();
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

function localDate(d) {
  d = d || new Date();
  const p = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}
function addDays(ds, n) {
  const d = new Date(ds + 'T00:00:00');
  d.setDate(d.getDate() + n);
  return localDate(d);
}
function dateLabel(ds) {
  const t = localDate();
  if (ds === t) return 'Сегодня';
  if (ds === addDays(t, -1)) return 'Вчера';
  if (ds === addDays(t, 1)) return 'Завтра';
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long' }).format(new Date(ds + 'T00:00:00'));
}
const ddmm = ds => ds.slice(8, 10) + '.' + ds.slice(5, 7);

const MEALS = [
  { k: 'breakfast', n: 'Завтрак', icon: 'sunrise', pct: 25, c: 's1', e: '🍳' },
  { k: 'lunch', n: 'Обед', icon: 'sun', pct: 35, c: 's2', e: '🥗' },
  { k: 'dinner', n: 'Ужин', icon: 'moon', pct: 25, c: 's3', e: '🍝' },
  { k: 'snack', n: 'Перекус', icon: 'apple', pct: 15, c: 's4', e: '🍎' },
];
const MEAL_GEN = { breakfast: 'завтрак', lunch: 'обед', dinner: 'ужин', snack: 'перекус' };
const GOALS = { lose: 'Снизить вес', maintain: 'Удерживать вес', gain: 'Набрать вес' };
const ACTIVITY = [
  { v: 1.2, t: 'Сидячий образ жизни', s: 'Офис, мало ходьбы, без тренировок' },
  { v: 1.375, t: 'Лёгкая активность', s: 'Прогулки, 1–3 тренировки в неделю' },
  { v: 1.55, t: 'Умеренная активность', s: '3–5 тренировок в неделю' },
  { v: 1.725, t: 'Высокая активность', s: 'Тренировки почти каждый день или физический труд' },
];
const MENU_USE = [
  { v: 'none', t: 'Не использовать в автоменю' }, { v: 'any', t: 'В любом приёме пищи' },
  { v: 'breakfast', t: 'Только на завтрак' }, { v: 'lunch', t: 'Только на обед' },
  { v: 'dinner', t: 'Только на ужин' }, { v: 'snack', t: 'Только на перекус' },
];
const THEMES = [
  { v: 'light', t: 'Светлая', i: 'sun' }, { v: 'dark', t: 'Тёмная', i: 'moon' },
  { v: 'auto', t: 'Как в Telegram', s: 'Авто', i: 'phone' },
];

const S = {
  date: localDate(), tab: 'diary', page: null, d: null, loading: true, error: null, req: 0,
  workTab: 'strength', range: 30, form: { ex: '', w: '', r: '' }, cardio: { idx: '', mins: '30', cal: '', name: '' },
  shop: new Set(), exHist: null, exSel: '', picker: null, edit: null, wiz: null,
  fb: { kind: 'idea', text: '' }, gf: null, rf: { name: '', cal: '', p: '', f: '', c: '', meal_type: 'none' },
  pg: { date: localDate(), w: '', s: '', note: '' },
};

/* ---------- сеть ---------- */
async function api(url, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ 'X-Telegram-Init-Data': initData, 'X-Fallback-User': tgUserId }, opts.headers || {});
  let res;
  try { res = await fetch(url, opts); } catch (e) { throw new Error('Нет связи с сервером. Проверьте интернет и повторите.'); }
  let body = null;
  try { body = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((body && body.error) || ('Ошибка сервера ' + res.status));
  return body;
}
const post = (url, payload) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });

async function load() {
  const my = ++S.req;
  try {
    const d = await api('/api/state?date=' + S.date + '&today=' + localDate());
    if (my !== S.req) return;
    S.d = d; S.error = null;
  } catch (e) {
    if (my !== S.req) return;
    S.error = e.message;
    if (S.d) toast(e.message, true);
  }
  S.loading = false;
  render();
}

/* Выполняет запрос, показывает ошибку, затем обновляет данные. */
async function run(fn, okMsg) {
  try { await fn(); } catch (e) { toast(e.message, true); return false; }
  if (okMsg) toast(okMsg);
  await load();
  return true;
}

/* ---------- тема ---------- */
function resolveTheme(pref) {
  if (pref === 'dark' || pref === 'light') return pref;
  if (tg && tg.colorScheme) return tg.colorScheme;
  return (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
}
let appliedTheme = '';
function applyTheme(pref) {
  const t = resolveTheme(pref);
  try { localStorage.setItem('gm_theme', pref); } catch (e) {}
  if (appliedTheme === t) return;
  appliedTheme = t;
  document.documentElement.setAttribute('data-theme', t);
  const bg = t === 'dark' ? '#0F1512' : '#F1F4F2';
  const surf = t === 'dark' ? '#171F1B' : '#FFFFFF';
  const meta = $('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', bg);
  if (tg) {
    try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); if (tg.setBottomBarColor) tg.setBottomBarColor(surf); } catch (e) {}
  }
  requestAnimationFrame(drawCharts);
}
if (tg && tg.onEvent) { try { tg.onEvent('themeChanged', () => { appliedTheme = ''; if (S.d) applyTheme(S.d.settings.theme); }); } catch (e) {} }

/* ---------- уведомления и шторки ---------- */
let toastTimer = null;
function toast(msg, err) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast' + (err ? ' err' : ''); }, err ? 4200 : 2200);
  if (tg && tg.HapticFeedback) { try { err ? tg.HapticFeedback.notificationOccurred('error') : tg.HapticFeedback.impactOccurred('light'); } catch (e) {} }
}
const sheetRoot = () => $('#sheetRoot');
let confirmResolve = null, sheetSeq = 0;
const closeIf = id => { if (id === sheetSeq) closeSheet(); };
function openSheet(title, bodyHtml) {
  sheetSeq++;
  sheetRoot().innerHTML = '<div class="backdrop" data-act="close-sheet"></div><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">' +
    '<div class="sheet-h"><h3 id="sheetTitle">' + esc(title) + '</h3><button class="x" data-act="close-sheet" aria-label="Закрыть">' + ic('x') + '</button></div>' +
    '<div class="sheet-b" id="sheetBody">' + bodyHtml + '</div></div>';
  document.body.classList.add('noscroll');
  requestAnimationFrame(() => requestAnimationFrame(() => sheetRoot().classList.add('open')));
}
function setSheet(title, bodyHtml) {
  $('#sheetTitle').textContent = title;
  $('#sheetBody').innerHTML = bodyHtml;
  $('#sheetBody').scrollTop = 0;
}
function closeSheet() {
  if (confirmResolve) { const r = confirmResolve; confirmResolve = null; r(false); }
  sheetRoot().classList.remove('open');
  document.body.classList.remove('noscroll');
  S.picker = null; S.edit = null;
  setTimeout(() => { if (!sheetRoot().classList.contains('open')) sheetRoot().innerHTML = ''; }, 280);
}
function askConfirm(title, text, okLabel, danger) {
  return new Promise(res => {
    confirmResolve = res;
    openSheet(title, '<p class="mut" style="margin:2px 0 18px">' + esc(text) + '</p><div class="btns">' +
      '<button class="btn sec" data-act="confirm-no">Отмена</button>' +
      '<button class="btn ' + (danger ? 'dng' : '') + '" data-act="confirm-yes">' + esc(okLabel) + '</button></div>');
  });
}

/* ---------- расчёт норм ---------- */
function calcPlan(p) {
  const w = num(p.weight), h = num(p.height), a = num(p.age);
  const bmr = 10 * w + 6.25 * h - 5 * a + (p.sex === 'female' ? -161 : 5);
  const tdee = bmr * num(p.activity);
  const perDay = num(p.goal_rate) * 7700 / 7;
  const delta = p.goal === 'lose' ? -perDay : (p.goal === 'gain' ? perDay : 0);
  const floor = p.sex === 'female' ? 1200 : 1500;
  let kcal = Math.round((tdee + delta) / 10) * 10;
  const floored = kcal < floor;
  if (floored) kcal = floor;
  const refW = p.goal === 'gain' ? w : Math.min(w, num(p.target_weight) || w);
  const prot = Math.min(Math.round(1.8 * refW), Math.round(kcal * 0.35 / 4));
  const fat = Math.round(kcal * 0.28 / 9);
  const carbs = Math.max(0, Math.round((kcal - prot * 4 - fat * 9) / 4));
  const water = Math.max(1500, Math.round(w * 30 / 50) * 50);
  return { kcal, prot, fat, carbs, water, bmr: Math.round(bmr), tdee: Math.round(tdee), floored };
}
function latestWeight() {
  const e = S.d ? S.d.entries.filter(x => x.weight > 0) : [];
  return e.length ? e[e.length - 1].weight : (S.d ? S.d.settings.weight : 75);
}

/* ================= Дневник ================= */
const CATS = [['all', 'Все'], ['dishes', 'Блюда'], ['cafe', 'Кафе'], ['meat', 'Мясо'], ['fish', 'Рыба'], ['dairy', 'Молочные и яйца'], ['grains', 'Крупы и хлеб'], ['veg', 'Овощи'], ['fruit', 'Фрукты'], ['other', 'Другое']];
const mealOf = k => MEALS.find(m => m.k === k) || MEALS[3];
const macLine = (p, f, c) => '<span class="mac"><span class="p">Б <b>' + n0(p) + '</b></span><span class="f">Ж <b>' + n0(f) + '</b></span><span class="c">У <b>' + n0(c) + '</b></span></span>';

/* Эмодзи для продуктов без фото: по ключевым словам названия. */
const FOOD_EMOJI = [
  [/кофе|капучино|латте|американо|эспрессо|раф\b|флэт/, '☕'], [/чай|матча/, '🍵'], [/кола|газиров|лимонад|фрустайл|добрый|физз|тоник|напиток|тархун|энергет/, '🥤'],
  [/сок|смузи/, '🧃'], [/вода/, '💧'], [/пиво/, '🍺'], [/вино/, '🍷'], [/коктейл|молочный коктейль|милкшейк/, '🥤'],
  [/мороженое|пломбир|айс де люкс|айс микс|рожок/, '🍦'], [/чизкейк|торт|пирожн|тарт|капкейк|профитрол/, '🍰'], [/донат|пончик/, '🍩'],
  [/печенье|макарони\b|вупи/, '🍪'], [/шоколад|конфет/, '🍫'], [/круассан|улитк/, '🥐'], [/маффин с яйц|омлет|яичниц|большой завтрак/, '🍳'], [/яйц/, '🥚'],
  [/бургер|чизбургер|гамбургер|гранд|биг хит|биг спешиал|шефбургер|маэстро|ростмастер|чикен хит|чикенбургер/, '🍔'],
  [/ролл|шаурм|лаваш|кесадиль|твистер/, '🌯'], [/пицц/, '🍕'], [/фри|картофел|дольки|оладуш/, '🍟'], [/наггет|стрипс|байтс|крыл|ножк|курин|курица|бедро|грудк|индейк/, '🍗'],
  [/креветк/, '🍤'], [/лосос|форел|сельд|скумбр|тунец|рыб|треск|минта|икра|шпрот/, '🐟'], [/говяд|свинин|фарш|стейк|котлет|бифштекс/, '🥩'],
  [/колбас|сосиск|ветчин|бекон|сервелат/, '🌭'], [/пельмен|вареник/, '🥟'], [/суп|борщ|бульон|щи\b/, '🍲'], [/салат|цезарь/, '🥗'],
  [/плов|рис/, '🍚'], [/макарон|спагетти|паста|лапш|рамен|доширак/, '🍝'], [/гречк|гречн|каша|овсян|хлопья|мюсли|геркулес|булгур|киноа|пшен|перлов|ячнев/, '🥣'],
  [/хлеб|батон|хлебц|тост|чиабатт|лепешк|галет/, '🍞'], [/блин|сырник|оладь|панкейк/, '🥞'], [/сырок/, '🍫'], [/творог|творож/, '🥛'],
  [/сыр|моцарел|фета|чеддер/, '🧀'], [/молок|кефир|йогурт|ряженк|сметан|сливк|актибио|простокваш/, '🥛'], [/масло слив/, '🧈'],
  [/масло|майонез|соус|кетчуп|горчиц/, '🫙'], [/мёд|мед\b|джем|варенье|сироп/, '🍯'], [/сахар/, '🍬'], [/чипс|сухарик|кириешк|снек/, '🥔'],
  [/орех|миндал|кешью|арахис|фисташ|семечк|семена|козинак/, '🥜'], [/батончик|протеин/, '🍫'], [/банан/, '🍌'], [/яблок/, '🍎'],
  [/груш/, '🍐'], [/апельсин|мандарин|грейпфрут/, '🍊'], [/лимон|лайм/, '🍋'], [/виноград|изюм/, '🍇'], [/клубник|земляник/, '🍓'],
  [/черник|голубик/, '🫐'], [/вишн|черешн/, '🍒'], [/персик|абрикос|курага/, '🍑'], [/ананас/, '🍍'], [/манго/, '🥭'], [/киви/, '🥝'],
  [/арбуз/, '🍉'], [/дыня/, '🍈'], [/авокадо/, '🥑'], [/финик/, '🌴'], [/огур/, '🥒'], [/помидор|томат/, '🍅'], [/морков/, '🥕'],
  [/брокколи|капуст/, '🥦'], [/перец/, '🫑'], [/баклажан/, '🍆'], [/кукуруз/, '🌽'], [/горош|фасол|нут|чечевиц|тофу/, '🫘'],
  [/салат лист|шпинат|зелень/, '🥬'], [/лук\b|лук реп/, '🧅'], [/свекл|свёкл/, '🍠'], [/кабач/, '🥒'], [/картош/, '🥔'], [/пирожок|пирог|штрудел/, '🥧'],
];
const CAT_EMOJI = { meat: '🍗', fish: '🐟', dairy: '🥛', grains: '🥣', veg: '🥦', fruit: '🍎', other: '🍽️' };
function foodEmoji(name, cat) {
  const n = norm(name);
  for (const [re, e] of FOOD_EMOJI) if (re.test(n)) return e;
  return CAT_EMOJI[cat] || '🍽️';
}
const PHOTO = {};
function thumbHtml(x, opts) {
  opts = opts || {};
  if (opts.mealId && opts.photo) return '<span class="thumb"><img data-photo="' + opts.mealId + '" alt="" src="' + (PHOTO[opts.mealId] || '') + '"><span class="cam">' + ic('camera') + '</span></span>';
  if (x.img) return '<span class="thumb pack"><img src="' + esc(x.img) + '" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.textContent=\'' + foodEmoji(x.name, x.cat) + '\'"></span>';
  return '<span class="thumb">' + foodEmoji(x.name, x.cat) + '</span>';
}
/* Фото из дневника загружаются отдельным запросом (нужна подпись Telegram в заголовке). */
async function loadPhotos() {
  for (const img of $$('img[data-photo]')) {
    const id = img.dataset.photo;
    if (PHOTO[id]) { img.src = PHOTO[id]; continue; }
    try {
      const r = await fetch('/api/meal_photo?id=' + id, { headers: { 'X-Telegram-Init-Data': initData, 'X-Fallback-User': tgUserId } });
      if (!r.ok) continue;
      PHOTO[id] = URL.createObjectURL(await r.blob());
      $$('img[data-photo="' + id + '"]').forEach(el => { el.src = PHOTO[id]; });
    } catch (e) {}
  }
}
/* Анимации главного экрана: кольцо дорисовывается, карточки появляются при прокрутке (один раз). */
const SEEN = new Set();
let riseObs = null;
function animateDiary() {
  const ring = $('#ringVal');
  if (ring) requestAnimationFrame(() => requestAnimationFrame(() => { ring.style.strokeDashoffset = ring.dataset.to + 'px'; }));
  const reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!riseObs && 'IntersectionObserver' in window) {
    riseObs = new IntersectionObserver(es => es.forEach(e => {
      if (!e.isIntersecting) return;
      const el = e.target, i = +el.dataset.i || 0;
      setTimeout(() => el.classList.add('in'), Math.min(i, 4) * 60);
      SEEN.add(el.dataset.anim); riseObs.unobserve(el);
    }), { threshold: 0.12 });
  }
  $$('.rise').forEach((el, i) => {
    if (reduce || !riseObs || SEEN.has(el.dataset.anim)) { el.classList.add('in'); el.style.transition = 'none'; return; }
    el.dataset.i = i; riseObs.observe(el);
  });
}

/* Сжатие фото на телефоне перед отправкой. */
function shrinkPhoto(file, maxSide, quality) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file), im = new Image();
    im.onload = () => {
      const k = Math.min(1, maxSide / Math.max(im.naturalWidth, im.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.round(im.naturalWidth * k); c.height = Math.round(im.naturalHeight * k);
      c.getContext('2d').drawImage(im, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', quality));
    };
    im.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Не удалось открыть фото')); };
    im.src = url;
  });
}

function mealsByType() {
  const out = { breakfast: [], lunch: [], dinner: [], snack: [] };
  S.d.day_meals.forEach(m => { (out[m.meal_type] || out.snack).push(m); });
  return out;
}

const RING = { frac: 0, key: '' };
function ringHtml(left, budget, eaten, over) {
  const r = 76, len = 2 * Math.PI * r, frac = over ? 1 : clamp(budget ? eaten / budget : 0, 0, 1);
  if (RING.key !== S.date) { RING.key = S.date; RING.frac = 0; }  // новый день — заполняем с нуля
  const from = RING.frac; RING.frac = frac; RING.len = len;
  return '<div class="ring' + (over ? ' over' : '') + '"><svg viewBox="0 0 184 184" aria-hidden="true"><circle class="trk" cx="92" cy="92" r="' + r + '"/>' +
    '<circle class="val" id="ringVal" cx="92" cy="92" r="' + r + '" stroke-dasharray="' + len.toFixed(1) + '" stroke-dashoffset="' + (len * (1 - from)).toFixed(1) + '" data-to="' + (len * (1 - frac)).toFixed(1) + '"/></svg>' +
    '<div class="mid"><div class="big">' + n0(Math.abs(left)) + '</div><div class="cap">' + (left >= 0 ? 'ккал осталось' : 'ккал сверх нормы') + '</div></div></div>';
}

function renderDiary() {
  const d = S.d, s = d.settings, by = mealsByType();
  const burn = d.today_burn, budget = s.target_cal + burn, eaten = d.today_cal;
  const left = budget - eaten, over = left < -budget * 0.05;
  const sums = MEALS.map(m => by[m.k].reduce((a, x) => a + x.calories, 0));
  let h = '<section class="hero rise" data-anim="hero" aria-label="Калории за день"><div class="ringrow">' +
    '<div class="side"><div class="v">' + n0(eaten) + '</div><div class="n">' + ic('fork') + 'съедено</div></div>' +
    ringHtml(left, budget, eaten, over) +
    '<div class="side"><div class="v">' + n0(burn) + '</div><div class="n">' + ic('flame') + 'сожжено</div></div></div>' +
    '<div class="macros">' + [['Белки', d.today_p, s.target_p, 'prot'], ['Жиры', d.today_f, s.target_f, 'fat'], ['Углеводы', d.today_c, s.target_c, 'carb']].map(x =>
      '<div class="macro" style="--mc:var(--' + x[3] + ')"><div class="n">' + x[0] + '</div><div class="v">' + n0(x[1]) + ' <small>/ ' + n0(x[2]) + ' г</small></div>' +
      '<div class="bar"><i style="width:' + clamp(x[2] ? x[1] / x[2] * 100 : 0, 0, 100) + '%"></i></div></div>').join('') + '</div></section>';

  if (!d.day_meals.length) {
    h += '<div class="group" style="margin-top:12px"><div class="empty"><b>Пока пусто.</b> Нажмите «+» у нужного приёма пищи, перенесите вчерашний день или соберите меню автоматически.</div>' +
      '<div class="pad stack" style="padding-top:0"><button class="btn soft" data-act="copy-prev">' + ic('copy') + 'Скопировать вчерашний день</button>' +
      '<button class="btn soft" data-act="gen-day">' + ic('spark') + 'Собрать меню на день</button></div></div>';
  }
  MEALS.forEach((m, i) => {
    const list = by[m.k], tgt = Math.round(s.target_cal * m.pct / 100);
    const grams = list.reduce((a, x) => a + x.grams, 0);
    const info = list.length ? n0(grams) + ' г · ' + list.length + ' ' + plural(list.length, 'продукт', 'продукта', 'продуктов') : 'Рекомендуем ~' + n0(tgt) + ' ккал';
    h += '<section class="meal rise" data-anim="meal-' + m.k + '" style="--mc:var(--' + m.c + ');--mc-soft:var(--' + m.c + '-soft)"><div class="meal-h"><span class="emo" aria-hidden="true">' + m.e + '</span>' +
      '<div class="grow"><div class="t">' + m.n + '</div><div class="k">' + info + '</div></div>' +
      '<div class="kc"><b>' + n0(sums[i]) + '</b><span>ккал</span></div>' +
      '<button class="add" data-act="add-food" data-m="' + m.k + '" aria-label="Добавить: ' + m.n + '">' + ic('plus') + '</button></div>' +
      (list.length ? '<div class="mbar"><i style="width:' + clamp(tgt ? sums[i] / tgt * 100 : 0, 0, 100) + '%"></i></div>' : '');
    h += list.map(x => '<button class="row" data-act="edit-meal" data-id="' + x.id + '">' + thumbHtml({ name: x.food_name }, { mealId: x.id, photo: x.photo }) +
      '<div class="grow"><div class="ttl">' + esc(x.food_name) + '</div>' +
      '<div class="sub i2"><span>' + n0(x.grams) + ' г</span>' + macLine(x.p, x.f, x.c) + '</div></div><div class="end">' + n0(x.calories) + '</div></button>').join('');
    h += '</section>';
  });

  const wp = Math.min(100, s.target_water ? d.water_ml / s.target_water * 100 : 0);
  const cups = 8, per = s.target_water / cups, full = d.water_ml / per;
  h += '<div class="h2">Вода <small>' + Math.round(wp) + '%</small></div><div class="group pad water rise" data-anim="water"><div class="top-r"><div><b>' + n1(d.water_ml / 1000) + '</b> <span class="mut">из ' + n1(s.target_water / 1000) + ' л</span></div></div>' +
    '<div class="cups" aria-hidden="true">' + Array.from({ length: cups }, (_, i) => '<i class="' + (full >= i + 1 ? 'on' : (full > i + 0.25 ? 'half' : '')) + '"></i>').join('') + '</div>' +
    '<div class="bar"><i style="width:' + wp + '%"></i></div>' +
    '<div class="btns"><button class="btn sec sm" data-act="water" data-v="-' + s.water_glass + '">' + ic('minus') + s.water_glass + ' мл</button>' +
    '<button class="btn soft sm" data-act="water" data-v="' + s.water_glass + '">' + ic('plus') + s.water_glass + ' мл</button>' +
    '<button class="btn soft sm" data-act="water" data-v="500">' + ic('plus') + '500 мл</button></div></div>';

  if (d.day_workouts.length || d.day_sets.length) {
    h += '<div class="h2">Активность</div><div class="group">' +
      (d.day_sets.length ? '<button class="row" data-act="tab" data-t="work"><span class="ico-box">' + ic('dumbbell') + '</span><div class="grow"><div class="ttl">Силовая тренировка</div><div class="sub">' + d.day_sets.length + ' подходов</div></div>' + ic('chev-r', 'chev') + '</button>' : '') +
      d.day_workouts.map(w => '<div class="row"><span class="ico-box">' + ic('bolt') + '</span><div class="grow"><div class="ttl">' + esc(w.name) + '</div><div class="sub">' + n0(w.mins) + ' мин</div></div><div class="end">−' + n0(w.cal) + ' <span class="sub">ккал</span></div></div>').join('') + '</div>';
  }
  if (d.day_meals.length) {
    h += '<div class="link-row"><button class="btn link" data-act="copy-prev">Скопировать вчерашний день сюда</button></div>';
  }
  return h;
}

/* ---------- выбор продукта ---------- */
function perOf(it) { return { name: it.name, cal: it.cal, p: it.p, f: it.f, c: it.c, cat: it.cat }; }
function pickerSource() {
  const pk = S.picker, d = S.d, q = norm(pk.q);
  const recent = d.recent.map(r => { const g = r.grams || 100; return { name: r.name, cal: r.cal / g * 100, p: r.p / g * 100, f: r.f / g * 100, c: r.c / g * 100, g: Math.round(g), src: 'recent' }; });
  const mine = d.custom_foods.map(x => ({ name: x.name, cal: x.cal, p: x.p, f: x.f, c: x.c, g: 100, src: 'mine' }));
  const base = d.foods.map(x => ({ name: x.name, cal: x.cal, p: x.p, f: x.f, c: x.c, g: 100, src: 'base', cat: x.cat }));
  let list;
  if (q) {
    const seen = new Set();
    const words = q.split(/\s+/).filter(Boolean);
    list = mine.concat(recent, base).filter(x => { const k = norm(x.name); if (seen.has(k)) return false; seen.add(k); return words.every(w => k.includes(w)); });
    if (pk.remoteQ === q) pk.remote.forEach(x => { const k = norm(x.name) + '|' + norm(x.brand); if (!seen.has(k) && !seen.has(norm(x.name))) { seen.add(k); list.push(x); } });
  } else if (pk.tab === 'recent') list = recent;
  else if (pk.tab === 'mine') list = mine;
  else if (pk.cat === 'cafe') list = CAFE.items.filter(x => !pk.chain || x.brand === pk.chain);
  else if (pk.cat === 'all') list = base.concat(CAFE.items);
  else list = base.filter(x => x.cat === pk.cat);
  return list;
}
function pickerList() {
  const list = pickerSource(), pk = S.picker;
  if (!list.length) {
    let msg;
    if (pk.q) msg = pk.loading ? 'Ищем в общей базе…' : 'Ничего не найдено. Добавьте продукт вручную.';
    else if (pk.tab === 'base' && pk.cat === 'cafe') msg = CAFE.loaded ? 'В меню этой сети пока нет позиций.' : 'Загружаем меню кафе…';
    else msg = pk.tab === 'recent' ? 'Здесь появятся продукты, которые вы уже добавляли.' : 'Своих рецептов пока нет. Их можно создать на вкладке «Меню».';
    return '<div class="empty">' + msg + '</div>';
  }
  const limit = pk.tab === 'base' && !pk.q && (pk.cat === 'cafe' || pk.cat === 'all') ? 800 : 120;
  return list.slice(0, limit).map((x, i) => '<button class="row thumbed" data-act="pick" data-i="' + i + '">' + thumbHtml(x) + '<div class="grow"><div class="ttl">' + esc(x.name) + '</div>' +
    (x.brand ? '<div class="sub">' + esc(x.brand) + (x.portions && x.portions.length > 1 ? ' · ' + x.portions.length + ' порции' : (x.src === 'restaurant' && x.g ? ' · ' + n0(x.g) + ' г' : '')) + '</div>' : '') +
    '<div class="sub i2"><span>' + n0(x.cal) + ' ккал</span>' + macLine(x.p, x.f, x.c) + '</div></div>' + ic('chev-r', 'chev') + '</button>').join('') +
    (pk.q && pk.loading ? '<div class="empty">Ищем ещё в общей базе…</div>' : '');
}
/* Общая база продуктов на сервере: сети ресторанов + магазинные продукты. */
let pkTimer = 0, pkSeq = 0;
function fromServer(x) {
  const full = (x.src === 'restaurant' && x.brand ? x.name + ' · ' + x.brand : x.name).slice(0, 100);
  return { name: x.name, full, brand: x.brand || '', cal: x.cal, p: x.p, f: x.f, c: x.c, g: x.g || 100, portion: x.portion || '', src: x.src, img: x.img || '', portions: x.portions || [] };
}
function searchRemote() {
  clearTimeout(pkTimer);
  const pk = S.picker; if (!pk) return;
  const q = norm(pk.q);
  if (q.length < 2) { pk.loading = false; return; }
  if (pk.remoteQ === q) return;
  pk.loading = true;
  pkTimer = setTimeout(async () => {
    const my = ++pkSeq;
    let items = [];
    try { items = (await api('/api/foods_search?q=' + encodeURIComponent(q))).items || []; } catch (e) {}
    if (my !== pkSeq || !S.picker || norm(S.picker.q) !== q) return;
    S.picker.remote = items.map(fromServer); S.picker.remoteQ = q; S.picker.loading = false;
    refreshPicker();
  }, 350);
}
const CAFE = { items: [], loaded: false, loading: false };
async function loadCafe() {
  if (CAFE.loaded || CAFE.loading) return;
  CAFE.loading = true;
  try { CAFE.items = ((await api('/api/foods_catalog')).items || []).map(x => Object.assign(fromServer(x), { cat: 'cafe' })); CAFE.loaded = true; }
  catch (e) { toast(e.message, true); }
  CAFE.loading = false;
  if (S.picker) refreshPicker();
}
async function openChain(name) {
  const pk = S.picker; pk.chain = name; pk.chainItems = []; pk.loading = true; refreshPicker();
  const my = ++pkSeq;
  let items = [];
  try { items = (await api('/api/foods_search?brand=' + encodeURIComponent(name))).items || []; } catch (e) { toast(e.message, true); }
  if (my !== pkSeq || !S.picker || S.picker.chain !== name) return;
  S.picker.chainItems = items.map(fromServer); S.picker.loading = false; refreshPicker();
}
async function loadChains() {
  const pk = S.picker; if (pk.chains) return;
  pk.chains = [];
  try { pk.chains = (await api('/api/foods_chains')).items || []; } catch (e) { toast(e.message, true); }
  if (S.picker === pk) { if (!pk.chain && pk.chains.length) openChain(pk.chains[0].name); else refreshPicker(); }
}
function pickerHtml() {
  const pk = S.picker;
  let h = '<div class="search">' + ic('search') + '<input id="pkq" data-in="picker.q" placeholder="Продукт, блюдо или «кфс стрипсы»" value="' + esc(pk.q) + '" autocomplete="off"></div>';
  if (S.d.features && S.d.features.recognize && !pk.replace) {
    h += '<label class="ai-btn"><span class="ic">' + ic('camera') + '</span><span class="grow"><span class="ttl" style="display:block">Распознать по фото</span>' +
      '<span class="sub">Сфотографируйте тарелку — посчитаем калории</span></span>' + ic('chev-r', 'chev') +
      '<input type="file" accept="image/*" class="vh" data-ch="aiPhoto"></label>';
  }
  h += '<div id="pkTabs">' + pickerTabs() + '</div>';
  h += '<div class="group" id="pkList">' + pickerList() + '</div>';
  h += '<div class="link-row"><button class="btn link" data-act="manual">Добавить вручную</button></div>';
  return h;
}
function pickerTabs() {
  const pk = S.picker;
  if (pk.q) return '';
  let h = '<div class="seg" style="margin-bottom:10px">' + [['recent', 'Недавние'], ['base', 'Каталог'], ['mine', 'Мои']].map(t => '<button class="' + (pk.tab === t[0] ? 'on' : '') + '" data-act="pk-tab" data-t="' + t[0] + '">' + t[1] + '</button>').join('') + '</div>';
  if (pk.tab === 'base') {
    h += '<div class="chips" style="margin-bottom:10px">' + CATS.map(c => '<button class="chip' + (pk.cat === c[0] ? ' on' : '') + '" data-act="pk-cat" data-c="' + c[0] + '">' + c[1] + '</button>').join('') + '</div>';
    if (pk.cat === 'cafe') {
      const chains = [...new Set(CAFE.items.map(x => x.brand))].sort((a, b) => a.localeCompare(b, 'ru'));
      h += '<div class="chips" style="margin-bottom:10px">' + [['', 'Все сети']].concat(chains.map(c => [c, c])).map(c =>
        '<button class="chip sm' + ((pk.chain || '') === c[0] ? ' on' : '') + '" data-act="pk-chain" data-c="' + esc(c[0]) + '">' + esc(c[1]) + '</button>').join('') + '</div>';
      if (!CAFE.loaded) h += '<p class="note" style="margin:0 2px 10px">Загружаем меню кафе…</p>';
    }
  }
  return h;
}
function refreshPicker() {
  if (!S.picker || S.picker.sel || S.picker.manual) return;
  const t = $('#pkTabs'), l = $('#pkList');
  if (t) t.innerHTML = pickerTabs();
  if (l) l.innerHTML = pickerList();
}
function openPicker(meal, replaceId) {
  const hasRecent = S.d.recent.length > 0;
  S.picker = { meal, replace: replaceId || 0, tab: (hasRecent && !replaceId) ? 'recent' : 'base', cat: 'all', q: '', sel: null, g: 100, manual: false,
    remote: [], remoteQ: '', loading: false, chains: null, chain: '', chainItems: [] };
  loadCafe();
  openSheet(replaceId ? 'Заменить продукт' : mealOf(meal).n, pickerHtml());
}
function amountHtml() {
  const pk = S.picker, it = pk.sel;
  let chips;
  if (it.portions && it.portions.length) chips = it.portions.map(x => [x.g, x.label + ' · ' + n0(x.g) + ' г']);
  else {
    const g = [50, 100, 150, 200, 250];
    if (it.g && g.indexOf(Math.round(it.g)) < 0) g.unshift(Math.round(it.g));
    chips = g.map(v => [v, v + ' г']);
  }
  return '<div class="pk-head">' + thumbHtml(it) + '<div class="grow"><div class="ttl">' + esc(it.name) + '</div><div class="sub">' +
    (it.brand ? esc(it.brand) + ' · ' : '') + n0(it.cal) + ' ккал на 100 г</div><div class="sub">' + macLine(it.p, it.f, it.c) + '</div></div></div>' +
    '<div class="field"><span>Вес порции, г</span><div class="stepper"><button class="btn sec" data-act="pk-step" data-v="-10" aria-label="Меньше">' + ic('minus') + '</button>' +
    '<input id="pkg" inputmode="decimal" data-in="picker.g" value="' + esc(pk.g) + '"><button class="btn sec" data-act="pk-step" data-v="10" aria-label="Больше">' + ic('plus') + '</button></div></div>' +
    '<div class="chips" style="margin:10px 0 0">' + chips.map(v => '<button class="chip' + (Math.round(num(pk.g)) === Math.round(v[0]) ? ' on' : '') + '" data-act="pk-set" data-v="' + v[0] + '">' + esc(v[1]) + '</button>').join('') + '</div>' +
    '<div class="pv"><span>Итого</span><span><b id="pvCal">0</b> ккал</span></div><div id="pvMac" class="sub" style="margin:-4px 2px 14px"></div>' +
    '<div class="btns"><button class="btn sec" data-act="pk-back">Назад</button><button class="btn" data-act="pk-add">Добавить</button></div>';
}
function updateAmount() {
  const pk = S.picker; if (!pk || !pk.sel) return;
  const g = num(pk.g), k = g / 100, it = pk.sel;
  const a = $('#pvCal'), b = $('#pvMac');
  if (a) a.textContent = n0(it.cal * k);
  if (b) b.innerHTML = macLine(it.p * k, it.f * k, it.c * k);
}
function manualHtml() {
  const f = S.mf;
  const fld = (k, l, ph) => '<label class="field"><span>' + l + '</span><input data-in="mf.' + k + '" inputmode="decimal" placeholder="' + (ph || '') + '" value="' + esc(f[k]) + '"></label>';
  return '<div class="stack"><label class="field"><span>Название</span><input data-in="mf.name" placeholder="Например, домашний борщ" value="' + esc(f.name) + '"></label>' +
    '<div class="cols2">' + fld('cal', 'Ккал на 100 г', '0') + fld('g', 'Вес порции, г', '100') + '</div>' +
    '<div class="cols3">' + fld('p', 'Белки', '0') + fld('f', 'Жиры', '0') + fld('c', 'Углеводы', '0') + '</div>' +
    '<p class="note" style="margin:0">Значения указываются на 100 г продукта, как на упаковке.</p>' +
    '<div class="btns"><button class="btn sec" data-act="pk-back">Назад</button><button class="btn" data-act="mf-add">Добавить</button></div>' +
    '<button class="btn soft" data-act="mf-save">Добавить и сохранить в «Мои»</button></div>';
}
async function addPicked(it, grams) {
  const k = grams / 100;
  return run(() => post('/api/add_meal', { date: S.date, meal_type: S.picker.meal, name: it.full || it.name, grams, cal: +(it.cal * k).toFixed(1), p: +(it.p * k).toFixed(1), f: +(it.f * k).toFixed(1), c: +(it.c * k).toFixed(1) }), 'Добавлено: ' + it.name);
}

/* ---------- запись приёма пищи ---------- */
function openEdit(id) {
  const m = S.d.day_meals.find(x => x.id === id); if (!m) return;
  S.edit = { id, g: String(Math.round(m.grams)), type: m.meal_type, g0: Math.round(m.grams) };
  openSheet(m.food_name, editHtml(m));
  loadPhotos();
}
function editHtml(m) {
  const e = S.edit;
  const photo = m.photo
    ? '<img class="photo-big" data-photo="' + m.id + '" alt="Фото блюда" src="' + (PHOTO[m.id] || '') + '">' +
      '<div class="btns" style="margin-bottom:16px"><label class="btn sec sm">' + ic('camera') + 'Заменить<input type="file" accept="image/*" class="vh" data-ch="mealPhoto"></label>' +
      '<button class="btn dng sm" data-act="photo-del">' + ic('trash') + 'Убрать фото</button></div>'
    : '<label class="btn soft" style="margin-bottom:16px">' + ic('camera') + 'Добавить фото блюда<input type="file" accept="image/*" class="vh" data-ch="mealPhoto"></label>';
  return photo + '<div class="field"><span>Вес порции, г</span><div class="stepper"><button class="btn sec" data-act="ed-step" data-v="-10" aria-label="Меньше">' + ic('minus') + '</button>' +
    '<input id="edg" inputmode="decimal" data-in="edit.g" value="' + esc(e.g) + '"><button class="btn sec" data-act="ed-step" data-v="10" aria-label="Больше">' + ic('plus') + '</button></div></div>' +
    '<div class="pv"><span>Итого</span><span><b id="edCal">' + n0(m.calories) + '</b> ккал</span></div><div id="edMac" class="sub" style="margin:-4px 2px 12px">' + macLine(m.p, m.f, m.c) + '</div>' +
    '<div class="field" style="margin-bottom:16px"><span>Приём пищи</span><div class="chips">' + MEALS.map(x => '<button class="chip' + (e.type === x.k ? ' on' : '') + '" data-act="ed-type" data-m="' + x.k + '">' + x.n + '</button>').join('') + '</div></div>' +
    '<div class="stack"><button class="btn" data-act="ed-save">Сохранить</button><div class="btns"><button class="btn sec" data-act="ed-replace">Заменить</button><button class="btn dng" data-act="ed-del">' + ic('trash') + 'Удалить</button></div></div>';
}
function updateEdit() {
  const e = S.edit; if (!e) return;
  const m = S.d.day_meals.find(x => x.id === e.id); if (!m || !m.grams) return;
  const k = num(e.g) / m.grams;
  const a = $('#edCal'), b = $('#edMac');
  if (a) a.textContent = n0(m.calories * k);
  if (b) b.innerHTML = macLine(m.p * k, m.f * k, m.c * k);
}

/* ---------- фото блюда ---------- */
async function uploadMealPhoto(inp) {
  const file = inp.files && inp.files[0]; inp.value = '';
  if (!file || !S.edit) return;
  const id = S.edit.id;
  let data;
  try { data = await shrinkPhoto(file, 720, 0.72); } catch (e) { toast(e.message, true); return; }
  try { await post('/api/meal_photo', { id, image: data }); } catch (e) { toast(e.message, true); return; }
  PHOTO[id] = data;
  const m = S.d.day_meals.find(x => x.id === id); if (m) m.photo = true;
  closeSheet(); render(); toast('Фото добавлено');
}

/* ---------- распознавание еды по фото ---------- */
async function recognizePhoto(inp) {
  const file = inp.files && inp.files[0]; inp.value = '';
  if (!file || !S.picker) return;
  let data;
  try { data = await shrinkPhoto(file, 1024, 0.8); } catch (e) { toast(e.message, true); return; }
  S.ai = { photo: data, items: null, busy: true, note: '' };
  setSheet('Распознаём…', aiHtml());
  try {
    const r = await post('/api/recognize', { image: data });
    S.ai.items = (r.items || []).map(x => Object.assign(x, { on: true, g: String(x.grams) }));
    S.ai.note = r.note || '';
  } catch (e) { S.ai.error = e.message; }
  S.ai.busy = false;
  if (S.picker) setSheet('Что на фото', aiHtml());
}
function aiHtml() {
  const a = S.ai;
  let h = '<img class="ai-photo" src="' + a.photo + '" alt="Ваше фото">';
  if (a.busy) return h + '<div class="spin"></div><p class="note" style="text-align:center">Определяем блюда и вес порций…</p>';
  if (a.error) return h + '<div class="empty">' + esc(a.error) + '</div><div class="btns"><button class="btn sec" data-act="pk-back">Назад</button></div>';
  if (!a.items.length) return h + '<div class="empty">' + esc(a.note || 'Не удалось распознать еду. Попробуйте сфотографировать ближе и при хорошем свете.') + '</div><div class="btns"><button class="btn sec" data-act="pk-back">Назад</button></div>';
  h += '<div class="group pad" style="padding-top:4px;padding-bottom:4px">' + a.items.map((x, i) =>
    '<div class="ai-row"><button class="ck2' + (x.on ? ' on' : '') + '" data-act="ai-toggle" data-i="' + i + '" aria-label="Выбрать">' + ic('check') + '</button>' +
    '<div><div class="ttl">' + esc(x.name) + '</div><div class="sub" id="aiK' + i + '">' + n0(x.cal * num(x.g) / 100) + ' ккал</div></div>' +
    '<input class="g" inputmode="decimal" data-in="ai.items.' + i + '.g" value="' + esc(x.g) + '" aria-label="Вес, г"></div>').join('') + '</div>' +
    '<p class="note">Вес и калории — оценка по фото. Поправьте граммы, если знаете точнее.</p>' +
    (a.note ? '<p class="note">' + esc(a.note) + '</p>' : '') +
    '<div class="btns" style="margin-top:14px"><button class="btn sec" data-act="pk-back">Назад</button><button class="btn" data-act="ai-add">Добавить</button></div>';
  return h;
}

/* ---------- статистика (только владелец) ---------- */
async function loadStats() {
  S.stats = null; render();
  try { S.stats = await api('/api/admin_stats'); } catch (e) { S.stats = { error: e.message }; }
  if (S.page === 'stats') render();
}
function renderStats() {
  const st = S.stats;
  let h = pageHead('Статистика');
  if (!st) return h + '<div class="loading">Загружаем…</div>';
  if (st.error) return h + '<div class="group"><div class="empty">' + esc(st.error) + '</div></div>';
  const tile = (n, v, sub) => '<div class="tile"><div class="n">' + n + '</div><div class="v">' + n0(v) + (sub ? ' <small>' + sub + '</small>' : '') + '</div></div>';
  h += '<div class="tiles">' + tile('Всего пользователей', st.total) + tile('Новых сегодня', st.new_today) +
    tile('Новых за 7 дней', st.new_7d) + tile('Новых за 30 дней', st.new_30d) +
    tile('Заходили сегодня', st.active_today) + tile('Заходили за 7 дней', st.active_7d) +
    tile('Вернулись повторно', st.returning) + tile('Ведут дневник', st.diary_users) + '</div>';
  const mx = Math.max(1, ...st.by_day.map(x => x.new));
  h += '<div class="h2">Новые по дням</div><div class="group pad"><div class="sbars">' + st.by_day.map(x =>
    '<div class="c"><span class="v">' + (x.new || '') + '</span><i class="b' + (x.new ? '' : ' z') + '" style="height:' + Math.max(3, x.new / mx * 80) + 'px"></i><span class="d">' + x.date.slice(8) + '</span></div>').join('') + '</div></div>';
  const ago = iso => { const m = Math.round((Date.now() - new Date(iso)) / 60000); return m < 60 ? m + ' мин назад' : m < 1440 ? Math.round(m / 60) + ' ч назад' : Math.round(m / 1440) + ' дн назад'; };
  h += '<div class="h2">Последние визиты</div><div class="group">' + (st.recent.length ? st.recent.map(u =>
    '<div class="row"><span class="avatar" style="width:36px;height:36px">' + esc(u.name.slice(0, 1).toUpperCase()) + '</span><div class="grow"><div class="ttl">' + esc(u.name) + (u.username ? ' <span class="sub">@' + esc(u.username) + '</span>' : '') + '</div>' +
    '<div class="sub">' + ago(u.last_seen) + ' · визитов ' + u.visits + ' · записей еды ' + u.meals + '</div></div></div>').join('') : '<div class="empty">Пока никого.</div>') + '</div>' +
    '<p class="note">Визит — открытие приложения; повторные открытия в течение 30 минут считаются одним визитом. Эта же сводка приходит в бот по команде /stats.</p>';
  return h;
}

/* ================= Меню ================= */
function renderPlan() {
  const d = S.d, s = d.settings, by = mealsByType();
  const ex = s.excluded || [];
  const names = { fish: 'Рыба и морепродукты', meat: 'Мясо и птица', dairy: 'Молочные продукты' };
  let h = '<div class="h2" style="margin-top:6px">Меню на день</div><div class="group pad"><div class="stack">' +
    '<p class="mut" style="margin:0;font-size:14px">Составим завтрак, обед, ужин и перекус под вашу норму ' + n0(s.target_cal) + ' ккал. Текущие записи за ' + esc(dateLabel(S.date).toLowerCase()) + ' будут заменены.</p>' +
    '<button class="btn" data-act="gen-day">' + ic('spark') + 'Собрать меню</button>' +
    '<div class="btns"><button class="btn sec sm" data-act="copy-prev">' + ic('copy') + 'Как вчера</button><button class="btn sec sm" data-act="clear-day">' + ic('trash') + 'Очистить</button></div></div></div>';

  h += '<div class="h2">Не включать в меню</div><div class="group">' + ['fish', 'meat', 'dairy'].map(k =>
    '<button class="row" data-act="excl" data-k="' + k + '"><div class="grow ttl">' + names[k] + '</div><span class="sw' + (ex.includes(k) ? ' on' : '') + '"></span></button>').join('') + '</div>';

  if (d.day_meals.length) {
    h += '<div class="h2">Состав дня <small>' + n0(d.today_cal) + ' ккал</small></div>';
    MEALS.forEach(m => {
      if (!by[m.k].length) return;
      h += '<div class="group" style="margin-bottom:10px"><div class="row" style="min-height:44px"><span class="ico-box" style="width:28px;height:28px">' + ic(m.icon, '') + '</span><div class="ttl grow">' + m.n + '</div><div class="sub">' + n0(by[m.k].reduce((a, x) => a + x.calories, 0)) + ' ккал</div></div>' +
        by[m.k].map(x => '<div class="row" style="min-height:44px"><div class="grow">' + esc(x.food_name) + '</div><div class="sub">' + n0(x.grams) + ' г</div></div>').join('') + '</div>';
    });
    const agg = {};
    d.day_meals.forEach(x => { agg[x.food_name] = (agg[x.food_name] || 0) + x.grams; });
    const keys = Object.keys(agg).sort((a, b) => a.localeCompare(b, 'ru'));
    const done = keys.filter(k => S.shop.has(k)).length;
    h += '<div class="h2">Список покупок <small>' + done + ' из ' + keys.length + '</small></div><div class="group">' + keys.map(k =>
      '<button class="row shop-row' + (S.shop.has(k) ? ' done' : '') + '" data-act="shop" data-k="' + esc(k) + '"><span class="ck">' + ic('check') + '</span><div class="ttl grow">' + esc(k) + '</div><div class="sub">' + n0(agg[k]) + ' г</div></button>').join('') + '</div>' +
      '<p class="note">Граммы указаны для приготовленного продукта, сырого понадобится больше. Список строится по выбранному дню.</p>';
  }

  h += '<div class="h2">Мои рецепты <small>' + d.custom_foods.length + '</small></div>';
  h += d.custom_foods.length ? '<div class="group">' + d.custom_foods.map(x =>
    '<div class="row"><div class="grow"><div class="ttl">' + esc(x.name) + '</div><div class="sub i2"><span>' + n0(x.cal) + ' ккал / 100 г</span>' + macLine(x.p, x.f, x.c) + '</div>' +
    (x.meal_type !== 'none' ? '<div class="sub ok" style="margin-top:2px">В автоменю: ' + (x.meal_type === 'any' ? 'любой приём' : MEAL_GEN[x.meal_type]) + '</div>' : '') + '</div>' +
    '<button class="x" data-act="del-recipe" data-id="' + x.id + '" aria-label="Удалить">' + ic('trash') + '</button></div>').join('') + '</div>'
    : '<div class="group"><div class="empty">Сохраняйте любимые блюда с известной калорийностью. Их можно добавлять в дневник и подставлять в автоменю.</div></div>';
  h += '<div style="margin-top:10px"><button class="btn soft" data-act="new-recipe">' + ic('plus') + 'Новый рецепт</button></div>';
  return h;
}
function recipeHtml() {
  const f = S.rf;
  const fld = (k, l, ph) => '<label class="field"><span>' + l + '</span><input data-in="rf.' + k + '" inputmode="decimal" placeholder="' + (ph || '') + '" value="' + esc(f[k]) + '"></label>';
  return '<div class="stack"><label class="field"><span>Название</span><input data-in="rf.name" placeholder="Например, сырники" value="' + esc(f.name) + '"></label>' +
    fld('cal', 'Ккал на 100 г', '0') + '<div class="cols3">' + fld('p', 'Белки', '0') + fld('f', 'Жиры', '0') + fld('c', 'Углеводы', '0') + '</div>' +
    '<label class="field"><span>Использовать в автоменю</span><select data-ch="rf.meal_type">' + MENU_USE.map(o => '<option value="' + o.v + '"' + (f.meal_type === o.v ? ' selected' : '') + '>' + o.t + '</option>').join('') + '</select></label>' +
    '<button class="btn" data-act="rf-save">Сохранить рецепт</button></div>';
}

/* ================= Тренировки ================= */
function programInfo() {
  const s = S.d.settings;
  const p = S.d.programs.find(x => x.id === s.program);
  if (!p) return null;
  const di = s.program_day % p.days.length;
  return { p, di, day: p.days[di] };
}
function setsByExercise() {
  const out = [], idx = {};
  S.d.day_sets.forEach(x => { if (!(x.exercise in idx)) { idx[x.exercise] = out.length; out.push({ ex: x.exercise, sets: [] }); } out[idx[x.exercise]].sets.push(x); });
  return out;
}
function lastText(ex) {
  const l = S.d.last_sets[ex];
  if (!l) return '';
  return 'Прошлый раз: ' + (l.weight > 0 ? n1(l.weight) + ' кг × ' : '') + l.reps;
}
function renderWork() {
  const d = S.d;
  let h = '<div class="seg" style="margin:6px 0 4px">' + [['strength', 'Силовые'], ['cardio', 'Кардио']].map(t => '<button class="' + (S.workTab === t[0] ? 'on' : '') + '" data-act="work-tab" data-t="' + t[0] + '">' + t[1] + '</button>').join('') + '</div>';
  if (S.workTab === 'cardio') return h + renderCardio();

  const pi = programInfo();
  if (pi) {
    h += '<div class="h2">Программа <small>' + esc(pi.p.name) + '</small></div><div class="group"><div class="row" style="min-height:48px"><div class="grow"><div class="ttl">' + esc(pi.day.name) + '</div><div class="sub">День ' + (pi.di + 1) + ' из ' + pi.p.days.length + '</div></div>' +
      '<button class="btn sm soft" style="width:auto" data-act="next-day">Следующий день</button></div>' +
      pi.day.exercises.map(e => {
        const done = d.day_sets.filter(x => x.exercise === e.name).length;
        return '<button class="row" data-act="pick-ex" data-ex="' + esc(e.name) + '"><div class="grow"><div class="ttl">' + esc(e.name) + '</div><div class="sub">' + e.sets + ' × ' + esc(e.reps) + (lastText(e.name) ? ' · ' + lastText(e.name) : '') + '</div></div>' +
          '<div class="end ' + (done >= e.sets ? 'ok' : 'mut') + '">' + done + '/' + e.sets + '</div></button>';
      }).join('') + '</div><div class="link-row"><button class="btn link" data-act="programs">Сменить программу</button></div>';
  } else {
    h += '<div class="h2">Программа</div><div class="group"><button class="row" data-act="programs"><span class="ico-box">' + ic('list') + '</span><div class="grow"><div class="ttl">Выбрать готовую программу</div><div class="sub">Фулбоди, верх-низ или тренировки дома</div></div>' + ic('chev-r', 'chev') + '</button></div>';
  }

  const f = S.form, opts = d.exercises.map(e => '<option value="' + esc(e) + '"' + (f.ex === e ? ' selected' : '') + '>' + esc(e) + '</option>').join('');
  h += '<div class="h2">Записать подход</div><div class="group pad"><div class="stack">' +
    '<label class="field"><span>Упражнение</span><select data-ch="form.ex"><option value="">Выберите упражнение</option>' + opts + '</select></label>' +
    '<div class="sub" id="lastHint">' + (f.ex ? esc(lastText(f.ex)) : '') + '</div>' +
    '<div class="cols2"><label class="field"><span>Вес, кг</span><input data-in="form.w" inputmode="decimal" placeholder="0" value="' + esc(f.w) + '"></label>' +
    '<label class="field"><span>Повторы</span><input data-in="form.r" inputmode="numeric" placeholder="0" value="' + esc(f.r) + '"></label></div>' +
    '<div class="btns"><button class="btn" data-act="add-set">' + ic('plus') + 'Записать</button><button class="btn sec" data-act="timer">' + ic('timer') + 'Отдых</button></div></div></div>';

  const grp = setsByExercise();
  h += '<div class="h2">Сегодня <small>' + d.day_sets.length + ' подходов</small></div>';
  if (!grp.length) h += '<div class="group"><div class="empty">Подходов пока нет. Выберите упражнение и запишите первый.</div></div>';
  else h += grp.map(g => '<div class="group" style="margin-bottom:10px"><div class="row" style="min-height:44px"><div class="ttl grow">' + esc(g.ex) + '</div>' +
    (d.prs[g.ex] && g.sets.some(x => x.weight >= d.prs[g.ex] && x.weight > 0) ? '<span class="sub ok">рекорд</span>' : '') + '</div>' +
    g.sets.map((x, i) => '<div class="row" style="min-height:44px"><div class="sub" style="width:22px">' + (i + 1) + '</div><div class="grow ttl">' + (x.weight > 0 ? n1(x.weight) + ' кг × ' : '') + x.reps + '</div>' +
      '<button class="x" data-act="del-set" data-id="' + x.id + '" aria-label="Удалить подход">' + ic('x') + '</button></div>').join('') + '</div>').join('');
  return h;
}
function renderCardio() {
  const d = S.d, c = S.cardio;
  const sel = c.idx === '' ? null : d.workouts[+c.idx];
  const est = sel ? Math.round(sel.met * latestWeight() * num(c.mins) / 60) : 0;
  let h = '<div class="h2">Записать тренировку</div><div class="group pad"><div class="stack">' +
    '<label class="field"><span>Вид активности</span><select data-ch="cardio.idx"><option value="">Другое</option>' + d.workouts.map((w, i) => '<option value="' + i + '"' + (String(c.idx) === String(i) ? ' selected' : '') + '>' + esc(w.name) + '</option>').join('') + '</select></label>' +
    (sel ? '' : '<label class="field"><span>Название</span><input data-in="cardio.name" placeholder="Например, танцы" value="' + esc(c.name) + '"></label>') +
    '<div class="cols2"><label class="field"><span>Минут</span><input data-in="cardio.mins" inputmode="numeric" value="' + esc(c.mins) + '"></label>' +
    '<label class="field"><span>Сожжено, ккал</span><input id="cardioCal" data-in="cardio.cal" inputmode="numeric" placeholder="' + (sel ? est : '0') + '" value="' + esc(c.cal) + '"></label></div>' +
    (sel ? '<p class="note" style="margin:0">Оценка по вашему весу ' + n1(latestWeight()) + ' кг. Если пульсометр показал другое число, впишите его.</p>' : '') +
    '<button class="btn" data-act="add-cardio">Записать</button></div></div>';
  h += '<div class="h2">Сегодня</div>';
  h += d.day_workouts.length ? '<div class="group">' + d.day_workouts.map(w => '<div class="row"><span class="ico-box">' + ic('bolt') + '</span><div class="grow"><div class="ttl">' + esc(w.name) + '</div><div class="sub">' + n0(w.mins) + ' мин · ' + n0(w.cal) + ' ккал</div></div><button class="x" data-act="del-workout" data-id="' + w.id + '" aria-label="Удалить">' + ic('trash') + '</button></div>').join('') + '</div>'
    : '<div class="group"><div class="empty">Записей за этот день нет.</div></div>';
  return h;
}
function programsHtml() {
  const s = S.d.settings;
  return '<div class="stack">' + S.d.programs.map(p => '<button class="opt' + (s.program === p.id ? ' on' : '') + '" data-act="set-program" data-id="' + p.id + '"><div class="grow"><div class="ttl">' + esc(p.name) + '</div>' +
    '<div class="sub">' + esc(p.level) + ' · ' + esc(p.place) + '</div><div class="sub" style="margin-top:4px">' + esc(p.desc) + '</div></div></button>').join('') +
    '<button class="opt' + (!s.program ? ' on' : '') + '" data-act="set-program" data-id=""><div class="grow"><div class="ttl">Без программы</div><div class="sub">Выбирать упражнения самому</div></div></button></div>';
}
function timerHtml() {
  return '<div class="timer"><div class="t" id="timerT">1:30</div><div class="chips">' + [60, 90, 120, 180].map(v => '<button class="chip' + (S.timerLen === v ? ' on' : '') + '" data-act="timer-len" data-v="' + v + '">' + (v / 60 | 0) + ':' + String(v % 60).padStart(2, '0') + '</button>').join('') + '</div>' +
    '<div class="btns"><button class="btn" data-act="timer-go">Запустить</button><button class="btn sec" data-act="timer-stop">Сбросить</button></div></div>';
}
function fmtT(sec) { sec = Math.max(0, Math.ceil(sec)); return (sec / 60 | 0) + ':' + String(sec % 60).padStart(2, '0'); }
function timerTick() {
  const el = $('#timerT');
  if (!el) { clearInterval(S.timerId); S.timerId = null; return; }
  const left = (S.timerEnd - Date.now()) / 1000;
  el.textContent = fmtT(left);
  if (left <= 0) {
    clearInterval(S.timerId); S.timerId = null; el.classList.add('done'); el.textContent = 'Пора';
    if (tg && tg.HapticFeedback) { try { tg.HapticFeedback.notificationOccurred('success'); } catch (e) {} }
    try { if (navigator.vibrate) navigator.vibrate([200, 100, 200]); } catch (e) {}
  }
}

/* ================= Прогресс ================= */
const CHARTS = {};
function rangeEntries() {
  const e = S.d.entries.filter(x => x.weight > 0);
  if (!S.range) return e;
  const from = addDays(localDate(), -S.range);
  return e.filter(x => x.date >= from);
}
function renderProgress() {
  const d = S.d, s = d.settings, today = localDate();
  const we = rangeEntries();
  const allW = d.entries.filter(x => x.weight > 0);
  const cur = allW.length ? allW[allW.length - 1].weight : 0;
  const delta = we.length > 1 ? we[we.length - 1].weight - we[0].weight : 0;
  const days7 = [];
  for (let i = 6; i >= 0; i--) days7.push(addDays(today, -i));
  const calOf = ds => { const e = d.entries.find(x => x.date === ds); return e ? e.calories : 0; };
  const logged = days7.filter(ds => calOf(ds) > 0);
  const avg = logged.length ? logged.reduce((a, ds) => a + calOf(ds), 0) / logged.length : 0;
  const wk = Object.keys(d.burned_by_date).filter(k => k >= addDays(today, -6)).length;

  let h = '<div class="tiles" style="margin-top:6px">' +
    '<div class="tile"><div class="n">Вес сейчас</div><div class="v">' + (cur ? n1(cur) : '—') + ' <small>кг</small></div></div>' +
    '<div class="tile"><div class="n">До цели</div><div class="v">' + (cur && s.target_weight ? n1(Math.abs(cur - s.target_weight)) : '—') + ' <small>кг</small></div></div>' +
    '<div class="tile"><div class="n">Среднее за неделю</div><div class="v">' + (avg ? n0(avg) : '—') + ' <small>ккал</small></div></div>' +
    '<div class="tile"><div class="n">Серия</div><div class="v">' + d.streak + ' <small>' + plural(d.streak, 'день', 'дня', 'дней') + '</small></div></div></div>';

  h += '<div class="h2">Вес</div><div class="chips" style="margin-bottom:10px">' + [[7, '7 дней'], [30, 30 + ' дней'], [90, '3 месяца'], [0, 'Всё время']].map(r => '<button class="chip' + (S.range === r[0] ? ' on' : '') + '" data-act="range" data-v="' + r[0] + '">' + r[1] + '</button>').join('') + '</div>' +
    '<div class="group pad">' + (we.length > 1 ? '<canvas class="chart" id="cWeight"></canvas><div class="sub" style="margin-top:6px">' + (delta === 0 ? 'Без изменений за период' : (delta > 0 ? '+' : '−') + n1(Math.abs(delta)) + ' кг за период') + '</div>' :
      '<div class="empty" style="padding:8px 0">Для графика нужно минимум два взвешивания.</div>') + '</div>';
  CHARTS.cWeight = we.length > 1 ? { pts: we.map(x => ({ t: new Date(x.date + 'T00:00:00').getTime(), y: x.weight })), line: s.target_weight, fmt: n1, tfmt: 'date' } : null;

  const maxC = Math.max(s.target_cal, ...days7.map(calOf), 1) * 1.08;
  h += '<div class="h2">Калории за неделю <small>среднее ' + (avg ? n0(avg) : '—') + '</small></div><div class="group pad"><div class="cal-bars">' +
    '<div class="cal-line" style="bottom:calc(' + (s.target_cal / maxC * 100).toFixed(1) + '% * 0.82 + 22px)"></div>' +
    days7.map(ds => { const c = calOf(ds); return '<div class="col' + (ds === today ? ' cur' : '') + '"><div class="b' + (c ? (c > s.target_cal * 1.05 ? ' over' : '') : ' none') + '" style="height:calc(' + (c / maxC * 100).toFixed(1) + '% * 0.82)"></div><div class="d">' + new Intl.DateTimeFormat('ru-RU', { weekday: 'short' }).format(new Date(ds + 'T00:00:00')) + '</div></div>'; }).join('') + '</div>' +
    '<div class="sub" style="margin-top:8px">Пунктир — ваша норма ' + n0(s.target_cal) + ' ккал. Красным отмечены дни с превышением. Тренировок за неделю: ' + wk + '.</div></div>';

  const exs = Object.keys(d.prs).sort((a, b) => a.localeCompare(b, 'ru'));
  h += '<div class="h2">Силовые</div>';
  if (!exs.length) h += '<div class="group"><div class="empty">Записывайте подходы на вкладке «Тренировки», и здесь появится график роста по каждому упражнению.</div></div>';
  else {
    if (!S.exSel || !exs.includes(S.exSel)) S.exSel = exs[0];
    const hist = S.exHist && S.exHist.exercise === S.exSel ? S.exHist.points : null;
    h += '<div class="group pad"><div class="stack"><select data-ch="exSel">' + exs.map(e => '<option value="' + esc(e) + '"' + (S.exSel === e ? ' selected' : '') + '>' + esc(e) + '</option>').join('') + '</select>' +
      '<div class="seg">' + [['e1rm', 'Расчётный 1ПМ'], ['weight', 'Вес'], ['volume', 'Объём']].map(t => '<button class="' + (S.exMetric === t[0] ? 'on' : '') + '" data-act="metric" data-v="' + t[0] + '">' + t[1] + '</button>').join('') + '</div>' +
      (hist === null ? '<div class="empty" style="padding:8px 0">Загружаем историю…</div>' : (hist.length > 1 ? '<canvas class="chart" id="cEx"></canvas>' : '<div class="empty" style="padding:8px 0">Нужно минимум две тренировки с этим упражнением.</div>')) +
      '<div class="sub">Рекорд по весу: <b style="color:var(--ink)">' + n1(d.prs[S.exSel]) + ' кг</b></div></div></div>';
    if (hist && hist.length > 1) {
      const key = S.exMetric;
      CHARTS.cEx = { pts: hist.map(p => ({ t: new Date(p.date + 'T00:00:00').getTime(), y: p[key] })), fmt: key === 'volume' ? n0 : n1, tfmt: 'date' };
    } else CHARTS.cEx = null;
    if (hist === null) loadHistory(S.exSel);
  }

  h += '<div class="h2">Записать замер</div><div class="group pad"><div class="stack"><div class="cols2"><label class="field"><span>Дата</span><input type="date" data-in="pg.date" value="' + S.pg.date + '" max="' + today + '"></label>' +
    '<label class="field"><span>Вес, кг</span><input data-in="pg.w" inputmode="decimal" placeholder="' + (cur ? n1(cur) : '0') + '" value="' + esc(S.pg.w) + '"></label></div>' +
    '<div class="cols2"><label class="field"><span>Шаги</span><input data-in="pg.s" inputmode="numeric" placeholder="0" value="' + esc(S.pg.s) + '"></label>' +
    '<label class="field"><span>Заметка</span><input data-in="pg.note" placeholder="Необязательно" value="' + esc(S.pg.note) + '"></label></div>' +
    '<button class="btn" data-act="add-entry">Сохранить замер</button></div></div>';

  const hist2 = d.entries.filter(x => x.weight > 0 || x.steps > 0 || x.note).slice().reverse().slice(0, 14);
  if (hist2.length) h += '<div class="h2">История замеров</div><div class="group">' + hist2.map(x => '<div class="row"><div class="grow"><div class="ttl">' + (x.weight ? n1(x.weight) + ' кг' : 'Без веса') + '</div><div class="sub">' + ddmm(x.date) + (x.steps ? ' · ' + n0(x.steps) + ' шагов' : '') + (x.note ? ' · ' + esc(x.note) : '') + '</div></div>' +
    '<button class="x" data-act="del-entry" data-d="' + x.date + '" aria-label="Удалить замер">' + ic('trash') + '</button></div>').join('') + '</div>';
  return h;
}
function plural(n, a, b, c) { n = Math.abs(n) % 100; const m = n % 10; return (n > 10 && n < 20) ? c : (m > 1 && m < 5) ? b : (m === 1 ? a : c); }
async function loadHistory(ex) {
  try {
    const r = await api('/api/exercise_history?exercise=' + encodeURIComponent(ex));
    S.exHist = r;
  } catch (e) { S.exHist = { exercise: ex, points: [] }; }
  if (S.tab === 'progress' && !S.page) render();
}

function drawLine(cv, cfg) {
  if (!cv || !cfg) return;
  const cs = getComputedStyle(document.documentElement);
  const col = n => cs.getPropertyValue(n).trim();
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext('2d'); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
  const pts = cfg.pts, L = 38, R = 10, T = 12, B = 24;
  let lo = Math.min(...pts.map(p => p.y)), hi = Math.max(...pts.map(p => p.y));
  if (cfg.line) { lo = Math.min(lo, cfg.line); hi = Math.max(hi, cfg.line); }
  if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
  const padY = (hi - lo) * 0.12; lo -= padY; hi += padY;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const X = t => L + (t1 === t0 ? 0.5 : (t - t0) / (t1 - t0)) * (W - L - R);
  const Y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  g.font = '600 11px ' + (col('--font') || 'sans-serif'); g.textBaseline = 'middle';
  g.strokeStyle = col('--line'); g.fillStyle = col('--ink-3'); g.lineWidth = 1;
  for (let i = 0; i < 4; i++) {
    const v = lo + (hi - lo) * i / 3, y = Y(v);
    g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke();
    g.textAlign = 'right'; g.fillText((cfg.fmt || n1)(v), L - 6, y);
  }
  g.textAlign = 'center'; g.textBaseline = 'alphabetic';
  [0, 1].forEach(i => { const p = pts[i ? pts.length - 1 : 0]; const dt = new Date(p.t); g.textAlign = i ? 'right' : 'left'; g.fillText(String(dt.getDate()).padStart(2, '0') + '.' + String(dt.getMonth() + 1).padStart(2, '0'), i ? W - R : L, H - 6); });
  if (cfg.line) {
    g.setLineDash([5, 4]); g.strokeStyle = col('--ink-3'); g.beginPath(); g.moveTo(L, Y(cfg.line)); g.lineTo(W - R, Y(cfg.line)); g.stroke(); g.setLineDash([]);
  }
  const acc = col('--accent');
  const grad = g.createLinearGradient(0, T, 0, H - B);
  grad.addColorStop(0, acc + '33'); grad.addColorStop(1, acc + '00');
  g.beginPath(); pts.forEach((p, i) => { i ? g.lineTo(X(p.t), Y(p.y)) : g.moveTo(X(p.t), Y(p.y)); });
  g.strokeStyle = acc; g.lineWidth = 2.5; g.lineJoin = 'round'; g.stroke();
  g.lineTo(X(pts[pts.length - 1].t), H - B); g.lineTo(X(pts[0].t), H - B); g.closePath(); g.fillStyle = grad; g.fill();
  const last = pts[pts.length - 1];
  g.beginPath(); g.arc(X(last.t), Y(last.y), 4.5, 0, 7); g.fillStyle = col('--surface'); g.fill(); g.lineWidth = 2.5; g.strokeStyle = acc; g.stroke();
}
function drawCharts() {
  Object.keys(CHARTS).forEach(id => { const el = document.getElementById(id); if (el && CHARTS[id]) drawLine(el, CHARTS[id]); });
}
window.addEventListener('resize', () => requestAnimationFrame(drawCharts));

/* ================= Профиль ================= */
function renderProfile() {
  const d = S.d, s = d.settings;
  const goalTxt = GOALS[s.goal] + (s.goal !== 'maintain' ? ', ' + String(s.goal_rate).replace('.', ',') + ' кг в неделю' : '');
  const initial = esc((userName || 'Г').slice(0, 1).toUpperCase());
  let h = '<div class="me"><div class="avatar">' + initial + (tgUser && tgUser.photo_url ? '<img src="' + esc(tgUser.photo_url) + '" alt="" onerror="this.remove()">' : '') + '</div><div><div class="nm">' + esc(userName) + '</div><div class="sub">' + esc(goalTxt) + '</div></div></div>' +
    '<div class="tiles" style="margin-top:14px"><div class="tile"><div class="n">Дневная норма</div><div class="v">' + n0(s.target_cal) + ' <small>ккал</small></div></div><div class="tile"><div class="n">Вес / цель</div><div class="v">' + n1(latestWeight()) + ' <small>/ ' + n1(s.target_weight) + ' кг</small></div></div></div>';
  if (d.is_admin) h += '<div class="h2">Владелец</div><div class="group">' + prow('stats', 'chart', 'Статистика', 'Пользователи, визиты, активность') + '</div>';
  h += '<div class="h2">Настройки</div><div class="group">' +
    prow('goals', 'target', 'Цели и параметры', 'Рост, вес, активность, расчёт нормы') +
    prow('prefs', 'sliders', 'Оформление и приложение', 'Тема, стакан воды') + '</div>' +
    '<div class="h2">Тема</div><div class="group pad"><div class="seg">' + THEMES.map(t => '<button class="' + (s.theme === t.v ? 'on' : '') + '" data-act="theme" data-v="' + t.v + '">' + (t.s || t.t) + '</button>').join('') + '</div></div>' +
    '<div class="h2">Ещё</div><div class="group">' +
    prow('feedback', 'message', 'Предложить улучшение', 'Идея или сообщение об ошибке') +
    prow('data', 'shield', 'Мои данные', 'Копия, перенос и удаление') +
    '<button class="row" data-act="policy"><span class="ico-box">' + ic('list') + '</span><div class="grow"><div class="ttl">Политика конфиденциальности</div><div class="sub">Что хранится и где</div></div>' + ic('chev-r', 'chev') + '</button>' +
    '<button class="row" data-act="redo-wizard"><span class="ico-box">' + ic('refresh') + '</span><div class="grow"><div class="ttl">Пройти настройку заново</div><div class="sub">Пересчитать норму шаг за шагом</div></div>' + ic('chev-r', 'chev') + '</button></div>' +
    '<p class="note" style="text-align:center;margin-top:18px">Grammika · версия ' + esc(d.version) + '</p>';
  return h;
}
function prow(p, icon, t, s) {
  return '<button class="row" data-act="page" data-p="' + p + '"><span class="ico-box">' + ic(icon) + '</span><div class="grow"><div class="ttl">' + t + '</div><div class="sub">' + s + '</div></div>' + ic('chev-r', 'chev') + '</button>';
}
const pageHead = t => '<div class="pagehead"><button class="back" data-act="back" aria-label="Назад">' + ic('arrow-left') + '</button><h1>' + t + '</h1></div>';
const GF_KEYS = ['sex', 'age', 'height', 'weight', 'target_weight', 'activity', 'goal', 'goal_rate', 'target_cal', 'target_p', 'target_f', 'target_c', 'target_water', 'target_steps'];
function gfInit() {
  const s = S.d.settings; S.gf = {};
  GF_KEYS.forEach(k => { S.gf[k] = s[k]; });
  S.gf.weight = latestWeight();
}
function renderGoals() {
  if (!S.gf) gfInit();
  const g = S.gf;
  const fld = (k, l, ph, mode) => '<label class="field"><span>' + l + '</span><input data-in="gf.' + k + '" inputmode="' + (mode || 'decimal') + '" value="' + esc(g[k]) + '" placeholder="' + (ph || '') + '"></label>';
  let h = pageHead('Цели и параметры') + '<div class="group pad"><div class="stack">' +
    '<div class="field"><span>Пол</span><div class="seg">' + [['male', 'Мужской'], ['female', 'Женский']].map(t => '<button class="' + (g.sex === t[0] ? 'on' : '') + '" data-act="gf-set" data-k="sex" data-v="' + t[0] + '">' + t[1] + '</button>').join('') + '</div></div>' +
    '<div class="cols3">' + fld('age', 'Возраст', '', 'numeric') + fld('height', 'Рост, см', '', 'numeric') + fld('weight', 'Вес, кг') + '</div>' +
    '<div class="field"><span>Цель</span><div class="seg">' + Object.keys(GOALS).map(k => '<button class="' + (g.goal === k ? 'on' : '') + '" data-act="gf-set" data-k="goal" data-v="' + k + '">' + GOALS[k].split(' ')[0] + '</button>').join('') + '</div></div>' +
    '<div class="cols2">' + fld('target_weight', 'Желаемый вес, кг') +
    '<label class="field"><span>Темп, кг в неделю</span><select data-ch="gf.goal_rate">' + [0.25, 0.5, 0.75, 1].map(v => '<option value="' + v + '"' + (+g.goal_rate === v ? ' selected' : '') + '>' + String(v).replace('.', ',') + '</option>').join('') + '</select></label></div></div></div>' +
    '<div class="h2">Активность</div><div class="group">' + ACTIVITY.map(a => '<button class="row" data-act="gf-set" data-k="activity" data-v="' + a.v + '"><div class="grow"><div class="ttl">' + a.t + '</div><div class="sub">' + a.s + '</div></div><span class="radio' + (+g.activity === a.v ? ' on' : '') + '"></span></button>').join('') + '</div>' +
    '<div id="gfPreview"></div>' +
    '<div class="h2">Норма на день</div><div class="group pad"><div class="stack"><div class="cols2">' + fld('target_cal', 'Калории, ккал', '', 'numeric') + fld('target_water', 'Вода, мл', '', 'numeric') + '</div>' +
    '<div class="cols3">' + fld('target_p', 'Белки, г', '', 'numeric') + fld('target_f', 'Жиры, г', '', 'numeric') + fld('target_c', 'Углеводы, г', '', 'numeric') + '</div>' + fld('target_steps', 'Шагов в день', '', 'numeric') + '</div></div>' +
    '<div class="stack" style="margin-top:14px"><button class="btn soft" data-act="gf-apply">' + ic('refresh') + 'Подставить расчётную норму</button><button class="btn" data-act="gf-save">Сохранить</button></div>';
  return h;
}
function gfPreviewHtml() {
  const g = S.gf, p = calcPlan(g);
  return '<div class="h2">Расчёт</div><div class="result"><div class="sub" style="color:var(--ink-2)">Рекомендуемая норма</div><div class="big">' + n0(p.kcal) + ' <small style="font-size:16px">ккал</small></div>' +
    '<div class="sub" style="color:var(--ink-2);margin-top:4px">Б ' + p.prot + ' · Ж ' + p.fat + ' · У ' + p.carbs + ' г · вода ' + n1(p.water / 1000) + ' л</div></div>' +
    '<p class="note">Базовый обмен ' + n0(p.bmr) + ' ккал, с учётом активности ' + n0(p.tdee) + ' ккал (формула Миффлина — Сан-Жеора).' + (p.floored ? ' Норма поднята до безопасного минимума, дефицит с такими параметрами лучше не увеличивать.' : '') + ' Это ориентир, а не медицинская рекомендация.</p>';
}
function updGfPreview() { const el = $('#gfPreview'); if (el && S.gf) el.innerHTML = gfPreviewHtml(); }
function renderPrefs() {
  const s = S.d.settings;
  return pageHead('Оформление') + '<div class="group">' + THEMES.map(t => '<button class="row" data-act="theme" data-v="' + t.v + '"><span class="ico-box">' + ic(t.i) + '</span><div class="grow ttl">' + t.t + '</div><span class="radio' + (s.theme === t.v ? ' on' : '') + '"></span></button>').join('') + '</div>' +
    '<p class="note">Вариант «Как в Telegram» повторяет тему вашего мессенджера.</p>' +
    '<div class="h2">Стакан воды</div><div class="group pad"><div class="chips">' + [150, 200, 250, 300, 400, 500].map(v => '<button class="chip' + (s.water_glass === v ? ' on' : '') + '" data-act="glass" data-v="' + v + '">' + v + ' мл</button>').join('') + '</div></div>';
}
function renderFeedback() {
  const f = S.fb;
  return pageHead('Предложить улучшение') + '<div class="group pad"><div class="stack"><p class="mut" style="margin:0;font-size:14px">Расскажите, чего не хватает или что работает неудобно. Каждое сообщение читает разработчик.</p>' +
    '<div class="seg">' + [['idea', 'Идея'], ['bug', 'Ошибка'], ['other', 'Другое']].map(t => '<button class="' + (f.kind === t[0] ? 'on' : '') + '" data-act="fb-kind" data-v="' + t[0] + '">' + t[1] + '</button>').join('') + '</div>' +
    '<textarea data-in="fb.text" maxlength="1500" placeholder="Что хотите улучшить?">' + esc(f.text) + '</textarea>' +
    '<button class="btn" data-act="fb-send">Отправить</button></div></div><p class="note">Не пишите в сообщении пароли и другие личные данные.</p>';
}
function renderData() {
  return pageHead('Мои данные') + '<div class="group">' +
    '<button class="row" data-act="export"><span class="ico-box">' + ic('download') + '</span><div class="grow"><div class="ttl">Скачать копию данных</div><div class="sub">Файл JSON со всеми записями</div></div></button>' +
    '<label class="row" style="cursor:pointer"><span class="ico-box">' + ic('upload') + '</span><div class="grow"><div class="ttl">Восстановить из копии</div><div class="sub">Заменит текущие данные</div></div><input type="file" accept=".json,application/json" data-ch="import" class="vh"></label></div>' +
    '<p class="note">Данные хранятся на сервере приложения и привязаны к вашему аккаунту Telegram. Другим пользователям они не видны.</p>' +
    '<div class="h2">Удаление</div><div class="group"><button class="row" data-act="wipe"><span class="ico-box" style="background:var(--danger-soft);color:var(--danger)">' + ic('trash') + '</span><div class="grow"><div class="ttl bad">Удалить все данные</div><div class="sub">Записи, цели и рецепты. Действие необратимо</div></div></button></div>';
}

/* ================= Знакомство ================= */
function wizStart() {
  const s = S.d.settings;
  S.wiz = { step: 0, p: { sex: s.sex, age: s.age, height: s.height, weight: s.weight, target_weight: s.target_weight, activity: s.activity, goal: s.goal, goal_rate: s.goal_rate } };
  $('#wizard').classList.add('open'); document.body.classList.add('noscroll');
  renderWizard();
}
function wizClose() { S.wiz = null; $('#wizard').classList.remove('open'); document.body.classList.remove('noscroll'); $('#wizard').innerHTML = ''; }
function renderWizard() {
  const w = S.wiz; if (!w) return;
  const p = w.p, N = 5;
  const fld = (k, l, mode) => '<label class="field"><span>' + l + '</span><input data-in="wiz.p.' + k + '" inputmode="' + (mode || 'decimal') + '" value="' + esc(p[k]) + '"></label>';
  let body = '';
  if (w.step === 0) body = '<h1>Давайте настроим Grammika под вас</h1><p class="lead">Ответьте на несколько вопросов, и мы рассчитаем дневную норму калорий, белков, жиров и углеводов.</p>' +
    ['male', 'female'].map(v => '<button class="opt' + (p.sex === v ? ' on' : '') + '" data-act="wz-set" data-k="sex" data-v="' + v + '"><div class="ttl">' + (v === 'male' ? 'Мужской' : 'Женский') + '</div></button>').join('') + '<p class="note">Пол нужен только для формулы расчёта обмена веществ.</p>';
  else if (w.step === 1) body = '<h1>Ваши параметры</h1><p class="lead">Возраст, рост и текущий вес.</p><div class="stack">' + fld('age', 'Возраст', 'numeric') + fld('height', 'Рост, см', 'numeric') + fld('weight', 'Вес, кг') + '</div>';
  else if (w.step === 2) body = '<h1>Какая у вас цель?</h1><p class="lead">От неё зависит, будет ли в норме дефицит или профицит калорий.</p>' +
    Object.keys(GOALS).map(k => '<button class="opt' + (p.goal === k ? ' on' : '') + '" data-act="wz-set" data-k="goal" data-v="' + k + '"><div class="ttl">' + GOALS[k] + '</div></button>').join('') +
    (p.goal !== 'maintain' ? '<div class="cols2" style="margin-top:6px">' + fld('target_weight', 'Желаемый вес, кг') + '<label class="field"><span>Темп, кг в неделю</span><select data-ch="wiz.p.goal_rate">' + [0.25, 0.5, 0.75, 1].map(v => '<option value="' + v + '"' + (+p.goal_rate === v ? ' selected' : '') + '>' + String(v).replace('.', ',') + '</option>').join('') + '</select></label></div><p class="note">Комфортный темп для снижения веса — 0,5 кг в неделю.</p>' : '');
  else if (w.step === 3) body = '<h1>Насколько вы активны?</h1><p class="lead">Учитываем обычный день без отдельных тренировок и сами тренировки.</p>' + ACTIVITY.map(a => '<button class="opt' + (+p.activity === a.v ? ' on' : '') + '" data-act="wz-set" data-k="activity" data-v="' + a.v + '"><div><div class="ttl">' + a.t + '</div><div class="sub">' + a.s + '</div></div></button>').join('');
  else { const r = calcPlan(p); w.plan = r; body = '<h1>Ваша норма готова</h1><p class="lead">Изменить её можно в любой момент в профиле.</p><div class="result"><div class="sub" style="color:var(--ink-2)">Калории в день</div><div class="big">' + n0(r.kcal) + ' ккал</div>' +
    '<div class="sub" style="color:var(--ink-2);margin-top:6px">Белки ' + r.prot + ' г · Жиры ' + r.fat + ' г · Углеводы ' + r.carbs + ' г<br>Вода ' + n1(r.water / 1000) + ' л</div></div><p class="note">Расчёт по формуле Миффлина — Сан-Жеора. Это ориентир, а не медицинская рекомендация.' + (r.floored ? ' Норма поднята до безопасного минимума.' : '') + '</p>'; }
  $('#wizard').innerHTML = '<div class="wrap"><div class="wz-prog">' + Array.from({ length: N }, (_, i) => '<i class="' + (i <= w.step ? 'on' : '') + '"></i>').join('') + '</div>' + body +
    '<div class="wz-foot stack">' + (w.step < N - 1 ? '<button class="btn" data-act="wz-next">Дальше</button>' : '<button class="btn" data-act="wz-done">Начать</button>') +
    (w.step > 0 ? '<button class="btn sec" data-act="wz-back">Назад</button>' : '<button class="btn link" style="margin:0 auto;display:flex" data-act="wz-skip">Пропустить</button>') + '</div></div>';
}
function wizValid() {
  const w = S.wiz, p = w.p;
  if (w.step === 1) {
    if (!(num(p.age) >= 14 && num(p.age) <= 100)) return 'Укажите возраст от 14 до 100 лет';
    if (!(num(p.height) >= 100 && num(p.height) <= 250)) return 'Укажите рост от 100 до 250 см';
    if (!(num(p.weight) >= 30 && num(p.weight) <= 300)) return 'Укажите вес от 30 до 300 кг';
  }
  if (w.step === 2 && p.goal !== 'maintain' && !(num(p.target_weight) >= 30 && num(p.target_weight) <= 300)) return 'Укажите желаемый вес от 30 до 300 кг';
  return '';
}

/* ================= Согласие и политика ================= */
function policyHtml() {
  const c = S.d && S.d.contact;
  const contact = c ? 'Связаться с оператором: ' + esc(c) + '. Также можно написать через раздел «Профиль → Предложить улучшение».' : 'Связаться с оператором можно через раздел «Профиль → Предложить улучшение».';
  const sec = (t, p) => '<h4 style="margin:16px 0 4px;font-size:15px">' + t + '</h4><p class="mut" style="margin:0;font-size:14px;line-height:1.5">' + p + '</p>';
  return sec('Какие данные мы храним', 'Ваш идентификатор в Telegram, пол, возраст, рост и вес, цели, записи о питании, воде, тренировках и замерах, ваши рецепты и сообщения, которые вы отправляете через форму обратной связи. Имя, фото и номер телефона мы не сохраняем.') +
    sec('Зачем', 'Только чтобы приложение работало: вело ваш дневник, считало норму калорий и показывало прогресс. Данные не используются для рекламы и не продаются.') +
    sec('Где хранятся', 'На серверах сервисов Render (приложение) и Neon (база данных). Эти серверы находятся за пределами России. Доступ к данным есть у оператора приложения и у этих сервисов в объёме, необходимом для хостинга.') +
    sec('Кому передаются', 'Третьим лицам данные не передаются, кроме случаев, когда это требует закон.') +
    sec('Как удалить', 'В любой момент: «Профиль → Мои данные → Удалить все данные». Записи будут стёрты без возможности восстановления. Там же можно скачать копию своих данных.') +
    sec('Согласие', 'Данные о весе, росте и питании относятся к сведениям о здоровье. Вы даёте согласие на их обработку, нажав «Принимаю» при первом запуске, и можете отозвать его, удалив данные. Приложение не ставит диагнозов и не заменяет консультацию врача.') +
    sec('Источники данных о продуктах', 'Меню ресторанов взято с официальных сайтов сетей. Часть данных о магазинных продуктах — из открытой базы Open Food Facts (openfoodfacts.org, лицензия ODbL). При поиске продукта на сервер Open Food Facts отправляется только текст запроса, без данных о вас.') +
    sec('Контакты', contact) + '<p class="note">Редакция от 10 ноября 2026 года.</p>';
}
function showConsent() {
  S.consentOn = true; S.consentCk = false;
  const w = $('#wizard'); w.classList.add('open'); document.body.classList.add('noscroll');
  renderConsent();
}
function renderConsent() {
  $('#wizard').innerHTML = '<div class="wrap"><h1 style="margin-top:34px">Прежде чем начать</h1><p class="lead">Grammika хранит ваш вес, рост, питание и тренировки, чтобы вести дневник и считать норму. Данные видны только вам.</p>' +
    '<div class="group"><button class="row" data-act="policy"><span class="ico-box">' + ic('shield') + '</span><div class="grow"><div class="ttl">Политика конфиденциальности</div><div class="sub">Что хранится, где и как удалить</div></div>' + ic('chev-r', 'chev') + '</button>' +
    '<button class="row shop-row' + (S.consentCk ? ' done' : '') + '" data-act="consent-ck" role="checkbox" aria-checked="' + !!S.consentCk + '"><span class="ck">' + ic('check') + '</span><div class="grow" style="font-size:14px">Я согласен на обработку моих данных, в том числе о здоровье, на условиях политики</div></button></div>' +
    '<div class="wz-foot"><button class="btn" data-act="consent-ok"' + (S.consentCk ? '' : ' disabled') + '>Принимаю и продолжаю</button></div></div>';
}

/* ================= Каркас и события ================= */
Object.assign(S, { mf: { name: '', cal: '', g: '100', p: '', f: '', c: '' }, exMetric: 'e1rm', timerLen: 90, timerEnd: 0, timerId: null });

const TABS = [['diary', 'Дневник', 'book'], ['plan', 'Меню', 'utensils'], ['work', 'Тренировки', 'dumbbell'], ['progress', 'Прогресс', 'trend'], ['me', 'Профиль', 'user']];
const TITLES = { work: 'Тренировки', progress: 'Прогресс', me: 'Профиль' };

function renderTop() {
  const d = S.d, initial = esc((userName || 'Г').slice(0, 1).toUpperCase());
  let mid;
  if (!S.page && (S.tab === 'diary' || S.tab === 'plan')) {
    mid = '<div class="datenav"><button class="nav" data-act="date-prev" aria-label="Предыдущий день">' + ic('chev-l') + '</button>' +
      '<label class="datelabel">' + esc(dateLabel(S.date)) + '<input type="date" data-ch="dateSel" value="' + S.date + '" aria-label="Выбрать дату"></label>' +
      '<button class="nav" data-act="date-next" aria-label="Следующий день">' + ic('chev-r') + '</button></div>';
  } else mid = '<div class="top-title">' + (S.page ? '' : (TITLES[S.tab] || '')) + '</div>';
  $('#top').innerHTML = '<div class="wrap"><div class="streak' + (d.streak > 0 ? ' live' : '') + '" title="Дней подряд с записями">' + ic('flame') + d.streak + '</div>' + mid +
    '<button class="avatar" data-act="tab" data-t="me" aria-label="Профиль">' + initial + (tgUser && tgUser.photo_url ? '<img src="' + esc(tgUser.photo_url) + '" alt="" onerror="this.remove()">' : '') + '</button></div>';
}
function renderTabbar() {
  $('#tabbar').innerHTML = '<div class="wrap">' + TABS.map(t => '<button class="' + (S.tab === t[0] ? 'on' : '') + '" data-act="tab" data-t="' + t[0] + '" aria-label="' + t[1] + '">' + ic(t[2]) + '<span>' + t[1] + '</span></button>').join('') + '</div>';
}
function render() {
  const v = $('#view');
  if (S.loading) { v.innerHTML = '<div class="loading">Загружаем данные…</div>'; return; }
  if (!S.d) {
    $('#top').innerHTML = ''; $('#tabbar').innerHTML = '';
    v.innerHTML = '<div class="loading">' + esc(S.error || 'Не удалось загрузить данные') + '<div style="margin-top:16px"><button class="btn" data-act="retry">Повторить</button></div></div>';
    return;
  }
  applyTheme(S.d.settings.theme);
  renderTop(); renderTabbar();
  let h;
  if (S.page === 'goals') h = renderGoals();
  else if (S.page === 'prefs') h = renderPrefs();
  else if (S.page === 'feedback') h = renderFeedback();
  else if (S.page === 'data') h = renderData();
  else if (S.page === 'stats') h = renderStats();
  else if (S.tab === 'plan') h = renderPlan();
  else if (S.tab === 'work') h = renderWork();
  else if (S.tab === 'progress') h = renderProgress();
  else if (S.tab === 'me') h = renderProfile();
  else h = renderDiary();
  v.innerHTML = '<div class="wrap">' + h + '</div>';
  if (S.page === 'goals') updGfPreview();
  if (S.tab === 'diary' || S.tab === 'plan') { /* график не нужен */ } else requestAnimationFrame(drawCharts);
  if (S.tab === 'diary' && !S.page) { loadPhotos(); animateDiary(); }
  if (S.d.settings.consent !== '1') { if (!S.consentOn) showConsent(); }
  else if (S.d.settings.onboarded !== '1' && !S.wiz) wizStart();
}

function setPath(path, val) {
  const parts = path.split('.'); let o = S;
  for (let i = 0; i < parts.length - 1; i++) o = o[parts[i]];
  o[parts[parts.length - 1]] = val;
}
function onInput(path) {
  if (path === 'picker.q') { searchRemote(); refreshPicker(); }
  else if (path === 'picker.g') updateAmount();
  else if (path === 'edit.g') updateEdit();
  else if (path.startsWith('ai.items.')) { const i = +path.split('.')[2], x = S.ai.items[i], el = $('#aiK' + i); if (el) el.textContent = n0(x.cal * num(x.g) / 100) + ' ккал'; }
  else if (path.startsWith('gf.')) updGfPreview();
}

/* Сохраняет настройки и обновляет локальное состояние. */
async function saveSettings(obj, okMsg) {
  try { await post('/api/save_goals', obj); } catch (e) { toast(e.message, true); return false; }
  Object.assign(S.d.settings, obj);
  if (okMsg) toast(okMsg);
  return true;
}
const withNums = (o, keys) => { const r = {}; keys.forEach(k => { r[k] = (k === 'sex' || k === 'goal') ? o[k] : num(o[k]); }); return r; };
function checkProfile(p) {
  if (!(num(p.age) >= 14 && num(p.age) <= 100)) return 'Возраст: от 14 до 100 лет';
  if (!(num(p.height) >= 100 && num(p.height) <= 250)) return 'Рост: от 100 до 250 см';
  if (!(num(p.weight) >= 20 && num(p.weight) <= 400)) return 'Вес: от 20 до 400 кг';
  if (!(num(p.target_weight) >= 20 && num(p.target_weight) <= 400)) return 'Желаемый вес: от 20 до 400 кг';
  return '';
}
function timerReset() {
  clearInterval(S.timerId); S.timerId = null; S.timerEnd = 0;
  if ($('#sheetBody')) { setSheet('Отдых между подходами', timerHtml()); $('#timerT').textContent = fmtT(S.timerLen); }
}

async function act(a, t, ev) {
  const D = t.dataset;
  switch (a) {
    case 'retry': S.loading = true; render(); load(); break;
    case 'close-sheet': closeSheet(); break;
    case 'confirm-no': closeSheet(); break;
    case 'confirm-yes': { const r = confirmResolve; confirmResolve = null; closeSheet(); if (r) r(true); break; }
    case 'tab': S.tab = D.t; S.page = null; S.gf = null; render(); window.scrollTo(0, 0); break;
    case 'page': S.page = D.p; if (D.p === 'goals') gfInit(); if (D.p === 'stats') { loadStats(); window.scrollTo(0, 0); break; } render(); window.scrollTo(0, 0); break;
    case 'back': S.page = null; S.gf = null; render(); window.scrollTo(0, 0); break;
    case 'date-prev': S.date = addDays(S.date, -1); load(); break;
    case 'date-next': if (S.date < addDays(localDate(), 7)) { S.date = addDays(S.date, 1); load(); } break;

    /* дневник */
    case 'add-food': openPicker(D.m); break;
    case 'edit-meal': openEdit(+D.id); break;
    case 'pk-tab': S.picker.tab = D.t; refreshPicker(); if (D.t === 'base') loadCafe(); break;
    case 'pk-chain': S.picker.chain = D.c; refreshPicker(); break;
    case 'pk-cat': S.picker.cat = D.c; S.picker.chain = ''; refreshPicker(); if (D.c === 'cafe' || D.c === 'all') loadCafe(); break;
    case 'pick': {
      const it = pickerSource()[+D.i]; if (!it) break;
      if (S.picker.replace) {
        const id = S.picker.replace; closeSheet();
        run(() => post('/api/replace_meal', { id, new_name: it.full || it.name, cal100: +it.cal.toFixed(1), p100: +it.p.toFixed(1), f100: +it.f.toFixed(1), c100: +it.c.toFixed(1) }), 'Продукт заменён');
        break;
      }
      S.picker.sel = it; S.picker.g = it.g || 100;
      setSheet(mealOf(S.picker.meal).n, amountHtml()); updateAmount(); break;
    }
    case 'pk-step': { const i = $('#pkg'); S.picker.g = String(clamp(Math.round(num(S.picker.g) + +D.v), 1, 5000)); i.value = S.picker.g; updateAmount(); break; }
    case 'pk-set': { S.picker.g = D.v; $('#pkg').value = D.v; $$('[data-act="pk-set"]').forEach(b => b.classList.toggle('on', b === t)); updateAmount(); break; }
    case 'pk-back': S.picker.sel = null; S.picker.manual = false; setSheet(S.picker.replace ? 'Заменить продукт' : mealOf(S.picker.meal).n, pickerHtml()); break;
    case 'pk-add': {
      const g = num(S.picker.g); if (!(g > 0 && g <= 5000)) { toast('Укажите вес от 1 до 5000 г', true); break; }
      const sid = sheetSeq; if (await addPicked(S.picker.sel, g)) closeIf(sid); break;
    }
    case 'manual': S.picker.manual = true; setSheet('Свой продукт', manualHtml()); break;
    case 'mf-add': case 'mf-save': {
      const f = S.mf, name = f.name.trim(), g = num(f.g);
      if (!name) { toast('Введите название', true); break; }
      if (!f.cal.trim() || num(f.cal) < 0 || num(f.cal) > 2000) { toast('Укажите калорийность на 100 г', true); break; }
      if (!(g > 0 && g <= 5000)) { toast('Укажите вес порции', true); break; }
      const it = { name, cal: num(f.cal), p: num(f.p), f: num(f.f), c: num(f.c) };
      if (a === 'mf-save') { try { await post('/api/add_custom_food', Object.assign({ meal_type: 'none' }, it)); } catch (e) { toast(e.message, true); break; } }
      const sid = sheetSeq; if (await addPicked(it, g)) { S.mf = { name: '', cal: '', g: '100', p: '', f: '', c: '' }; closeIf(sid); }
      break;
    }
    case 'ed-step': { S.edit.g = String(clamp(Math.round(num(S.edit.g) + +D.v), 1, 5000)); $('#edg').value = S.edit.g; updateEdit(); break; }
    case 'ed-type': { S.edit.type = D.m; $$('[data-act="ed-type"]').forEach(b => b.classList.toggle('on', b.dataset.m === D.m)); break; }
    case 'ed-save': {
      const e = S.edit, g = num(e.g); if (!(g >= 1 && g <= 5000)) { toast('Укажите вес от 1 до 5000 г', true); break; }
      const body = { id: e.id, meal_type: e.type }; if (Math.round(g) !== e.g0) body.grams = g;
      closeSheet(); run(() => post('/api/update_meal', body), 'Сохранено'); break;
    }
    case 'ed-del': { const id = S.edit.id; closeSheet(); run(() => post('/api/delete_meal', { id }), 'Удалено'); break; }
    case 'photo-del': {
      const id = S.edit.id; closeSheet();
      try { await post('/api/meal_photo_delete', { id }); } catch (e) { toast(e.message, true); break; }
      delete PHOTO[id]; const m = S.d.day_meals.find(x => x.id === id); if (m) m.photo = false; render(); toast('Фото убрано'); break;
    }
    case 'ai-toggle': { const x = S.ai.items[+D.i]; x.on = !x.on; t.classList.toggle('on', x.on); break; }
    case 'ai-add': {
      const items = S.ai.items.filter(x => x.on && num(x.g) > 0);
      if (!items.length) { toast('Отметьте хотя бы одно блюдо', true); break; }
      const sid = sheetSeq; let ok = 0;
      for (const x of items) {
        const k = num(x.g) / 100;
        try { await post('/api/add_meal', { date: S.date, meal_type: S.picker.meal, name: x.name, grams: num(x.g), cal: +(x.cal * k).toFixed(1), p: +(x.p * k).toFixed(1), f: +(x.f * k).toFixed(1), c: +(x.c * k).toFixed(1) }); ok++; }
        catch (e) { toast(e.message, true); break; }
      }
      if (ok) { closeIf(sid); await load(); toast('Добавлено блюд: ' + ok); }
      break;
    }
    case 'ed-replace': { const e = S.edit; S.edit = null; openPicker(e.type, e.id); break; }
    case 'copy-prev': {
      if (S.d.day_meals.length && !await askConfirm('Добавить блюда?', 'В этом дне уже есть записи. Блюда предыдущего дня добавятся к ним.', 'Добавить')) break;
      try {
        const r = await post('/api/copy_day', { from_date: addDays(S.date, -1), to_date: S.date });
        toast(r.copied ? 'Перенесено записей: ' + r.copied : 'В предыдущем дне нет записей', !r.copied);
      } catch (e) { toast(e.message, true); }
      load(); break;
    }
    case 'gen-day': {
      if (S.d.day_meals.length && !await askConfirm('Заменить день меню?', 'Текущие записи за этот день будут удалены, вместо них появится собранное меню.', 'Заменить', true)) break;
      run(() => post('/api/generate_day', { date: S.date }), 'Меню собрано'); break;
    }
    case 'clear-day': {
      if (!S.d.day_meals.length) { toast('День уже пуст'); break; }
      if (await askConfirm('Очистить день?', 'Все приёмы пищи за этот день будут удалены.', 'Очистить', true)) run(() => post('/api/clear_day_meals', { date: S.date }), 'День очищен'); break;
    }
    case 'water': run(() => post('/api/add_water', { date: S.date, delta: +D.v })); break;

    /* меню */
    case 'excl': {
      const ex = (S.d.settings.excluded || []).slice(), i = ex.indexOf(D.k);
      i >= 0 ? ex.splice(i, 1) : ex.push(D.k);
      S.d.settings.excluded = ex; render(); saveSettings({ excluded: ex }); break;
    }
    case 'shop': S.shop.has(D.k) ? S.shop.delete(D.k) : S.shop.add(D.k); render(); break;
    case 'new-recipe': openSheet('Новый рецепт', recipeHtml()); break;
    case 'rf-save': {
      const f = S.rf, name = f.name.trim();
      if (!name) { toast('Введите название', true); break; }
      if (!f.cal.trim() || num(f.cal) < 0 || num(f.cal) > 2000) { toast('Укажите калорийность на 100 г', true); break; }
      const sid = sheetSeq;
      const ok = await run(() => post('/api/add_custom_food', { name, cal: num(f.cal), p: num(f.p), f: num(f.f), c: num(f.c), meal_type: f.meal_type }), 'Рецепт сохранён');
      if (ok) { S.rf = { name: '', cal: '', p: '', f: '', c: '', meal_type: 'none' }; closeIf(sid); }
      break;
    }
    case 'del-recipe': if (await askConfirm('Удалить рецепт?', 'Рецепт исчезнет из списка и автоменю. Записи в дневнике останутся.', 'Удалить', true)) run(() => post('/api/delete_custom_food', { id: +D.id }), 'Рецепт удалён'); break;

    /* тренировки */
    case 'work-tab': S.workTab = D.t; render(); break;
    case 'programs': openSheet('Программа тренировок', programsHtml()); break;
    case 'set-program': { const sid = sheetSeq; const ok = await saveSettings({ program: D.id, program_day: 0 }); if (ok) { closeIf(sid); render(); } break; }
    case 'next-day': { const pi = programInfo(); if (pi && await saveSettings({ program_day: (pi.di + 1) % pi.p.days.length })) render(); break; }
    case 'pick-ex': {
      S.form.ex = D.ex; const l = S.d.last_sets[D.ex];
      S.form.w = l ? String(l.weight) : ''; S.form.r = l ? String(l.reps) : '';
      render(); const f = $('select[data-ch="form.ex"]'); if (f) f.scrollIntoView({ behavior: 'smooth', block: 'center' }); break;
    }
    case 'add-set': {
      const f = S.form, reps = Math.round(num(f.r)), w = num(f.w);
      if (!f.ex) { toast('Выберите упражнение', true); break; }
      if (!(reps >= 1)) { toast('Укажите число повторов', true); break; }
      if (w < 0) { toast('Вес не может быть отрицательным', true); break; }
      try {
        const r = await post('/api/add_set', { date: S.date, exercise: f.ex, weight: w, reps });
        toast(r.pr ? 'Новый рекорд по весу!' : 'Подход записан');
      } catch (e) { toast(e.message, true); break; }
      S.exHist = null; load(); break;
    }
    case 'del-set': S.exHist = null; run(() => post('/api/delete_set', { id: +D.id })); break;
    case 'timer': openSheet('Отдых между подходами', timerHtml()); $('#timerT').textContent = fmtT(S.timerLen); break;
    case 'timer-len': S.timerLen = +D.v; timerReset(); break;
    case 'timer-go': {
      clearInterval(S.timerId); S.timerEnd = Date.now() + S.timerLen * 1000; $('#timerT').classList.remove('done');
      timerTick(); S.timerId = setInterval(timerTick, 250); break;
    }
    case 'timer-stop': timerReset(); break;
    case 'add-cardio': {
      const c = S.cardio, sel = c.idx === '' ? null : S.d.workouts[+c.idx], mins = num(c.mins);
      const name = sel ? sel.name : c.name.trim();
      if (!name) { toast('Введите название активности', true); break; }
      if (!(mins > 0 && mins <= 1440)) { toast('Укажите длительность в минутах', true); break; }
      const cal = c.cal.trim() ? num(c.cal) : (sel ? Math.round(sel.met * latestWeight() * mins / 60) : 0);
      if (!(cal > 0)) { toast('Укажите, сколько калорий сожжено', true); break; }
      const ok = await run(() => post('/api/add_workout', { date: S.date, name, mins, cal }), 'Тренировка записана');
      if (ok) { S.cardio.cal = ''; S.cardio.name = ''; render(); }
      break;
    }
    case 'del-workout': run(() => post('/api/delete_workout', { id: +D.id })); break;

    /* прогресс */
    case 'range': S.range = +D.v; render(); break;
    case 'metric': S.exMetric = D.v; render(); break;
    case 'add-entry': {
      const w = num(S.pg.w), s = Math.round(num(S.pg.s));
      if (!(w > 0) && !(s > 0)) { toast('Укажите вес или шаги', true); break; }
      if (w && (w < 20 || w > 400)) { toast('Вес: от 20 до 400 кг', true); break; }
      const ok = await run(() => post('/api/add_entry', { date: S.pg.date || localDate(), weight: w, steps: s, note: S.pg.note }), 'Замер сохранён');
      if (ok) { S.pg.w = ''; S.pg.s = ''; S.pg.note = ''; render(); }
      break;
    }
    case 'del-entry': if (await askConfirm('Удалить замер?', 'Вес, шаги и заметка за ' + ddmm(D.d) + ' будут удалены. Записи о питании останутся.', 'Удалить', true)) run(() => post('/api/delete_entry', { date: D.d }), 'Замер удалён'); break;

    /* профиль */
    case 'theme': S.d.settings.theme = D.v; applyTheme(D.v); render(); saveSettings({ theme: D.v }); break;
    case 'glass': if (await saveSettings({ water_glass: +D.v })) render(); break;
    case 'gf-set': S.gf[D.k] = (D.k === 'activity') ? +D.v : D.v; render(); break;
    case 'gf-apply': {
      const err = checkProfile(S.gf); if (err) { toast(err, true); break; }
      const p = calcPlan(S.gf); Object.assign(S.gf, { target_cal: p.kcal, target_p: p.prot, target_f: p.fat, target_c: p.carbs, target_water: p.water }); render(); toast('Норма подставлена, не забудьте сохранить'); break;
    }
    case 'gf-save': {
      const err = checkProfile(S.gf); if (err) { toast(err, true); break; }
      const o = withNums(S.gf, GF_KEYS);
      if (!(o.target_cal >= 500 && o.target_cal <= 10000)) { toast('Калории: от 500 до 10000', true); break; }
      if (!(await saveSettings(o))) break;
      if (Math.abs(o.weight - latestWeight()) > 0.04) { try { await post('/api/add_entry', { date: localDate(), weight: o.weight, steps: 0, note: '' }); } catch (e) {} }
      S.gf = null; S.page = null; await load(); toast('Настройки сохранены'); break;
    }
    case 'fb-kind': S.fb.kind = D.v; render(); break;
    case 'fb-send': {
      const text = S.fb.text.trim(); if (text.length < 5) { toast('Опишите пожелание подробнее, хотя бы пару слов', true); break; }
      try { await post('/api/add_feedback', { kind: S.fb.kind, text }); } catch (e) { toast(e.message, true); break; }
      S.fb.text = ''; S.page = null; render(); toast('Спасибо! Сообщение отправлено'); break;
    }
    case 'export': {
      try {
        const data = await api('/api/export_backup'), txt = JSON.stringify(data);
        if (tg) { await navigator.clipboard.writeText(txt); toast('Копия скопирована в буфер обмена. Вставьте её в заметки'); }
        else {
          const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([txt], { type: 'application/json' })); a.download = 'grammika-' + localDate() + '.json';
          document.body.appendChild(a); a.click(); a.remove(); toast('Файл сохранён');
        }
      } catch (e) { toast(tg ? 'Не удалось скопировать. Откройте приложение в браузере и повторите' : e.message, true); }
      break;
    }
    case 'wipe': {
      if (!await askConfirm('Удалить все данные?', 'Записи питания, тренировки, замеры, рецепты и цели будут стёрты без возможности восстановления.', 'Удалить всё', true)) break;
      try { await post('/api/delete_all_data', { confirm: true }); } catch (e) { toast(e.message, true); break; }
      S.page = null; S.shop = new Set(); S.exHist = null; S.gf = null; await load(); toast('Данные удалены'); break;
    }
    case 'redo-wizard': wizStart(); break;
    case 'policy': openSheet('Политика конфиденциальности', policyHtml()); break;
    case 'consent-ck': S.consentCk = !S.consentCk; renderConsent(); break;
    case 'consent-ok': {
      if (!S.consentCk) break;
      if (await saveSettings({ consent: '1' })) { S.consentOn = false; $('#wizard').classList.remove('open'); $('#wizard').innerHTML = ''; document.body.classList.remove('noscroll'); render(); }
      break;
    }

    /* знакомство */
    case 'wz-set': S.wiz.p[D.k] = D.k === 'activity' ? +D.v : D.v; renderWizard(); break;
    case 'wz-next': { const m = wizValid(); if (m) { toast(m, true); break; } S.wiz.step++; renderWizard(); $('#wizard').scrollTop = 0; break; }
    case 'wz-back': S.wiz.step--; renderWizard(); break;
    case 'wz-skip': { if (await saveSettings({ onboarded: '1' })) { wizClose(); render(); } break; }
    case 'wz-done': {
      const p = S.wiz.p, r = calcPlan(p);
      const o = Object.assign(withNums(p, ['sex', 'age', 'height', 'weight', 'target_weight', 'activity', 'goal', 'goal_rate']), { target_cal: r.kcal, target_p: r.prot, target_f: r.fat, target_c: r.carbs, target_water: r.water, onboarded: '1' });
      if (p.goal === 'maintain') o.target_weight = o.weight;
      if (!(await saveSettings(o))) break;
      try { await post('/api/add_entry', { date: localDate(), weight: o.weight, steps: 0, note: '' }); } catch (e) {}
      wizClose(); await load(); break;
    }
  }
}

document.addEventListener('click', e => {
  const t = e.target.closest('[data-act]');
  if (t) act(t.dataset.act, t, e);
});
document.addEventListener('input', e => {
  const p = e.target.dataset && e.target.dataset.in;
  if (p) { setPath(p, e.target.value); onInput(p); }
});
document.addEventListener('change', e => {
  const t = e.target, p = t.dataset && t.dataset.ch; if (!p) return;
  if (p === 'dateSel') { if (t.value) { S.date = t.value; load(); } return; }
  if (p === 'import') { importFile(t); return; }
  if (p === 'mealPhoto') { uploadMealPhoto(t); return; }
  if (p === 'aiPhoto') { recognizePhoto(t); return; }
  setPath(p, t.value);
  if (p === 'exSel') { S.exHist = null; render(); }
  else if (p === 'form.ex') { const l = S.d.last_sets[t.value]; if (l && !S.form.w && !S.form.r) { S.form.w = String(l.weight); S.form.r = String(l.reps); } render(); }
  else if (p === 'cardio.idx') render();
  else if (p === 'gf.goal_rate') updGfPreview();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape' && sheetRoot().classList.contains('open')) closeSheet(); });

async function importFile(inp) {
  const file = inp.files && inp.files[0]; inp.value = '';
  if (!file) return;
  let obj;
  try { obj = JSON.parse(await file.text()); } catch (e) { toast('Файл повреждён или не является копией Grammika', true); return; }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj) || !('entries' in obj || 'meals' in obj || 'settings' in obj)) { toast('Это не копия данных Grammika', true); return; }
  if (!await askConfirm('Восстановить из копии?', 'Все текущие данные будут заменены данными из файла.', 'Восстановить', true)) return;
  try { await post('/api/import_backup', obj); } catch (e) { toast(e.message, true); return; }
  S.exHist = null; await load(); toast('Данные восстановлены');
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { render(); load(); });
else { render(); load(); }

</script>
</body>
</html>
"""


# ---------------------------------------------------------------- API logic

class TooMany(Exception):
    pass


def insert_meal(cur, user_id, date, meal_type, name, grams, cal, p, f, c):
    cur.execute(
        """INSERT INTO meal_log
           (telegram_id, date, meal_type, food_name, grams, calories, protein, fat, carbs)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (user_id, date, meal_type, name, grams, cal, p, f, c),
    )


def round_grams(value, lo, hi):
    return min(hi, max(lo, round(value / 10) * 10))


def generate_day(cur, user_id, date):
    settings = get_user_settings(cur, user_id)
    target_cal = settings["target_cal"]
    excluded = settings["excluded"]

    filtered_foods = [f for f in DEFAULT_FOODS if not (menu_data.tags(f) & set(excluded))]
    if not filtered_foods:
        filtered_foods = DEFAULT_FOODS
    food_map = {f[0]: f for f in filtered_foods}
    full_map = {f[0]: f for f in DEFAULT_FOODS}

    cur.execute(
        "SELECT name, calories, protein, fat, carbs, COALESCE(meal_type, 'none') FROM custom_foods WHERE telegram_id = %s",
        (user_id,),
    )
    custom = [r for r in cur.fetchall() if r[5] != "none" and (r[1] or 0) > 0]

    cur.execute("DELETE FROM meal_log WHERE telegram_id = %s AND date = %s", (user_id, date))

    shares = {"breakfast": 0.25, "lunch": 0.35, "dinner": 0.25, "snack": 0.15}
    for m_type, m_share in shares.items():
        budget = target_cal * m_share

        # Свой рецепт, отмеченный для автоменю, занимает основную часть приёма пищи.
        eligible = [c for c in custom if c[5] in ("any", m_type)]
        if eligible and random.random() < 0.5:
            name, cal100, p100, f100, c100, _ = random.choice(eligible)
            grams = round_grams(budget * 0.7 / cal100 * 100, 50, 500)
            fac = grams / 100.0
            insert_meal(cur, user_id, date, m_type, name, grams, round(cal100 * fac, 1),
                        round((p100 or 0) * fac, 1), round((f100 or 0) * fac, 1),
                        round((c100 or 0) * fac, 1))
            rest = budget - cal100 * fac
            sides = [food_map[n] for n in SIDE_NAMES if n in food_map]
            if sides and rest >= 40:
                side = random.choice(sides)
                g = round_grams(rest / side[1] * 100, 30, 300)
                sfac = g / 100.0
                insert_meal(cur, user_id, date, m_type, side[0], g, round(side[1] * sfac, 1),
                            round(side[2] * sfac, 1), round(side[3] * sfac, 1), round(side[4] * sfac, 1))
            continue

        # Сочетание из библиотеки menu_data: порции в реалистичных границах, калории добираются гарниром
        for f_name, grams, cal, p, f, c in menu_data.build_meal(m_type, budget, full_map, excluded):
            insert_meal(cur, user_id, date, m_type, f_name, grams, cal, p, f, c)
    sync_daily_calories(cur, user_id, date)


def handle_post(cur, user_id, path, data):
    """Выполняет POST-запрос. Возвращает dict с доп. полями ответа или None, если путь неизвестен."""

    if path == "/api/import_backup":
        sections = {}
        for key in ("settings", "entries", "meals", "workouts", "custom_foods", "strength_sets"):
            v = data.get(key, [])
            if not isinstance(v, list):
                raise ValueError("bad backup section")
            sections[key] = v

        for table in USER_TABLES:
            if table == "feedback":
                continue  # отзывы резервной копией не затрагиваются
            cur.execute(f"DELETE FROM {table} WHERE telegram_id = %s", (user_id,))

        for k, v in sections["settings"]:
            if k in SETTINGS_KEYS:
                if k == "excluded" and isinstance(v, str):
                    v = json.loads(v)
                cur.execute(
                    "INSERT INTO settings (telegram_id, key, value) VALUES (%s, %s, %s) "
                    "ON CONFLICT (telegram_id, key) DO UPDATE SET value = excluded.value",
                    (user_id, k, clean_setting(k, v)),
                )
        for r in sections["entries"]:
            cur.execute(
                """INSERT INTO entries
                   (telegram_id, date, weight, calories, steps, note, water_ml)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (telegram_id, date) DO UPDATE SET
                       weight = excluded.weight, calories = excluded.calories,
                       steps = excluded.steps, note = excluded.note,
                       water_ml = excluded.water_ml""",
                (user_id, need_date(r[0]), num(r[1] or 0, 0, 500), int(num(r[2] or 0, 0, 100000)),
                 int(num(r[3] or 0, 0, 500000)), str(r[4] or "")[:500], int(num(r[5] or 0, 0, 100000))),
            )
        for r in sections["meals"]:
            insert_meal(cur, user_id, need_date(r[0]), r[1] if r[1] in MEAL_TYPES else "breakfast",
                        need_text(r[2]), num(r[3] or 0, 0, 5000), num(r[4] or 0, 0, 20000),
                        num(r[5] or 0, 0, 2000), num(r[6] or 0, 0, 2000), num(r[7] or 0, 0, 2000))
        for r in sections["workouts"]:
            cur.execute(
                """INSERT INTO workout_log
                   (telegram_id, date, workout_name, duration_min, calories_burned)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user_id, need_date(r[0]), need_text(r[1]), num(r[2] or 0, 0, 1440), num(r[3] or 0, 0, 10000)),
            )
        for r in sections["custom_foods"]:
            mt = r[5] if len(r) > 5 and r[5] in CUSTOM_MEAL_TYPES else "none"
            cur.execute(
                """INSERT INTO custom_foods (telegram_id, name, calories, protein, fat, carbs, meal_type)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (user_id, need_text(r[0]), num(r[1] or 0, 0, 2000), num(r[2] or 0, 0, 100),
                 num(r[3] or 0, 0, 100), num(r[4] or 0, 0, 100), mt),
            )
        for r in sections["strength_sets"]:
            cur.execute(
                """INSERT INTO strength_sets (telegram_id, date, exercise, weight, reps)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user_id, need_date(r[0]), need_text(r[1]), num(r[2], 0, 1000), int(num(r[3], 1, 1000))),
            )
        return {}

    if path == "/api/delete_all_data":
        if data.get("confirm") is not True:
            raise ValueError("confirmation required")
        for table in USER_TABLES:
            cur.execute(f"DELETE FROM {table} WHERE telegram_id = %s", (user_id,))
        extras.delete_user(cur, user_id)
        return {}

    if path == "/api/add_custom_food":
        meal_type = data.get("meal_type", "none")
        if meal_type not in CUSTOM_MEAL_TYPES:
            raise ValueError("bad meal type")
        cur.execute("SELECT COUNT(*) FROM custom_foods WHERE telegram_id = %s", (user_id,))
        if cur.fetchone()[0] >= 200:
            raise ValueError("too many custom foods")
        cur.execute(
            """INSERT INTO custom_foods (telegram_id, name, calories, protein, fat, carbs, meal_type)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (user_id, need_text(data["name"]), num(data["cal"], 0, 2000), num(data.get("p", 0), 0, 100),
             num(data.get("f", 0), 0, 100), num(data.get("c", 0), 0, 100), meal_type),
        )
        return {}

    if path == "/api/delete_custom_food":
        cur.execute(
            "DELETE FROM custom_foods WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
        )
        return {}

    if path == "/api/replace_meal":
        meal_id = int(data["id"])
        new_name = need_text(data["new_name"])
        cal100 = num(data["cal100"], 0, 2000)
        p100 = num(data["p100"], 0, 100)
        f100 = num(data["f100"], 0, 100)
        c100 = num(data["c100"], 0, 100)
        cur.execute(
            "SELECT grams, date FROM meal_log WHERE id = %s AND telegram_id = %s", (meal_id, user_id)
        )
        mrow = cur.fetchone()
        if mrow:
            fac = mrow[0] / 100.0
            cur.execute(
                """UPDATE meal_log SET food_name = %s, calories = %s, protein = %s, fat = %s, carbs = %s
                   WHERE id = %s AND telegram_id = %s""",
                (new_name, round(cal100 * fac, 1), round(p100 * fac, 1),
                 round(f100 * fac, 1), round(c100 * fac, 1), meal_id, user_id),
            )
            sync_daily_calories(cur, user_id, mrow[1])
        return {}

    if path == "/api/update_meal":
        meal_id = int(data["id"])
        cur.execute(
            """SELECT grams, calories, protein, fat, carbs, meal_type, date
               FROM meal_log WHERE id = %s AND telegram_id = %s""",
            (meal_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            return {}
        old_g, cal, p, f, c, m_type, date = row
        new_g = old_g
        if data.get("grams") is not None:
            new_g = num(data["grams"], 1, 5000)
        new_type = data.get("meal_type", m_type)
        if new_type not in MEAL_TYPES:
            raise ValueError("bad meal type")
        if new_g != old_g:
            if not old_g or old_g <= 0:
                raise ValueError("cannot scale")
            k = new_g / old_g
            cal, p, f, c = (round((x or 0) * k, 1) for x in (cal, p, f, c))
        cur.execute(
            """UPDATE meal_log SET grams = %s, calories = %s, protein = %s, fat = %s, carbs = %s,
                   meal_type = %s WHERE id = %s AND telegram_id = %s""",
            (new_g, cal, p, f, c, new_type, meal_id, user_id),
        )
        sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/copy_day":
        src = need_date(data["from_date"])
        dst = need_date(data["to_date"])
        if src == dst:
            raise ValueError("same day")
        meal_type = data.get("meal_type")
        if meal_type is not None and meal_type not in MEAL_TYPES:
            raise ValueError("bad meal type")
        sql = """SELECT meal_type, food_name, grams, calories, protein, fat, carbs
                 FROM meal_log WHERE telegram_id = %s AND date = %s"""
        params = [user_id, src]
        if meal_type:
            sql += " AND meal_type = %s"
            params.append(meal_type)
        cur.execute(sql + " ORDER BY id LIMIT 100", params)
        rows = cur.fetchall()
        for r in rows:
            insert_meal(cur, user_id, dst, r[0], r[1], r[2], r[3], r[4], r[5], r[6])
        if rows:
            sync_daily_calories(cur, user_id, dst)
        return {"copied": len(rows)}

    if path == "/api/save_goals":
        for k, v in data.items():
            if k not in SETTINGS_KEYS:
                continue
            cur.execute(
                """INSERT INTO settings (telegram_id, key, value) VALUES (%s, %s, %s)
                   ON CONFLICT(telegram_id, key) DO UPDATE SET value = excluded.value""",
                (user_id, k, clean_setting(k, v)),
            )
        return {}

    if path == "/api/add_water":
        date = need_date(data["date"])
        delta = int(num(data.get("delta", 250), -5000, 5000))
        cur.execute(
            "SELECT water_ml FROM entries WHERE telegram_id = %s AND date = %s", (user_id, date)
        )
        row = cur.fetchone()
        curr_w = row[0] if (row and row[0]) else 0
        new_w = max(0, curr_w + delta)
        cur.execute(
            """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
               VALUES (%s, %s, 0, 0, 0, '', %s)
               ON CONFLICT(telegram_id, date) DO UPDATE SET water_ml = excluded.water_ml""",
            (user_id, date, new_w),
        )
        return {}

    if path == "/api/add_meal":
        date = need_date(data["date"])
        meal_type = data.get("meal_type", "breakfast")
        if meal_type not in MEAL_TYPES:
            raise ValueError("bad meal type")
        insert_meal(cur, user_id, date, meal_type, need_text(data["name"]),
                    num(data["grams"], 0, 5000), num(data["cal"], 0, 20000),
                    num(data.get("p", 0), 0, 2000), num(data.get("f", 0), 0, 2000),
                    num(data.get("c", 0), 0, 2000))
        sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/delete_meal":
        cur.execute(
            "SELECT date FROM meal_log WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "DELETE FROM meal_log WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
            )
            extras.delete_meal_photo(cur, user_id, int(data["id"]))
            sync_daily_calories(cur, user_id, row[0])
        return {}

    if path == "/api/meal_photo":
        extras.save_meal_photo(cur, user_id, int(data["id"]), data["image"])
        return {}

    if path == "/api/meal_photo_delete":
        extras.delete_meal_photo(cur, user_id, int(data["id"]))
        return {}

    if path == "/api/clear_day_meals":
        date = need_date(data["date"])
        cur.execute("DELETE FROM meal_log WHERE telegram_id = %s AND date = %s", (user_id, date))
        sync_daily_calories(cur, user_id, date)
        return {}

    if path == "/api/generate_day":
        date = need_date(data["date"])
        generate_day(cur, user_id, date)
        return {}

    if path == "/api/add_entry":
        date = need_date(data["date"])
        weight = num(data.get("weight", 0), 0, 500)
        steps = int(num(data.get("steps", 0), 0, 500000))
        note = str(data.get("note", ""))[:500]
        cur.execute(
            """INSERT INTO entries (telegram_id, date, weight, calories, steps, note, water_ml)
               VALUES (%s, %s, %s, %s, %s, %s, 0)
               ON CONFLICT(telegram_id, date) DO UPDATE SET
                   weight = CASE WHEN excluded.weight > 0 THEN excluded.weight ELSE entries.weight END,
                   steps = CASE WHEN excluded.steps > 0 THEN excluded.steps ELSE entries.steps END,
                   note = CASE WHEN excluded.note != '' THEN excluded.note ELSE entries.note END""",
            (user_id, date, weight, 0, steps, note),
        )
        return {}

    if path == "/api/delete_entry":
        cur.execute(
            "DELETE FROM entries WHERE telegram_id = %s AND date = %s",
            (user_id, need_date(data["date"])),
        )
        return {}

    if path == "/api/add_workout":
        cur.execute(
            """INSERT INTO workout_log (telegram_id, date, workout_name, duration_min, calories_burned)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, need_date(data["date"]), need_text(data["name"]),
             num(data["mins"], 0, 1440), num(data["cal"], 0, 10000)),
        )
        return {}

    if path == "/api/delete_workout":
        cur.execute(
            "DELETE FROM workout_log WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
        )
        return {}

    if path == "/api/add_set":
        date = need_date(data["date"])
        exercise = need_text(data["exercise"])
        weight = num(data.get("weight", 0), 0, 1000)
        reps = int(num(data["reps"], 1, 1000))
        cur.execute(
            "SELECT MAX(weight) FROM strength_sets WHERE telegram_id = %s AND exercise = %s",
            (user_id, exercise),
        )
        prev = cur.fetchone()[0]
        is_pr = prev is not None and weight > prev and weight > 0
        cur.execute(
            """INSERT INTO strength_sets (telegram_id, date, exercise, weight, reps)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, date, exercise, weight, reps),
        )
        return {"pr": bool(is_pr)}

    if path == "/api/delete_set":
        cur.execute(
            "DELETE FROM strength_sets WHERE id = %s AND telegram_id = %s", (int(data["id"]), user_id)
        )
        return {}

    if path == "/api/add_feedback":
        text = str(data["text"]).strip()
        if len(text) < 5 or len(text) > 1500:
            raise ValueError("bad text length")
        kind = data.get("kind", "idea")
        if kind not in ("idea", "bug", "other"):
            raise ValueError("bad kind")
        cur.execute(
            "SELECT COUNT(*) FROM feedback WHERE telegram_id = %s AND created_at > now() - interval '1 day'",
            (user_id,),
        )
        if cur.fetchone()[0] >= FEEDBACK_PER_DAY:
            raise TooMany()
        cur.execute(
            "INSERT INTO feedback (telegram_id, kind, text) VALUES (%s, %s, %s)",
            (user_id, kind, text),
        )
        return {}

    return None


class NotFound(Exception):
    pass


DB_UNAVAILABLE = (psycopg.OperationalError, PoolTimeout)
BAD_INPUT = (KeyError, ValueError, TypeError, IndexError, psycopg.DataError)

GET_PATHS = ("/api/state", "/api/export_backup", "/api/exercise_history", "/api/foods_search",
             "/api/foods_chains", "/api/admin_stats", "/api/meal_photo", "/api/foods_catalog")


def notify_admin(user_id, kind, text):
    """Присылает новый отзыв в личный чат владельца. Ошибки не мешают пользователю."""
    if not (BOT_TOKEN and ADMIN_CHAT_ID):
        return
    labels = {"idea": "Идея", "bug": "Ошибка", "other": "Другое"}
    msg = "Новый отзыв Grammika\n" + labels.get(kind, "Другое") + " · пользователь " + str(user_id) + "\n\n" + text[:1500]

    def send():
        try:
            req = urllib.request.Request(
                "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage",
                data=json.dumps({"chat_id": ADMIN_CHAT_ID, "text": msg}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as exc:  # в тексте ошибки мог бы оказаться токен, поэтому пишем только тип
            print("Не удалось отправить уведомление об отзыве:", type(exc).__name__, flush=True)

    threading.Thread(target=send, daemon=True).start()


class GrammikaAppHandler(http.server.BaseHTTPRequestHandler):

    def _get_uid(self):
        init_data = self.headers.get("X-Telegram-Init-Data", "")
        if BOT_TOKEN:
            uid = verify_init_data(init_data, BOT_TOKEN)
            if not uid:
                self._send_json(
                    {"error": "Не удалось подтвердить пользователя Telegram. Откройте приложение из Telegram заново."},
                    401,
                )
            return uid
        # Режим разработки: подпись не проверяется.
        return extract_user_id(init_data, self.headers.get("X-Fallback-User", "default_user"))

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, exc, where):
        if isinstance(exc, DB_UNAVAILABLE):
            print(where, "- база недоступна:", repr(exc), flush=True)
            self._send_json(
                {"error": "База данных временно недоступна. Повторите через несколько секунд."}, 503
            )
        else:
            print(where, "- ошибка:", repr(exc), flush=True)
            self._send_json({"error": "Внутренняя ошибка сервера"}, 500)

    def do_HEAD(self):
        # Мониторинг (UptimeRobot, Render) проверяет сайт запросом HEAD: отвечаем заголовками без тела.
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = WEB_APP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path in ("/", "/index.html"):
            body = WEB_APP_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path not in GET_PATHS:
            self._send_json({"error": "not found"}, 404)
            return

        user_id = self._get_uid()
        if user_id is None:
            return
        qs = urllib.parse.parse_qs(parsed_url.query)
        try:
            if path == "/api/state":
                self._api_state(user_id, qs)
            elif path == "/api/exercise_history":
                self._api_exercise_history(user_id, qs)
            elif path == "/api/foods_search":
                q = qs.get("q", [""])[0].strip()[:80]
                brand = qs.get("brand", [""])[0].strip()[:80] or None
                self._send_json({"items": food_db.search(POOL, q, brand)})
            elif path == "/api/foods_chains":
                self._send_json({"items": food_db.chains(POOL)})
            elif path == "/api/foods_catalog":
                self._send_json({"items": food_db.catalog(POOL)})
            elif path == "/api/admin_stats":
                if not extras.is_admin(user_id):
                    self._send_json({"error": "not found"}, 404)
                    return
                with POOL.connection() as conn:
                    with conn.cursor() as cur:
                        self._send_json(extras.stats(cur))
            elif path == "/api/meal_photo":
                with POOL.connection() as conn:
                    with conn.cursor() as cur:
                        img = extras.get_meal_photo(cur, user_id, int(qs.get("id", ["0"])[0]))
                if not img:
                    self._send_json({"error": "not found"}, 404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "private, max-age=86400")
                self.send_header("Content-Length", str(len(img)))
                self.end_headers()
                self.wfile.write(img)
            else:
                self._api_export(user_id)
        except Exception as exc:
            self._fail(exc, "GET " + path)

    def _api_exercise_history(self, user_id, qs):
        exercise = qs.get("exercise", [""])[0].strip()
        if not exercise or len(exercise) > 100:
            self._send_json({"error": "Некорректное упражнение"}, 400)
            return
        with POOL.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT date, MAX(weight), MAX(reps), SUM(weight * reps),
                              MAX(CASE WHEN weight > 0 THEN weight * (1 + reps / 30.0) END)
                       FROM strength_sets WHERE telegram_id = %s AND exercise = %s
                       GROUP BY date ORDER BY date""",
                    (user_id, exercise),
                )
                points = [
                    {"date": r[0], "weight": r[1] or 0, "reps": r[2] or 0,
                     "volume": round(r[3] or 0, 1), "e1rm": round(r[4], 1) if r[4] else 0}
                    for r in cur.fetchall()
                ]
        self._send_json({"exercise": exercise, "points": points})

    def _api_state(self, user_id, qs):
        now_str = datetime.now().strftime("%Y-%m-%d")
        date_str = qs.get("date", [now_str])[0]
        today_str = qs.get("today", [date_str])[0]
        if not valid_date(date_str) or not valid_date(today_str):
            self._send_json({"error": "Некорректная дата"}, 400)
            return

        with POOL.connection() as conn:
            with conn.cursor() as cur:
                try:  # extras: учёт визитов не должен ломать загрузку приложения
                    cur.execute("SAVEPOINT touch_user")
                    fname, uname = extras.user_info_from_init(self.headers.get("X-Telegram-Init-Data", ""))
                    extras.touch_user(cur, user_id, fname, uname)
                    cur.execute("RELEASE SAVEPOINT touch_user")
                except Exception as exc:
                    cur.execute("ROLLBACK TO SAVEPOINT touch_user")
                    print("Учёт визита не записан:", repr(exc), flush=True)
                settings = get_user_settings(cur, user_id)

                cur.execute(
                    "SELECT date, weight, calories, steps, note, water_ml FROM entries WHERE telegram_id = %s ORDER BY date ASC",
                    (user_id,),
                )
                entries = [
                    {"date": r[0], "weight": r[1] or 0, "calories": r[2] or 0, "steps": r[3] or 0,
                     "note": r[4] or "", "water_ml": r[5] or 0}
                    for r in cur.fetchall()
                ]

                streak = compute_streak(cur, user_id, today_str)

                cur.execute(
                    """SELECT id, meal_type, food_name, grams, calories, protein, fat, carbs
                       FROM meal_log WHERE telegram_id = %s AND date = %s ORDER BY id ASC""",
                    (user_id, date_str),
                )
                day_meals = [
                    {"id": r[0], "meal_type": r[1] or "breakfast", "food_name": r[2],
                     "grams": r[3] or 0, "calories": r[4] or 0,
                     "p": r[5] or 0, "f": r[6] or 0, "c": r[7] or 0}
                    for r in cur.fetchall()
                ]
                with_photo = extras.photo_ids(cur, user_id, [m["id"] for m in day_meals])
                for m in day_meals:
                    m["photo"] = m["id"] in with_photo

                cur.execute(
                    """SELECT id, workout_name, duration_min, calories_burned
                       FROM workout_log WHERE telegram_id = %s AND date = %s ORDER BY id DESC""",
                    (user_id, date_str),
                )
                day_workouts = [
                    {"id": r[0], "name": r[1], "mins": r[2] or 0, "cal": r[3] or 0}
                    for r in cur.fetchall()
                ]

                # Калории, сожжённые по дням (для статистики).
                cur.execute(
                    "SELECT date, SUM(calories_burned) FROM workout_log WHERE telegram_id = %s GROUP BY date",
                    (user_id,),
                )
                burned_by_date = {r[0]: round(r[1] or 0) for r in cur.fetchall()}

                cur.execute(
                    """SELECT id, name, calories, protein, fat, carbs, COALESCE(meal_type, 'none')
                       FROM custom_foods WHERE telegram_id = %s ORDER BY id""",
                    (user_id,),
                )
                custom_foods = [
                    {"id": r[0], "name": r[1], "cal": r[2] or 0, "p": r[3] or 0,
                     "f": r[4] or 0, "c": r[5] or 0, "meal_type": r[6]}
                    for r in cur.fetchall()
                ]

                # Последние продукты: для быстрого повторного добавления.
                cur.execute(
                    """SELECT food_name, grams, calories, protein, fat, carbs FROM meal_log
                       WHERE telegram_id = %s AND id IN (
                           SELECT MAX(id) FROM meal_log WHERE telegram_id = %s GROUP BY food_name)
                       ORDER BY id DESC LIMIT 20""",
                    (user_id, user_id),
                )
                recent = [
                    {"name": r[0], "grams": r[1] or 0, "cal": r[2] or 0,
                     "p": r[3] or 0, "f": r[4] or 0, "c": r[5] or 0}
                    for r in cur.fetchall()
                ]

                # силовые тренировки
                cur.execute(
                    "SELECT id, exercise, weight, reps FROM strength_sets WHERE telegram_id = %s AND date = %s ORDER BY id ASC",
                    (user_id, date_str),
                )
                day_sets = [
                    {"id": r[0], "exercise": r[1], "weight": r[2] or 0, "reps": r[3]}
                    for r in cur.fetchall()
                ]
                cur.execute(
                    "SELECT DISTINCT exercise FROM strength_sets WHERE telegram_id = %s", (user_id,)
                )
                own_exercises = [r[0] for r in cur.fetchall()]
                exercises = sorted(set(ALL_DEFAULT_EXERCISES) | set(own_exercises))

                cur.execute(
                    "SELECT exercise, MAX(weight) FROM strength_sets WHERE telegram_id = %s GROUP BY exercise",
                    (user_id,),
                )
                prs = {r[0]: r[1] for r in cur.fetchall()}

                cur.execute(
                    """SELECT s.exercise, s.weight, s.reps FROM strength_sets s
                       JOIN (SELECT exercise, MAX(date) AS d FROM strength_sets
                             WHERE telegram_id = %s AND date < %s GROUP BY exercise) m
                         ON s.exercise = m.exercise AND s.date = m.d
                       WHERE s.telegram_id = %s""",
                    (user_id, date_str, user_id),
                )
                last_sets = {}
                for ex, wt, reps in cur.fetchall():
                    best = last_sets.get(ex)
                    if best is None or (wt, reps) > (best["weight"], best["reps"]):
                        last_sets[ex] = {"weight": wt, "reps": reps}

        day_entry = next((e for e in entries if e["date"] == date_str), None)
        water_ml = day_entry["water_ml"] if day_entry else 0

        all_foods = [
            {"name": f[0], "cal": f[1], "p": f[2], "f": f[3], "c": f[4], "cat": f[5]}
            for f in DEFAULT_FOODS
        ]

        self._send_json(
            {
                "version": APP_VERSION,
                "is_admin": extras.is_admin(user_id),
                "features": extras.features(),
                "contact": PRIVACY_CONTACT,
                "settings": settings,
                "water_ml": water_ml,
                "streak": streak,
                "today_cal": int(round(sum(m["calories"] for m in day_meals))),
                "today_p": round(sum(m["p"] for m in day_meals), 1),
                "today_f": round(sum(m["f"] for m in day_meals), 1),
                "today_c": round(sum(m["c"] for m in day_meals), 1),
                "today_burn": int(round(sum(w["cal"] for w in day_workouts))),
                "day_meals": day_meals,
                "day_workouts": day_workouts,
                "burned_by_date": burned_by_date,
                "entries": entries,
                "foods": all_foods,
                "custom_foods": custom_foods,
                "recent": recent,
                "workouts": [{"name": w[0], "met": w[1]} for w in DEFAULT_WORKOUTS],
                "programs": DEFAULT_PROGRAMS,
                "day_sets": day_sets,
                "exercises": exercises,
                "prs": prs,
                "last_sets": last_sets,
            }
        )

    def _api_export(self, user_id):
        with POOL.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM settings WHERE telegram_id = %s", (user_id,))
                settings = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, weight, calories, steps, note, water_ml FROM entries WHERE telegram_id = %s ORDER BY date",
                    (user_id,),
                )
                entries = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, meal_type, food_name, grams, calories, protein, fat, carbs FROM meal_log WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                meals = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, workout_name, duration_min, calories_burned FROM workout_log WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                workouts = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT name, calories, protein, fat, carbs, COALESCE(meal_type, 'none') FROM custom_foods WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                custom = [list(r) for r in cur.fetchall()]
                cur.execute(
                    "SELECT date, exercise, weight, reps FROM strength_sets WHERE telegram_id = %s ORDER BY id",
                    (user_id,),
                )
                sets = [list(r) for r in cur.fetchall()]

        self._send_json(
            {
                "app": "grammika",
                "version": APP_VERSION,
                "settings": settings,
                "entries": entries,
                "meals": meals,
                "workouts": workouts,
                "custom_foods": custom,
                "strength_sets": sets,
            }
        )

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/tg/webhook":  # extras: сообщения боту (/start, /stats)
            self._tg_webhook()
            return
        user_id = self._get_uid()
        if user_id is None:
            return
        if path == "/api/recognize":  # extras: распознавание еды по фото (долгий запрос, вне транзакции)
            self._api_recognize(user_id)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                self._send_json({"error": "Слишком большой запрос"}, 413)
                return
            raw_body = self.rfile.read(length).decode("utf-8") if length else ""
            data = json.loads(raw_body) if raw_body else {}
            if not isinstance(data, dict):
                raise ValueError("body must be an object")
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "Некорректный запрос"}, 400)
            return

        try:
            # Выход из блока with без ошибки = commit, любое исключение = rollback.
            with POOL.connection() as conn:
                with conn.cursor() as cur:
                    result = handle_post(cur, user_id, path, data)
                if result is None:
                    raise NotFound()
        except NotFound:
            self._send_json({"error": "not found"}, 404)
            return
        except TooMany:
            self._send_json({"error": "Слишком много сообщений за сутки. Попробуйте завтра."}, 429)
            return
        except BAD_INPUT:
            self._send_json({"error": "Некорректные данные"}, 400)
            return
        except Exception as exc:
            self._fail(exc, "POST " + path)
            return

        out = {"ok": True}
        out.update(result)
        self._send_json(out)
        if path == "/api/add_feedback":
            notify_admin(user_id, str(data.get("kind", "idea")), str(data.get("text", "")).strip())

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_BODY:
            raise ValueError("too large")
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            raise ValueError("body must be an object")
        return data

    def _tg_webhook(self):
        if not extras.check_webhook_secret(self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")):
            self._send_json({"error": "forbidden"}, 403)
            return
        try:
            update = self._read_json()
        except (ValueError, UnicodeDecodeError):
            self._send_json({"ok": True})
            return
        self._send_json({"ok": True})  # Telegram ждёт быстрый ответ, команду выполняем в фоне

        def work():
            try:
                extras.handle_update(POOL, update)
            except Exception as exc:
                print("Бот: ошибка обработки:", type(exc).__name__, flush=True)

        threading.Thread(target=work, daemon=True).start()

    def _api_recognize(self, user_id):
        try:
            data = self._read_json()
            result = extras.recognize(POOL, user_id, data.get("image"))
        except extras.AiLimit:
            self._send_json({"error": f"На сегодня лимит распознаваний исчерпан ({extras.AI_DAILY_LIMIT} в сутки)."}, 429)
            return
        except extras.AiError as exc:
            self._send_json({"error": str(exc)}, 502)
            return
        except (ValueError, UnicodeDecodeError, TypeError):
            self._send_json({"error": "Не удалось прочитать фото. Попробуйте другое."}, 400)
            return
        except Exception as exc:
            self._fail(exc, "POST /api/recognize")
            return
        out = {"ok": True}
        out.update(result)
        self._send_json(out)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    open_pool()
    init_db()
    try:
        food_db.setup(POOL)  # база продуктов: таблицы + меню сетей, обновление раз в сутки
    except Exception as exc:
        print("База продуктов не запустилась:", repr(exc), flush=True)
    try:
        extras.init(POOL)  # extras: учёт пользователей, фото, распознавание, бот
    except Exception as exc:
        print("Дополнения не запустились:", repr(exc), flush=True)
    if not BOT_TOKEN:
        print(
            "ВНИМАНИЕ: BOT_TOKEN не задан — подпись Telegram не проверяется (режим разработки). "
            "Перед публикацией задайте переменную окружения BOT_TOKEN.",
            flush=True,
        )
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), GrammikaAppHandler)
    print(f"Grammika Server running on port {PORT}", flush=True)
    server.serve_forever()
