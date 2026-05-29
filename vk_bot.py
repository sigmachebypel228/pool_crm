# vk_bot.py (ИСПРАВЛЕННАЯ ВЕРСИЯ - замените весь файл)
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
import re
from datetime import datetime
import threading
import logging
from bot_db import bot_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VKBot:
    def __init__(self, group_token, group_id):
        self.group_token = group_token
        self.group_id = group_id
        self.vk_session = None
        self.vk = None
        self.longpoll = None
        self.running = False
        self.thread = None

    def init_api(self):
        """Инициализирует VK API"""
        try:
            self.vk_session = vk_api.VkApi(token=self.group_token)
            self.vk = self.vk_session.get_api()

            # Проверяем, работает ли API
            group = self.vk.groups.getById(group_id=self.group_id)
            logger.info(f"✅ VK API initialized for group: {group[0]['name']}")

            # Пытаемся создать LongPoll
            try:
                self.longpoll = VkBotLongPoll(self.vk_session, self.group_id)
                logger.info("✅ VK Bot LongPoll initialized")
            except Exception as e:
                logger.warning(f"⚠️ LongPoll not available: {e}")
                self.longpoll = None

            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize VK API: {e}")
            return False

    def send_message(self, user_id, message, keyboard=None):
        """Отправляет сообщение пользователю"""
        try:
            params = {
                "user_id": user_id,
                "message": message,
                "random_id": 0
            }
            if keyboard:
                params["keyboard"] = keyboard

            self.vk.messages.send(**params)
            logger.info(f"Message sent to {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {user_id}: {e}")
            return False

    def get_user_info(self, user_id):
        """Получает информацию о пользователе VK"""
        try:
            user = self.vk.users.get(user_ids=user_id, fields="first_name,last_name,domain")[0]
            return user
        except Exception as e:
            logger.error(f"Failed to get user info {user_id}: {e}")
            return None

    def link_vk_account(self, vk_id, phone, password):
        """Привязывает VK аккаунт к родителю"""
        try:
            from init_db import verify_password

            cursor = bot_db.get_cursor()

            cursor.execute(
                "SELECT id, full_name, phone, password_hash FROM parents WHERE phone = ?",
                (phone,)
            )
            parent = cursor.fetchone()

            if not parent:
                return False, "❌ Родитель с таким телефоном не найден"

            # Проверяем пароль
            if not verify_password(password, parent["password_hash"]):
                return False, "❌ Неверный пароль"

            # Обновляем vk_id
            cursor.execute(
                "UPDATE parents SET vk_id = ? WHERE id = ?",
                (str(vk_id), parent["id"])
            )
            bot_db.commit()

            return True, f"✅ Аккаунт привязан! Добро пожаловать, {parent['full_name']}"

        except Exception as e:
            logger.error(f"Failed to link account: {e}")
            return False, "❌ Ошибка при привязке аккаунта"

    def get_parent_by_vk(self, vk_id):
        """Находит родителя по VK ID"""
        cursor = bot_db.get_cursor()
        cursor.execute(
            "SELECT id, full_name, phone FROM parents WHERE vk_id = ?",
            (str(vk_id),)
        )
        return cursor.fetchone()

    def get_children_by_parent(self, parent_id):
        """Получает список детей родителя"""
        cursor = bot_db.get_cursor()
        cursor.execute(
            """
            SELECT c.id, c.full_name, c.age, c.school_name, c.shift,
                   g.name as group_name, e.is_active
            FROM children c
            LEFT JOIN enrollments e ON c.id = e.child_id AND e.is_active = 1
            LEFT JOIN groups g ON e.group_id = g.id
            WHERE c.parent_id = ?
            """,
            (parent_id,)
        )
        return cursor.fetchall()

    def format_children_list(self, children):
        """Форматирует список детей для отправки"""
        if not children:
            return "👶 У вас пока нет зарегистрированных детей.\n\nОтправьте /apply чтобы подать заявку."

        message = "👶 *Ваши дети:*\n\n"
        for child in children:
            status = "✅ Зачислен" if child["is_active"] else "⏳ Ожидает зачисления"
            group_info = f"Группа: {child['group_name']}" if child["group_name"] else "Группа: не назначена"
            message += f"👤 *{child['full_name']}*\n"
            message += f"   Возраст: {child['age']} лет\n"
            message += f"   Школа: {child['school_name']}\n"
            message += f"   {group_info}\n"
            message += f"   {status}\n\n"
        return message

    def handle_message(self, user_id, message_text):
        """Обрабатывает входящие сообщения"""
        message_text = message_text.strip()

        # Проверяем, привязан ли пользователь
        parent = self.get_parent_by_vk(user_id)
        is_linked = parent is not None

        # Команды
        if message_text == "/start" or message_text == "/help":
            from config import BOT_SETTINGS
            return BOT_SETTINGS["help_message"]

        elif message_text == "/apply":
            if not is_linked:
                return "⚠️ Для подачи заявки необходимо привязать аккаунт.\nОтправьте: /link [телефон] [пароль]\n\nПример: /link +79123456789 password123"
            return self.create_application_form(user_id)

        elif message_text.startswith("/link"):
            parts = message_text.split()
            if len(parts) >= 3:
                phone = parts[1]
                password = " ".join(parts[2:])
                success, message = self.link_vk_account(user_id, phone, password)
                return message
            else:
                return "⚠️ Неверный формат. Используйте:\n/link [телефон] [пароль]\n\nПример: /link +79123456789 password123"

        elif message_text == "/my_children" or message_text == "/children":
            if not is_linked:
                return "⚠️ Сначала привяжите аккаунт: /link [телефон] [пароль]"
            children = self.get_children_by_parent(parent["id"])
            return self.format_children_list(children)

        elif message_text == "/profile":
            if not is_linked:
                return "⚠️ Сначала привяжите аккаунт: /link [телефон] [пароль]"
            children_count = len(self.get_children_by_parent(parent["id"]))
            return f"""👤 *Ваш профиль*

Имя: {parent['full_name']}
Телефон: {parent['phone']}
VK ID: {user_id}

Связанные дети: {children_count}"""

        elif message_text == "/ping":
            return "🏓 Pong! Бот работает."

        else:
            return "❓ Неизвестная команда. Отправьте /help для списка команд."

    def create_application_form(self, user_id):
        """Создаёт форму заявки (отправляет ссылку на сайт)"""
        return """📝 *Подача заявки на занятия*

Для подачи заявки перейдите по ссылке:
http://localhost:8000/apply

Или отправьте заявку в текстовом формате:
Имя ребёнка, возраст, школа, смена (утро/вечер)

Пример:
Иван Иванов, 7 лет, Школа №1, утро

Наш администратор свяжется с вами."""

    def run_longpoll(self):
        """Запускает LongPoll режим"""
        if not self.longpoll:
            logger.warning("LongPoll not available")
            return

        self.running = True
        logger.info("🚀 VK Bot LongPoll started...")

        try:
            for event in self.longpoll.listen():
                if not self.running:
                    break

                if event.type == VkBotEventType.MESSAGE_NEW:
                    if event.object and hasattr(event.object, 'message'):
                        message = event.object.message
                        user_id = message['from_id']
                        message_text = message.get('text', '')

                        if message_text:
                            response = self.handle_message(user_id, message_text)
                            if response:
                                self.send_message(user_id, response)

        except Exception as e:
            logger.error(f"VK Bot LongPoll error: {e}")
        finally:
            logger.info("VK Bot LongPoll stopped")

    def run(self):
        """Запускает бота"""
        if not self.init_api():
            return

        if self.longpoll:
            self.run_longpoll()
        else:
            logger.warning("⚠️ LongPoll not available. Use webhook mode instead.")
            logger.info("   Set up webhook at: https://your-domain.com/api/vk/webhook")
            self.running = True
            while self.running:
                import time
                time.sleep(1)

    def stop(self):
        """Останавливает бота"""
        self.running = False
        logger.info("Stopping VK Bot...")


# Глобальный экземпляр бота
vk_bot_instance = None


def start_vk_bot():
    """Запускает VK бота в отдельном потоке"""
    global vk_bot_instance

    from config import VK_CONFIG

    if not VK_CONFIG["group_token"] or VK_CONFIG["group_token"] == "ваш_токен_сюда":
        logger.warning("⚠️ VK Bot not configured. Set VK_GROUP_TOKEN in .env file")
        return None

    if vk_bot_instance is None:
        vk_bot_instance = VKBot(
            group_token=VK_CONFIG["group_token"],
            group_id=VK_CONFIG["group_id"]
        )

        # Запускаем в отдельном потоке
        thread = threading.Thread(target=vk_bot_instance.run, daemon=True)
        thread.start()
        logger.info("✅ VK Bot started in background thread")

    return vk_bot_instance


def stop_vk_bot():
    """Останавливает VK бота"""
    global vk_bot_instance
    if vk_bot_instance:
        vk_bot_instance.stop()
        vk_bot_instance = None