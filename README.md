# Система учёта посетителей бассейна
## 1. Запуск приложения
### Для запуска приложения введите в окне терминала команду: 
### uvicorn main:app --reload
### База данных создастся автоматически
## 2. Библиотеки
### Установка библиотек тремя командами:
### pip install fastapi uvicorn
### pip install jinja2
### pip install python-multipart

# Database (Удалов Арсений)
## init_db.py
## Используется для создания таблиц и заполнения их тестовыми данными
### 1. Библиотеки, которые мы импортируем:
* import sqlite3 
* import os 
* from datetime import datetime, time, date, timedelta
* from database import db_instance, get_db_cursor, DATABASE_PATH (файл database)
### 2. Функции:
* table_exists() - Проверяет, существует ли таблица в базе данных SQLite
* create_tables() - Создаёт все таблицы:
* ensure_tables_exist() - Проверяет существование таблиц и создаёт их при необходимости
* seed_test_data() - Заполнение тестовыми данными для разработки
* drop_all_tables() - Удаляет ВСЕ таблицы из базы данных (без пересоздания)
* reset_database() - Удаляет все таблицы и создаёт их заново с тестовыми данными
* recreate_database() - Полностью пересоздаёт базу данных
* reset_database_safe() - Безопасная версия сброса с подтверждением
* show_tables() - Показать список всех таблиц в базе данных
* get_database_info() - Получить подробную информацию о базе данных
* create_full_database() - Создаёт полную базу данных с таблицами и тестовыми данными
### 3. Таблицы, которые мы создаём (в правильном порядке):
* Таблица родителей (parents)
* Таблица детей (children)
* Таблица тренеров (trainers)
* Таблица групп (groups)
* Таблица зачислений (enrollments)
* Таблица расписания (schedule)
* Таблица посещаемости(attendance)
* Таблица заявок (applications)
* Таблица логов администратора (admin_logs)
* Таблица уведомлений (notifications)
### 4. Самостоятельный запуск init_db
1) if len(sys.argv) < 2: Если аргументов меньше 2 (то есть нет команды)
2) Выводим справку (help)
3) sys.exit(0) Выходим из программы с кодом 0 (успешное завершение)
4) command = sys.argv[1] Берём первый аргумент (команду)
5) if command == "create": create_tables() Создание таблиц 
6) elif command == "seed": seed_test_data() Заполнение тестовыми данными 
7) elif command == "reset": reset_database() Полный сброс (удаление + создание + заполнение)
8) elif command == "drop": drop_all_tables() Удаление всех таблиц 
9) elif command == "recreate": recreate_database() Пересоздание с удалением файла 
10) elif command == "show": show_tables() Показать список таблиц 
11) elif command == "info": get_database_info() Показать подробную информацию о БД 
12) elif command == "full": create_full_database() Создать полную БД (таблицы + данные)
13) else: Если команда не распознана 
14) print(f"❌ Unknown command: {command}")
15) print("Use: create, seed, reset, drop, recreate, show, info, full")
### 5. Индексы для производительности:
* Индекс на внешний ключ родителя в таблице children
* Индекс на ID ребёнка в таблице зачислений 
* Индекс на ID группы в таблице зачислений 
* Индекс на ID зачисления в таблице посещаемости 
* Индекс на дату в таблице посещаемости 
* Индекс на статус в таблице заявок 
* Индекс на телефон в таблице заявок 
* Индекс на группу в таблице расписания 
* Индекс на статус в таблице уведомлений 
* Индекс на тренера в таблице групп
## database.py
## Управляет подключением к базе данных
### 1. Библиотеки, которые мы импортируем:
* import sqlite3 
* from contextlib import contextmanager 
* from fastapi import FastAPI, Depends, HTTPException
### 2. Класс Database 
1) def __init__(): Класс для управления подключением к SQLite
2) get_connection(): Создаёт соединение с SQLite при первом запросе
3) close(): Закрыть соединение с БД
### 3. Функции:
* get_db_cursor() - Автоматически управляет транзакциями
* get_db() - Используется в эндпоинтах для получения курсора
* init_database() - Функция для инициализации БД (будет вызвана при старте)  

