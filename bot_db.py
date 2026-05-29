# bot_db.py (НОВЫЙ ФАЙЛ)
import sqlite3
from database import DATABASE_PATH


class BotDatabase:
    """Отдельное соединение с БД для VK бота"""

    def __init__(self):
        self.connection = None
        self.connect()

    def connect(self):
        """Создает соединение с БД"""
        try:
            self.connection = sqlite3.connect(
                DATABASE_PATH,
                check_same_thread=False,  # Важно для многопоточности
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            )
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.row_factory = sqlite3.Row
            print("✅ Bot database connection established")
        except Exception as e:
            print(f"❌ Bot database connection failed: {e}")

    def get_cursor(self):
        """Возвращает курсор для выполнения запросов"""
        if not self.connection:
            self.connect()
        return self.connection.cursor()

    def commit(self):
        """Коммитит изменения"""
        if self.connection:
            self.connection.commit()

    def close(self):
        """Закрывает соединение"""
        if self.connection:
            self.connection.close()
            self.connection = None
            print("✅ Bot database connection closed")


# Глобальный экземпляр для бота
bot_db = BotDatabase()