# BACKEND (Смирнов Александр)

## main.py — основное приложение FastAPI

### Используемые библиотеки
- `fastapi` — веб-фреймворк  
- `uvicorn` — ASGI‑сервер  
- `jinja2` — шаблонизатор  
- `python-multipart` — работа с формами
- `sqlite3` — работа с БД  
- `datetime`, `secrets`, `typing` — стандартные библиотеки  

### Основные функции и модули

#### 1. Управление сессиями и аутентификация
- `generate_session_token()` — создаёт уникальный токен для сессии  
- `active_sessions` — словарь {token: {user_id, user_type, login}}  
- `get_current_user()` — извлекает пользователя из cookie и БД  
- Функции проверки ролей:  
  - `require_parent()`  
  - `require_trainer()`  
  - `require_admin()`  
  - `require_trainer_or_admin()`

#### 2. Публичные маршруты (без авторизации)
| Эндпоинт    | Описание                       |
|-------------|--------------------------------|
| `/`         | Главная страница               |
| `/login`    | Вход (админ, тренер, родитель) |
| `/logout`   | Выход из системы               |
| `/apply`    | Подача заявки на зачисление    |
| `/gallery`  | Страница галереи               |

#### 3. Родитель (личный кабинет)
| Эндпоинт                   | Описание                                                     |
|----------------------------|--------------------------------------------------------------|
| `/parent/profile`          | Список всех детей родителя                                   |
| `/parent/child/{child_id}` | Детальная карточка ребёнка: группа, расписание, посещаемость |

#### 4. Тренер
| Эндпоинт                                              | Описание                                             |
|-------------------------------------------------------|------------------------------------------------------|
| `/trainer/dashboard`                                  | Список групп (свои или все, если админ)              |
| `/trainer/group/{group_id}`                           | Просмотр группы: ученики, расписание                 |
| `/trainer/group/{group_id}/add_student`               | Добавление ученика в группу                          |
| `/trainer/group/{group_id}/remove_student/{child_id}` | Отчисление ученика                                   |
| `/trainer/student/{child_id}/edit`                    | Редактирование педагогических данных ученика         |
| `/trainer/group/{group_id}/attendance`                | Журнал посещаемости (отметка на выбранную дату)      |
| `/trainer/group/{group_id}/schedule/edit`             | Управление расписанием (добавление/удаление занятий) |

#### 5. Администратор
| Эндпоинт                              | Описание                                                  |
|---------------------------------------|-----------------------------------------------------------|
| `/admin/applications`                 | Список заявок с фильтрацией (статус, возраст, смена)      |
| `/admin/application/{app_id}`         | Карточка заявки                                           |
| `/admin/application/{app_id}/approve` | Одобрение заявки (автоматический или ручной выбор группы) |
| `/admin/application/{app_id}/reject`  | Отклонение заявки с указанием причины                     |
| `/admin/trainers`                     | Список тренеров                                           |
| `/admin/trainer/create`               | Форма создания тренера                                    |
| `/admin/trainer/{trainer_id}/edit`    | Редактирование тренера                                    |
| `/admin/trainer/{trainer_id}/delete`  | Удаление тренера (обнуляет привязку групп)                |
| `/admin/groups`                       | Список групп                                              |
| `/admin/group/create`                 | Форма создания группы                                     |
| `/admin/group/{group_id}/edit`        | Редактирование группы                                     |
| `/admin/group/{group_id}/delete`      | Удаление группы (с переводом учеников или отчислением)    |
| `/admin/api/stats`                    | JSON‑статистика для дашборда админа                       |
| `/admin/api/recent_applications`      | Последние 10 заявок (JSON)                                |

#### 7. Особенности реализации
- **Сессии** — cookie с токеном, хранилище в памяти (при перезапуске сервера сессии сбрасываются).   
- **Права доступа** — роли `parent`, `trainer`, `admin`.  
- **Даты** — все поля `datetime` преобразуются в строки перед передачей в шаблоны (чтобы избежать ошибок `datetime` не subscriptable).  
- **Вспомогательные функции**:
  - `can_manage_group()` — проверка доступа тренера к группе  
  - `auto_select_group()` — подбор группы по возрасту, году обучения, смене



# Шифрование паролей и VK бот

## 📋 Содержание
1. [Введение](#введение)
2. [Шифрование паролей (bcrypt)](#шифрование-паролей-bcrypt)
3. [Интеграция с VK ботом](#интеграция-с-vk-ботом)
4. [Установка и запуск](#установка-и-запуск)
5. [Список библиотек](#список-библиотек)
6. [Основные функции](#основные-функции)
7. [Команды VK бота](#команды-vk-бота)

---

## Введение

В данном проекте выполнены два ключевых улучшения для CRM системы бассейна:

1. **Шифрование паролей** — все пароли пользователей хешируются с помощью bcrypt
2. **Интеграция с VK ботом** — родители могут получать информацию о детях через ВКонтакте

---

## Шифрование паролей (bcrypt)

### Проблема
Изначально пароли хранились в базе данных в открытом виде, что создавало серьёзную уязвимость.

### Решение
Внедрено хеширование паролей с использованием библиотеки `bcrypt` с солью и 12 раундами шифрования.

### Реализация

#### Функции хеширования (`init_db.py`)

```python
import bcrypt

def hash_password(password: str) -> str:
    """Хеширует пароль с помощью bcrypt"""
    if not password:
        password = "default123"
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверяет соответствие пароля хешу"""
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False
```

#### Создание пользователей с хешированным паролем

```python
# Создание родителя (пароль = телефон)
hashed_password = hash_password(parent_phone)
cursor.execute(
    "INSERT INTO parents (full_name, phone, password_hash) VALUES (?, ?, ?)",
    (full_name, phone, hashed_password)
)

# Создание тренера
hashed_password = hash_password(password)
cursor.execute(
    "INSERT INTO trainers (full_name, login, password_hash) VALUES (?, ?, ?)",
    (full_name, login, hashed_password)
)
```

#### Проверка пароля при входе

```python
from init_db import verify_password

# В эндпоинте /login
if admin and verify_password(password, admin["password_hash"]):
    # Успешный вход
```


---

## Интеграция с VK ботом

### Функциональность

VK бот позволяет родителям:
- 🔗 Привязать VK аккаунт к личному кабинету
- 👶 Просматривать список своих детей и их группы
- 📊 Проверять статус заявки на зачисление
- 👤 Получать информацию о профиле

### Структура бота

#### 1. Конфигурация (`.env` файл)

```env
VK_GROUP_TOKEN=vk1.a.ваш_токен
VK_GROUP_ID=239120595
VK_BOT_ENABLED=True
```

#### 2. Отдельное соединение с БД (`bot_db.py`)

Поскольку бот работает в отдельном потоке, ему требуется собственное соединение с базой данных:

```python
class BotDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(
            DATABASE_PATH,
            check_same_thread=False,  # Важно для многопоточности
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        self.connection.row_factory = sqlite3.Row

    def get_cursor(self):
        return self.connection.cursor()
```

#### 3. Основной класс бота (`vk_bot.py`)

```python
class VKBot:
    def __init__(self, group_token, group_id):
        self.group_token = group_token
        self.group_id = group_id
        self.vk_session = None
        self.vk = None
        self.longpoll = None

    def init_api(self):
        """Инициализирует VK API и LongPoll"""
        self.vk_session = vk_api.VkApi(token=self.group_token)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkBotLongPoll(self.vk_session, self.group_id)

    def send_message(self, user_id, message):
        """Отправляет сообщение пользователю"""
        self.vk.messages.send(user_id=user_id, message=message, random_id=0)

    def link_vk_account(self, vk_id, phone, password):
        """Привязывает VK аккаунт к родителю"""
        # Поиск родителя по телефону
        # Проверка пароля через verify_password()
        # Сохранение vk_id в БД

    def handle_message(self, user_id, message_text):
        """Обработчик команд"""
        if message_text == "/my_children":
            return self.format_children_list(children)
        elif message_text.startswith("/link"):
            return self.link_vk_account(...)
        # ... обработка других команд
```

#### 4. Запуск бота в фоновом потоке (`main.py`)

```python
from vk_bot import start_vk_bot, stop_vk_bot

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запуск при старте
    start_vk_bot()
    yield
    # Остановка при завершении
    stop_vk_bot()
```

---

## Список библиотек

| Библиотека | Версия | Назначение |
|------------|--------|------------|
| **fastapi** | 0.104.1 | Веб-фреймворк для API |
| **uvicorn** | 0.24.0 | ASGI сервер для запуска приложения |
| **jinja2** | 3.1.2 | Шаблонизатор для HTML страниц |
| **python-multipart** | 0.0.6 | Обработка form-data запросов |
| **bcrypt** | 4.0.1 | Хеширование паролей |
| **vk-api** | 11.9.9 | Работа с API ВКонтакте |
| **python-dotenv** | 1.0.0 | Загрузка переменных из .env файла |

### Установка одной командой

```bash
pip install fastapi uvicorn jinja2 python-multipart bcrypt vk-api python-dotenv
```

---

## Основные функции

### Шифрование (init_db.py)

| Функция | Параметры | Возвращает | Описание |
|---------|-----------|------------|----------|
| `hash_password(password)` | `password: str` | `str` (хеш) | Хеширует пароль с помощью bcrypt |
| `verify_password(plain, hashed)` | `plain: str, hashed: str` | `bool` | Проверяет соответствие пароля хешу |

### VK бот (vk_bot.py)

| Функция | Параметры | Описание |
|---------|-----------|----------|
| `__init__(group_token, group_id)` | токен, ID группы | Инициализация бота |
| `init_api()` | - | Подключение к VK API |
| `send_message(user_id, message)` | ID пользователя, текст | Отправка сообщения |
| `link_vk_account(vk_id, phone, password)` | VK ID, телефон, пароль | Привязка аккаунта |
| `get_parent_by_vk(vk_id)` | VK ID | Поиск родителя по VK ID |
| `get_children_by_parent(parent_id)` | ID родителя | Получение списка детей |
| `handle_message(user_id, text)` | ID пользователя, текст | Обработчик команд |
| `run()` | - | Запуск LongPoll прослушивания |

### Запуск бота (vk_bot.py)

| Функция | Описание |
|---------|----------|
| `start_vk_bot()` | Запускает бота в фоновом потоке |
| `stop_vk_bot()` | Останавливает бота |

---

## Команды VK бота

| Команда | Формат | Описание |
|---------|--------|----------|
| `/help` | `/help` | Показать список команд |
| `/link` | `/link [телефон] [пароль]` | Привязать VK к аккаунту |
| `/my_children` | `/my_children` | Список детей и их групп |
| `/status` | `/status [номер_заявки]` | Статус заявки на зачисление |
| `/profile` | `/profile` | Информация о профиле |
| `/apply` | `/apply` | Инструкция по подаче заявки |

### Примеры использования

```
/link +79123456789 password123
✅ Аккаунт привязан! Добро пожаловать, Сергей Петров

/my_children
👶 Ваши дети:
   👤 Алексей Петров
      Возраст: 7 лет
      Группа: Рыбки
      ✅ Зачислен

/status 5
📋 Заявка #5
Ребёнок: Иван Иванов
Статус: 🟢 Одобрена
```

---

## Требования к группе ВК

Для работы бота необходимо:

1. **Создать группу** ВКонтакте (или использовать существующую)
2. **Включить сообщения сообщества:**
   - Управление → Сообщения → Включить сообщения сообщества
3. **Создать ключ доступа с правами:**
   - ✅ Управление сообществом
   - ✅ Доступ к сообщениям сообщества
4. **Добавить бота в администраторы** (опционально, но рекомендуется)

---


## Возможные ошибки и их решение

### 1. VK Bot: "Cannot operate on a closed cursor"

**Причина:** Бот использует курсор из основного приложения, который закрывается после HTTP запроса.

**Решение:** Создано отдельное соединение в `bot_db.py`.

### 2. VK Bot: "Access denied: no access to call this method"

**Причина:** Недостаточно прав у токена.

**Решение:** При создании токена выберите права:
- Управление сообществом
- Доступ к сообщениям сообщества

