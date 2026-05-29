# main.py

import secrets
import json
from contextlib import asynccontextmanager
from typing import Annotated, Optional
from datetime import date, datetime, timedelta

from init_db import verify_password

import os

from fastapi import (
    FastAPI, Request, HTTPException, Depends, status,
    Form, UploadFile, File, Cookie, Query)

from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from database import db_instance, get_db
from init_db import create_tables, seed_test_data, ensure_tables_exist

from vk_bot import start_vk_bot, stop_vk_bot


# Простая аутентификация (прямое сравнение строк)



# ------------------- Управление сессиями -------------------
active_sessions = {}  # token -> {"user_id": int, "user_type": str, "login": str}

def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def get_current_user(
        session_token: Optional[str] = Cookie(None),
        db_cursor = Depends(get_db)
) -> Optional[dict]:
    """
       Возвращает словарь с данными текущего пользователя или None.
       Структура: {
           "id": int,
           "name": str,
           "login": str,
           "type": "parent" | "trainer",
           "is_admin": bool (только для trainer)
       }
       """

    # Текущий пользователь не найден
    if not session_token or session_token not in active_sessions:
        return None

    session = active_sessions[session_token]
    user_id = session["user_id"]
    user_type = session["user_type"]

    # Если админ
    if user_type == "admin":
        db_cursor.execute(
            "SELECT id, full_name, login FROM admins WHERE id = ?",
            (user_id,)
        )
        user = db_cursor.fetchone()
        if user:
            return {
                "id": user["id"],
                "name": user["full_name"],
                "login": user["login"],
                "type": "admin"
            }
    # Если тренер
    elif user_type == "trainer":
        db_cursor.execute(
            "SELECT id, full_name, login FROM trainers WHERE id = ?",
            (user_id,)
        )
        user = db_cursor.fetchone()
        if user:
            return {
                "id": user["id"],
                "name": user["full_name"],
                "login": user["login"],
                "type": "trainer"
            }
    # Если родитель
    elif user_type == "parent":
        db_cursor.execute(
            "SELECT id, full_name, phone FROM parents WHERE id = ?",
            (user_id,)
        )
        user = db_cursor.fetchone()
        if user:
            return {
                "id": user["id"],
                "name": user["full_name"],
                "login": user["phone"],  # логин = телефон
                "type": "parent"
            }
    return None


def format_dates_in_dict(data: dict, date_fields: list) -> dict:
    """Преобразует datetime-поля в строки формата 'YYYY-MM-DD HH:MM:SS'"""
    for field in date_fields:
        if field in data and data[field] is not None:
            if hasattr(data[field], 'strftime'):
                data[field] = data[field].strftime('%Y-%m-%d %H:%M:%S')
    return data


# Проверка ролей/прав
def require_parent(current_user: Optional[dict]):
    if not current_user or current_user["type"] != "parent":
        raise HTTPException(status_code=403, detail="Доступ только для родителей")

def require_trainer(current_user: Optional[dict]):
    if not current_user or current_user["type"] != "trainer":
        raise HTTPException(status_code=403, detail="Доступ только для тренеров")

def require_admin(current_user: Optional[dict]):
    if not current_user or current_user["type"] != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администратора")

def require_trainer_or_admin(current_user: Optional[dict]):
    if not current_user or current_user["type"] not in ("trainer", "admin"):
        raise HTTPException(status_code=403, detail="Недостаточно прав")

def require_parent_or_self_child(current_user: Optional[dict], child_id: int, db_cursor):
    """Проверяет, что родитель имеет доступ к указанному ребёнку (своему)."""
    if current_user["type"] == "admin":
        return True
    if current_user["type"] == "parent":
        db_cursor.execute("SELECT id FROM children WHERE id = ? AND parent_id = ?", (child_id, current_user["id"]))
        return db_cursor.fetch


# Вспомогательная функция для проверки доступа тренера к группе
def can_manage_group(group_id: int, current_user: dict, db_cursor) -> bool:
    """Проверяет, может ли текущий пользователь (тренер или админ) управлять группой."""
    if current_user["type"] == "admin":
        return True
    if current_user["type"] == "trainer":
        db_cursor.execute("SELECT trainer_id FROM groups WHERE id = ?", (group_id,))
        group = db_cursor.fetchone()
        return group is not None and group["trainer_id"] == current_user["id"]
    return False


# Инициализация базы данных (добавление полей при необходимости)
def upgrade_database():
    """
    Приводит структуру БД к актуальному состоянию:
    - Создаёт таблицу admins, если её нет.
    - Добавляет password_hash в parents, если нет.
    - Создаёт тестового администратора, если таблица admins пуста.
    """
    with db_instance.get_connection() as conn:
        cursor = conn.cursor()

        # 1. Создаём таблицу admins, если её нет
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name VARCHAR(255) NOT NULL,
                login VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Добавляем тестового администратора, если таблица пуста
        cursor.execute("SELECT COUNT(*) FROM admins")
        if cursor.fetchone()[0] == 0:
            # Логин: admin, пароль: admin (временное решение, без хеширования)
            cursor.execute(
                "INSERT INTO admins (full_name, login, password_hash) VALUES (?, ?, ?)",
                ("Администратор системы", "admin", "admin")
            )
            print("✅ Создан тестовый администратор: login='admin', password='admin'")

        # 2. Для таблицы parents: добавляем password_hash (если нет)
        cursor.execute("PRAGMA table_info(parents)")
        columns = [col[1] for col in cursor.fetchall()]
        if "password_hash" not in columns:
            cursor.execute("ALTER TABLE parents ADD COLUMN password_hash VARCHAR(255)")
            # Для существующих родителей проставляем пароль = телефон
            cursor.execute("SELECT id, phone FROM parents")
            for row in cursor.fetchall():
                cursor.execute(
                    "UPDATE parents SET password_hash = ? WHERE id = ?",
                    (row["phone"], row["id"])
                )
            print("✅ Добавлено поле password_hash в таблицу parents")

        conn.commit()


# ---------- Вспомогательные функции для уведомлений ----------

def send_notification_to_parent(parent_id: int, subject: str, message: str, db_cursor):
    """
    Отправляет уведомление родителю (пока только запись в таблицу notifications,
    в будущем можно добавить email/SMS/VK).
    """
    db_cursor.execute(
        """
        INSERT INTO notifications (user_id, user_type, type, title, message, status, sent_at)
        VALUES (?, 'parent', 'system', ?, ?, 'pending', NULL)
        """,
        (parent_id, subject, message)
    )
    db_cursor.connection.commit()
    # Здесь можно добавить реальную отправку (email, VK и т.д.)
    print(f"📢 Уведомление для родителя {parent_id}: {subject} - {message}")


def auto_select_group(child_age: int, swimming_years: int, shift: str, db_cursor) -> Optional[dict]:
    """
    Автоматически подбирает подходящую группу по возрасту, году обучения и смене.
    Возвращает словарь с данными группы или None.
    """
    db_cursor.execute(
        """
        SELECT id, name, min_age, max_age, swimming_year, max_students, shift,
               (SELECT COUNT(*) FROM enrollments WHERE group_id = groups.id AND is_active = 1) as enrolled
        FROM groups
        WHERE is_active = 1
          AND ? BETWEEN min_age AND max_age
          AND swimming_year = ?
          AND shift = ?
          AND (SELECT COUNT(*) FROM enrollments WHERE group_id = groups.id AND is_active = 1) < max_students
        ORDER BY min_age, swimming_year
        LIMIT 1
        """,
        (child_age, swimming_years, shift)
    )
    group = db_cursor.fetchone()
    return dict(group) if group else None

# Жизненный цикл приложения
# main.py - обновите функцию lifespan (только часть с запуском бота)

from vk_bot import start_vk_bot, stop_vk_bot


# Удалите импорт get_db_cursor из database, если он там был

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управляет жизненным циклом приложения"""
    print("Запуск приложения...")

    tables_created = ensure_tables_exist()
    if tables_created:
        print("Таблицы созданы, заполняем тестовыми данными...")
        seed_test_data()
    else:
        from database import get_db_cursor
        with get_db_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM trainers")
            if cursor.fetchone()[0] == 0:
                seed_test_data()
    upgrade_database()

    # ЗАПУСКАЕМ VK БОТА (без передачи курсора)
    try:
        from config import BOT_SETTINGS
        if BOT_SETTINGS.get("enabled", True):
            start_vk_bot()
        else:
            print("ℹ️ VK Bot disabled in configuration")
    except Exception as e:
        print(f"⚠️ VK Bot startup error: {e}")

    print("Приложение готово")
    yield

    # ОСТАНАВЛИВАЕМ VK БОТА
    stop_vk_bot()

    print("Остановка приложения, закрытие соединения с БД...")
    db_instance.close()
    print("Соединение закрыто")


# Инициализация FastAPI
app = FastAPI(
    title="pool_crm",
    description="CRM для бассейна (школьные группы)",
    version="1.0.0",
    lifespan=lifespan
)

# Подключение статики и шаблонов
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")



# ======================== ПУБЛИЧНЫЕ МАРШРУТЫ (не требуют авторизации) ========================

# ---------- Главная страница (информация о школе) ----------
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"request": request}
    )


# ---------- Галерея ----------
@app.get("/gallery", response_class=HTMLResponse)
async def gallery_page(request: Request):
    """ Страница галереи. """
    return templates.TemplateResponse(request, "gallery.html", {"request": request})

@app.get("/api/gallery/images")
async def get_gallery_images():
    """ Возвращает список относительных путей к изображениям из папки static/images/gallery. """
    gallery_dir = "static/images/gallery"
    if not os.path.exists(gallery_dir):
        raise HTTPException(status_code=404, detail="Папка галереи не найдена")

    extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
    images = []
    for filename in sorted(os.listdir(gallery_dir)):
        if filename.lower().endswith(extensions):
            images.append(f"/static/images/gallery/{filename}")
    return {"images": images}

# ---------- Страница входа для всех пользователей ----------
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request}
    )

@app.post("/login")
async def login(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    db_cursor = Depends(get_db)
):
    """
    Обработка входа:
    - сначала проверяем среди администраторов
    - затем среди тренеров
    - затем среди родителей (по телефону)
    """
    # 1. Проверка администратора
    db_cursor.execute(
        "SELECT id, full_name, login, password_hash FROM admins WHERE login = ?",
        (username,)
    )
    admin = db_cursor.fetchone()
    if admin and verify_password(password, admin["password_hash"]):
        token = generate_session_token()
        active_sessions[token] = {
            "user_id": admin["id"],
            "user_type": "admin",
            "login": admin["login"]
        }
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie("session_token", token, httponly=True)
        return response

    # 2. Проверка тренера
    db_cursor.execute(
        "SELECT id, full_name, login, password_hash FROM trainers WHERE login = ?",
        (username,)
    )
    trainer = db_cursor.fetchone()
    if trainer and verify_password(password, trainer["password_hash"]):
        token = generate_session_token()
        active_sessions[token] = {
            "user_id": trainer["id"],
            "user_type": "trainer",
            "login": trainer["login"]
        }
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie("session_token", token, httponly=True)
        return response

    # 3. Проверка родителя (по телефону)
    db_cursor.execute(
        "SELECT id, full_name, phone, password_hash FROM parents WHERE phone = ?",
        (username,)
    )
    parent = db_cursor.fetchone()
    if parent and verify_password(password, parent["password_hash"]):
        token = generate_session_token()
        active_sessions[token] = {
            "user_id": parent["id"],
            "user_type": "parent",
            "login": parent["phone"]
        }
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie("session_token", token, httponly=True)
        return response

    # Если ничего не подошло — вернуться на страницу входа с ошибкой
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": "Неверный логин или пароль"
        }
    )

@app.get("/logout")
async def logout():
    """Выход из системы — удаляем сессию и cookie."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response


# ---------- Подача заявки ----------
@app.get("/apply", response_class=HTMLResponse)
async def apply_form(request: Request):
    """
    Публичная форма для подачи заявки на зачисление.
    """
    return templates.TemplateResponse(
        request,
        "application_form.html",
        {"request": request}
    )

@app.post("/apply")
async def submit_application(
    request: Request,
    parent_full_name: Annotated[str, Form()],
    parent_phone: Annotated[str, Form()],
    child_full_name: Annotated[str, Form()],
    child_age: Annotated[int, Form()],
    school_name: Annotated[str, Form()],
    shift: Annotated[str, Form()],
    parent_email: Annotated[Optional[str], Form()] = None,
    child_class: Annotated[Optional[int], Form()] = None,
    swimming_years: Annotated[int, Form()] = 1,
    desired_lessons_per_week: Annotated[int, Form()] = 2,
    db_cursor = Depends(get_db)
):
    """
    Обработка и сохранение заявки.
    """
    # Простая валидация
    if not (3 <= child_age <= 17):
        return templates.TemplateResponse(
            request,
            "application_form.html",
            {
                "request": request,
                "error": "Возраст ребёнка должен быть от 3 до 17 лет",
                "form_data": dict(await request.form())  # для возврата введённых данных
            }
        )

    if shift not in ("day", "evening"):
        shift = "day"

    if desired_lessons_per_week not in (1, 2, 3):
        desired_lessons_per_week = 2

    # Сохраняем заявку
    db_cursor.execute(
        """
        INSERT INTO applications
        (parent_full_name, parent_phone, parent_email, child_full_name, child_age,
         child_class, school_name, swimming_years, shift, desired_lessons_per_week, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
        """,
        (parent_full_name, parent_phone, parent_email, child_full_name, child_age,
         child_class, school_name, swimming_years, shift, desired_lessons_per_week)
    )
    db_cursor.connection.commit()

    # Отображаем страницу успеха
    return templates.TemplateResponse(
        request,
        "application_success.html",
        {
            "request": request,
            "message": "Заявка успешно отправлена! Наш администратор свяжется с вами в ближайшее время."
        }
    )



# ======================== РОДИТЕЛЬ ========================

# ---------- Личный кабинет ----------
@app.get("/parent/profile", response_class=HTMLResponse)
async def parent_profile(
    request: Request,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """
    Личный кабинет родителя — показывает список всех детей.
    """
    require_parent(current_user)
    parent_id = current_user["id"]

    # Выбираем всех детей родителя
    db_cursor.execute(
        """
        SELECT id, full_name, age, class_number, school_name,
               swimming_years, shift, desired_lessons_per_week
        FROM children
        WHERE parent_id = ?
        ORDER BY full_name
        """,
        (parent_id,)
    )
    children = db_cursor.fetchall()

    # Для каждого ребёнка узнаем его текущую группу
    children_with_group = []
    for child in children:
        db_cursor.execute(
            """
            SELECT g.id, g.name, g.shift, t.full_name as trainer_name
            FROM enrollments e
            JOIN groups g ON e.group_id = g.id
            LEFT JOIN trainers t ON g.trainer_id = t.id
            WHERE e.child_id = ? AND e.is_active = 1
            """,
            (child["id"],)
        )
        group = db_cursor.fetchone()
        children_with_group.append({
            "id": child["id"],
            "full_name": child["full_name"],
            "age": child["age"],
            "class_number": child["class_number"],
            "school_name": child["school_name"],
            "swimming_years": child["swimming_years"],
            "shift": child["shift"],
            "desired_lessons_per_week": child["desired_lessons_per_week"],
            "group": dict(group) if group else None
        })

    return templates.TemplateResponse(
        request,
        "parent_profile.html",
        {
            "request": request,
            "children": children_with_group,
            "current_user": current_user
        }
    )


# ---------- Профили детей ----------
# main.py - замените ВСЮ функцию child_details

@app.get("/parent/child/{child_id}", response_class=HTMLResponse)
async def child_details(
        request: Request,
        child_id: int,
        current_user=Depends(get_current_user),
        db_cursor=Depends(get_db)
):
    """
    Детальная страница ребёнка: информация, группа, расписание, посещаемость.
    """
    require_parent(current_user)
    parent_id = current_user["id"]

    # Проверяем, что ребёнок принадлежит этому родителю
    db_cursor.execute(
        "SELECT * FROM children WHERE id = ? AND parent_id = ?",
        (child_id, parent_id)
    )
    child_row = db_cursor.fetchone()
    if not child_row:
        raise HTTPException(status_code=404, detail="Ребёнок не найден")

    # Преобразуем child в словарь
    child = dict(child_row)

    # Преобразуем created_at если есть
    if child.get('created_at') and hasattr(child['created_at'], 'strftime'):
        child['created_at'] = child['created_at'].strftime('%Y-%m-%d %H:%M:%S')

    # Текущее зачисление
    db_cursor.execute(
        """
        SELECT g.id, g.name, g.shift, g.min_age, g.max_age, g.max_students,
               t.full_name as trainer_name, e.enrolled_at
        FROM enrollments e
        JOIN groups g ON e.group_id = g.id
        LEFT JOIN trainers t ON g.trainer_id = t.id
        WHERE e.child_id = ? AND e.is_active = 1
        """,
        (child_id,)
    )
    enrollment = db_cursor.fetchone()
    group = dict(enrollment) if enrollment else None

    # Преобразуем enrolled_at в строку, если это datetime
    if group and group.get('enrolled_at') and hasattr(group['enrolled_at'], 'strftime'):
        group['enrolled_at'] = group['enrolled_at'].strftime('%Y-%m-%d %H:%M:%S')

    # Расписание занятий группы (если есть)
    schedule_items = []
    if group:
        db_cursor.execute(
            """
            SELECT id, weekday, start_time, end_time, location, is_recurring, single_date
            FROM schedule
            WHERE group_id = ?
            ORDER BY weekday, start_time
            """,
            (group["id"],)
        )
        schedule_rows = db_cursor.fetchall()
        schedule_items = []
        for row in schedule_rows:
            item = dict(row)
            # Преобразуем время, если нужно
            if item.get('start_time') and hasattr(item['start_time'], 'strftime'):
                item['start_time'] = item['start_time'].strftime('%H:%M:%S')
            if item.get('end_time') and hasattr(item['end_time'], 'strftime'):
                item['end_time'] = item['end_time'].strftime('%H:%M:%S')
            schedule_items.append(item)

    # Посещаемость: последние 30 дней
    attendance = []
    if group:
        # Найдём enrollment_id для этого ребёнка
        db_cursor.execute(
            "SELECT id FROM enrollments WHERE child_id = ? AND group_id = ? AND is_active = 1",
            (child_id, group["id"])
        )
        enrollment_record = db_cursor.fetchone()
        if enrollment_record:
            db_cursor.execute(
                """
                SELECT date, status
                FROM attendance
                WHERE enrollment_id = ?
                ORDER BY date DESC
                LIMIT 30
                """,
                (enrollment_record["id"],)
            )
            attendance_rows = db_cursor.fetchall()
            for row in attendance_rows:
                rec = dict(row)
                if rec.get('date') and hasattr(rec['date'], 'strftime'):
                    rec['date'] = rec['date'].strftime('%Y-%m-%d')
                attendance.append(rec)

    return templates.TemplateResponse(
        request,
        "child_details.html",
        {
            "request": request,
            "child": child,
            "group": group,
            "schedule": schedule_items,
            "attendance": attendance,
            "current_user": current_user
        }
    )

# ======================== ТРЕНЕР ========================

# ---------- Личный кабинет тренера ----------
@app.get("/trainer/dashboard", response_class=HTMLResponse)
async def trainer_dashboard(
    request: Request,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Дашборд тренера: список групп (админ видит все, тренер — только свои)."""
    require_trainer_or_admin(current_user)

    if current_user["type"] == "admin":
        db_cursor.execute(
            """
            SELECT g.id, g.name, g.min_age, g.max_age, g.max_students, g.shift,
                   t.full_name as trainer_name, COUNT(e.id) as enrolled
            FROM groups g
            LEFT JOIN trainers t ON g.trainer_id = t.id
            LEFT JOIN enrollments e ON e.group_id = g.id AND e.is_active = 1
            GROUP BY g.id
            ORDER BY g.name
            """
        )
    else:  # тренер
        db_cursor.execute(
            """
            SELECT g.id, g.name, g.min_age, g.max_age, g.max_students, g.shift,
                   t.full_name as trainer_name, COUNT(e.id) as enrolled
            FROM groups g
            LEFT JOIN trainers t ON g.trainer_id = t.id
            LEFT JOIN enrollments e ON e.group_id = g.id AND e.is_active = 1
            WHERE g.trainer_id = ?
            GROUP BY g.id
            ORDER BY g.name
            """,
            (current_user["id"],)
        )
    groups = db_cursor.fetchall()

    return templates.TemplateResponse(
        request,
        "trainer_dashboard.html",
        {
            "request": request,
            "groups": groups,
            "current_user": current_user
        }
    )


# ---------- Просмотр группы (ученики + расписание) ----------
@app.get("/trainer/group/{group_id}", response_class=HTMLResponse)
async def group_view(
    request: Request,
    group_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Детальная страница группы: список учеников, расписание, кнопки действий."""
    require_trainer_or_admin(current_user)

    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа к этой группе")

    # Информация о группе
    db_cursor.execute(
        """
        SELECT g.*, t.full_name as trainer_name
        FROM groups g
        LEFT JOIN trainers t ON g.trainer_id = t.id
        WHERE g.id = ?
        """,
        (group_id,)
    )
    group = db_cursor.fetchone()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    # Список учеников (активных)
    db_cursor.execute(
        """
        SELECT c.id, c.full_name, c.age, c.class_number, c.school_name,
               c.swimming_years, c.shift, e.enrolled_at
        FROM enrollments e
        JOIN children c ON e.child_id = c.id
        WHERE e.group_id = ? AND e.is_active = 1
        ORDER BY c.full_name
        """,
        (group_id,)
    )
    students = db_cursor.fetchall()

    # Расписание группы
    db_cursor.execute(
        """
        SELECT id, weekday, start_time, end_time, location, is_recurring, single_date
        FROM schedule
        WHERE group_id = ?
        ORDER BY weekday, start_time
        """,
        (group_id,)
    )
    schedule = db_cursor.fetchall()

    return templates.TemplateResponse(
        request,
        "group_details.html",
        {
            "request": request,
            "group": dict(group),
            "students": students,
            "schedule": schedule,
            "current_user": current_user,
            "group_id": group_id
        }
    )


# ---------- Управление учениками в группе ----------

# Страница добавления ученика (форма поиска по незачисленным детям)
@app.get("/trainer/group/{group_id}/add_student", response_class=HTMLResponse)
async def add_student_form(
    request: Request,
    group_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Форма для поиска и добавления ученика в группу."""
    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    # Список всех детей, которые ещё не зачислены в эту группу (или не зачислены никуда)
    db_cursor.execute(
        """
        SELECT c.id, c.full_name, c.age, c.class_number, c.school_name
        FROM children c
        WHERE c.id NOT IN (
            SELECT child_id FROM enrollments WHERE group_id = ? AND is_active = 1
        )
        ORDER BY c.full_name
        """,
        (group_id,)
    )
    available_children = db_cursor.fetchall()

    return templates.TemplateResponse(
        request,
        "add_student_to_group.html",
        {
            "request": request,
            "group_id": group_id,
            "children": available_children,
            "current_user": current_user
        }
    )


# main.py - исправленный фрагмент (замените соответствующие функции)

@app.post("/trainer/group/{group_id}/add_student")
async def add_student_submit(
        request: Request,
        group_id: int,
        child_id: Annotated[int, Form()],
        current_user=Depends(get_current_user),
        db_cursor=Depends(get_db)
):
    """Добавляет ученика в группу."""
    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    # Проверяем, существует ли группа
    db_cursor.execute("SELECT id, max_students FROM groups WHERE id = ?", (group_id,))
    group = db_cursor.fetchone()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    # Проверяем, не переполнена ли группа
    db_cursor.execute(
        "SELECT COUNT(*) as cnt FROM enrollments WHERE group_id = ? AND is_active = 1",
        (group_id,)
    )
    enrolled = db_cursor.fetchone()["cnt"]
    if enrolled >= group["max_students"]:
        return RedirectResponse(
            url=f"/trainer/group/{group_id}?error=full",
            status_code=303
        )

    # Проверяем, не зачислен ли уже
    db_cursor.execute(
        "SELECT id FROM enrollments WHERE child_id = ? AND group_id = ? AND is_active = 1",
        (child_id, group_id)
    )
    if db_cursor.fetchone():
        return RedirectResponse(
            url=f"/trainer/group/{group_id}?error=already_enrolled",
            status_code=303
        )

    # Проверяем, существует ли ребёнок
    db_cursor.execute("SELECT id FROM children WHERE id = ?", (child_id,))
    if not db_cursor.fetchone():
        raise HTTPException(status_code=404, detail="Ребёнок не найден")

    # Добавляем запись
    db_cursor.execute(
        "INSERT INTO enrollments (child_id, group_id, enrolled_at) VALUES (?, ?, ?)",
        (child_id, group_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    db_cursor.connection.commit()

    # Отправляем уведомление родителю
    db_cursor.execute(
        "SELECT parent_id FROM children WHERE id = ?",
        (child_id,)
    )
    child = db_cursor.fetchone()
    if child:
        send_notification_to_parent(
            child["parent_id"],
            "Зачисление в группу",
            f"Ваш ребёнок зачислен в группу. Подробности в личном кабинете.",
            db_cursor
        )

    return RedirectResponse(url=f"/trainer/group/{group_id}?success=added", status_code=303)

@app.post("/trainer/group/{group_id}/remove_student/{child_id}")
async def remove_student(
    request: Request,
    group_id: int,
    child_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Отчисляет (мягко удаляет) ученика из группы."""
    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    db_cursor.execute(
        "UPDATE enrollments SET is_active = 0 WHERE child_id = ? AND group_id = ?",
        (child_id, group_id)
    )
    db_cursor.connection.commit()
    return RedirectResponse(url=f"/trainer/group/{group_id}?success=removed", status_code=303)


# ---------- Редактирование данных ученика ----------
@app.get("/trainer/student/{child_id}/edit", response_class=HTMLResponse)
async def edit_student_form(
    request: Request,
    child_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """
    Редактирование данных ученика (только педагогические поля).
    Доступно тренеру, если ребёнок числится в группе этого тренера, либо админу.
    """
    require_trainer_or_admin(current_user)

    # Получаем данные ученика
    db_cursor.execute(
        """
        SELECT id, full_name, age, class_number, school_name,
               swimming_years, shift, desired_lessons_per_week
        FROM children
        WHERE id = ?
        """,
        (child_id,)
    )
    child = db_cursor.fetchone()
    if not child:
        raise HTTPException(status_code=404, detail="Ученик не найден")

    # Проверяем, что тренер имеет право (если не админ)
    if current_user["type"] != "admin":
        # Найдём группу, где числится этот ученик, и проверим, что тренер группы — текущий
        db_cursor.execute(
            """
            SELECT g.trainer_id
            FROM enrollments e
            JOIN groups g ON e.group_id = g.id
            WHERE e.child_id = ? AND e.is_active = 1
            """,
            (child_id,)
        )
        group = db_cursor.fetchone()
        if not group or group["trainer_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Нет прав на редактирование этого ученика")

    return templates.TemplateResponse(
        request,
        "edit_student.html",
        {
            "request": request,
            "child": dict(child),
            "current_user": current_user
        }
    )

@app.post("/trainer/student/{child_id}/edit")
async def edit_student_submit(
    request: Request,
    child_id: int,
    full_name: Annotated[str, Form()],
    age: Annotated[int, Form()],
    school_name: Annotated[str, Form()],
    swimming_years: Annotated[int, Form()],
    shift: Annotated[str, Form()],
    desired_lessons_per_week: Annotated[int, Form()],
    class_number: Annotated[Optional[int], Form()] = None,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Обновление данных ученика."""
    require_trainer_or_admin(current_user)

    # Проверка прав такая же, как в GET
    if current_user["type"] != "admin":
        db_cursor.execute(
            """
            SELECT g.trainer_id
            FROM enrollments e
            JOIN groups g ON e.group_id = g.id
            WHERE e.child_id = ? AND e.is_active = 1
            """,
            (child_id,)
        )
        group = db_cursor.fetchone()
        if not group or group["trainer_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Нет прав на редактирование")

    # Валидация
    if not (3 <= age <= 17):
        raise HTTPException(status_code=400, detail="Возраст должен быть от 3 до 17")
    if shift not in ("day", "evening"):
        shift = "day"
    if desired_lessons_per_week not in (1, 2, 3):
        desired_lessons_per_week = 2

    db_cursor.execute(
        """
        UPDATE children
        SET full_name = ?, age = ?, class_number = ?, school_name = ?,
            swimming_years = ?, shift = ?, desired_lessons_per_week = ?
        WHERE id = ?
        """,
        (full_name, age, class_number, school_name, swimming_years, shift, desired_lessons_per_week, child_id)
    )
    db_cursor.connection.commit()
    return RedirectResponse(url=f"/trainer/student/{child_id}/edit?success=updated", status_code=303)


# ---------- Журнал посещаемости ----------
@app.get("/trainer/group/{group_id}/attendance", response_class=HTMLResponse)
async def attendance_mark_form(
    request: Request,
    group_id: int,
    date_param: Optional[str] = None,  # дата в формате YYYY-MM-DD
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Форма для отметки посещаемости на выбранную дату."""
    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    # Если дата не задана, используем сегодняшнюю
    if not date_param:
        dt = date.today()
    else:
        try:
            dt = date.fromisoformat(date_param)
        except ValueError:
            raise HTTPException(status_code=400, detail="Неверный формат даты")

    # Список активных учеников группы
    db_cursor.execute(
        """
        SELECT c.id, c.full_name
        FROM enrollments e
        JOIN children c ON e.child_id = c.id
        WHERE e.group_id = ? AND e.is_active = 1
        ORDER BY c.full_name
        """,
        (group_id,)
    )
    students = db_cursor.fetchall()

    # Существующие отметки на эту дату
    attendance_map = {}
    for student in students:
        # Находим enrollment_id
        db_cursor.execute("SELECT id FROM enrollments WHERE child_id = ? AND group_id = ? AND is_active = 1", (student["id"], group_id))
        enrollment = db_cursor.fetchone()
        if enrollment:
            db_cursor.execute(
                "SELECT status FROM attendance WHERE enrollment_id = ? AND date = ?",
                (enrollment["id"], dt.isoformat())
            )
            row = db_cursor.fetchone()
            attendance_map[student["id"]] = row["status"] if row else None

    return templates.TemplateResponse(
        request,
        "attendance_mark.html",
        {
            "request": request,
            "group_id": group_id,
            "students": students,
            "date": dt,
            "attendance": attendance_map,
            "current_user": current_user
        }
    )


@app.post("/trainer/group/{group_id}/attendance")
async def attendance_mark_save(
    request: Request,
    group_id: int,
    date_str: Annotated[str, Form()],
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Сохранение отметок посещаемости."""
    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    form = await request.form()
    # Ожидается, что в форме будут поля вида "status_{child_id}" со значениями 'present', 'absent', 'sick', 'excused'
    for key, value in form.items():
        if key.startswith("status_"):
            child_id = int(key.split("_")[1])
            # Находим enrollment_id
            db_cursor.execute(
                "SELECT id FROM enrollments WHERE child_id = ? AND group_id = ? AND is_active = 1",
                (child_id, group_id)
            )
            enrollment = db_cursor.fetchone()
            if not enrollment:
                continue
            enrollment_id = enrollment["id"]
            # Проверяем, есть ли уже запись
            db_cursor.execute(
                "SELECT id FROM attendance WHERE enrollment_id = ? AND date = ?",
                (enrollment_id, date_str)
            )
            existing = db_cursor.fetchone()
            if existing:
                db_cursor.execute(
                    "UPDATE attendance SET status = ?, mark_time = ? WHERE id = ?",
                    (value, datetime.now().isoformat(), existing["id"])
                )
            else:
                db_cursor.execute(
                    "INSERT INTO attendance (enrollment_id, date, status, mark_time) VALUES (?, ?, ?, ?)",
                    (enrollment_id, date_str, value, datetime.now().isoformat())
                )
    db_cursor.connection.commit()
    return RedirectResponse(url=f"/trainer/group/{group_id}/attendance?date={date_str}&success=1", status_code=303)


# ---------- Управление расписанием группы ----------
@app.get("/trainer/group/{group_id}/schedule/edit", response_class=HTMLResponse)
async def edit_schedule_form(
    request: Request,
    group_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Форма для редактирования расписания: можно добавлять/удалять занятия."""
    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    # Получаем расписание группы
    db_cursor.execute(
        "SELECT id, weekday, start_time, end_time, location, is_recurring, single_date FROM schedule WHERE group_id = ?",
        (group_id,)
    )
    schedule = db_cursor.fetchall()

    return templates.TemplateResponse(
        request,
        "edit_schedule.html",
        {
            "request": request,
            "group_id": group_id,
            "schedule": schedule,
            "weekdays": ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"],
            "current_user": current_user
        }
    )


@app.post("/trainer/group/{group_id}/schedule/add")
async def add_schedule_item(
    request: Request,
    group_id: int,
    weekday: Annotated[int, Form()],
    start_time: Annotated[str, Form()],
    end_time: Annotated[str, Form()],
    location: Annotated[str, Form()] = "",
    is_recurring: Annotated[bool, Form()] = True,
    single_date: Annotated[Optional[str], Form()] = None,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Добавляет занятие в расписание группы."""
    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    db_cursor.execute(
        """
        INSERT INTO schedule (group_id, weekday, start_time, end_time, location, is_recurring, single_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (group_id, weekday, start_time, end_time, location, is_recurring, single_date)
    )
    db_cursor.connection.commit()
    return RedirectResponse(url=f"/trainer/group/{group_id}/schedule/edit?success=added", status_code=303)


@app.post("/trainer/schedule/{schedule_id}/delete")
async def delete_schedule_item(
    request: Request,
    schedule_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Удаляет занятие из расписания."""
    # Сначала узнаем group_id
    db_cursor.execute("SELECT group_id FROM schedule WHERE id = ?", (schedule_id,))
    row = db_cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Занятие не найдено")
    group_id = row["group_id"]

    require_trainer_or_admin(current_user)
    if not can_manage_group(group_id, current_user, db_cursor):
        raise HTTPException(status_code=403, detail="Нет доступа")

    db_cursor.execute("DELETE FROM schedule WHERE id = ?", (schedule_id,))
    db_cursor.connection.commit()
    return RedirectResponse(url=f"/trainer/group/{group_id}/schedule/edit?success=deleted", status_code=303)



# ======================== АДМИН ========================

# ---------- Обработка заявок ----------

@app.get("/admin/applications", response_class=HTMLResponse)
async def admin_applications(
    request: Request,
    status_filter: Optional[str] = Query(None, description="Фильтр по статусу"),
    age_min: Optional[int] = Query(None, description="Мин. возраст"),
    age_max: Optional[int] = Query(None, description="Макс. возраст"),
    swimming_year: Optional[int] = Query(None, description="Год обучения"),
    shift_filter: Optional[str] = Query(None, description="Смена (day/evening)"),
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)

    # Базовый запрос
    query = "SELECT * FROM applications WHERE 1=1"
    params = []

    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if age_min is not None:
        query += " AND child_age >= ?"
        params.append(age_min)
    if age_max is not None:
        query += " AND child_age <= ?"
        params.append(age_max)
    if swimming_year is not None:
        query += " AND swimming_years = ?"
        params.append(swimming_year)
    if shift_filter:
        query += " AND shift = ?"
        params.append(shift_filter)

    query += " ORDER BY created_at DESC"
    db_cursor.execute(query, params)

    # Вместо прямого fetchall() преобразование дат
    rows = db_cursor.fetchall()
    # Преобразуем каждую заявку
    applications = []
    for row in rows:
        app = dict(row)
        # Преобразуем поле created_at (и возможно processed_at)
        if app.get('created_at') and hasattr(app['created_at'], 'strftime'):
            app['created_at'] = app['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        if app.get('processed_at') and hasattr(app['processed_at'], 'strftime'):
            app['processed_at'] = app['processed_at'].strftime('%Y-%m-%d %H:%M:%S')
        applications.append(app)

    # Для отображения списка групп в форме одобрения
    db_cursor.execute(
        "SELECT id, name, min_age, max_age, swimming_year, shift, max_students FROM groups WHERE is_active = 1"
    )
    all_groups = db_cursor.fetchall()

    return templates.TemplateResponse(
        request,
        "admin_applications.html",
        {
            "request": request,
            "applications": applications,
            "all_groups": all_groups,
            "filters": {
                "status": status_filter,
                "age_min": age_min,
                "age_max": age_max,
                "swimming_year": swimming_year,
                "shift": shift_filter
            },
            "current_user": current_user
        }
    )


@app.get("/admin/application/{app_id}", response_class=HTMLResponse)
async def admin_application_detail(
    request: Request,
    app_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    """Просмотр карточки заявки."""
    require_admin(current_user)

    db_cursor.execute("SELECT * FROM applications WHERE id = ?", (app_id,))
    app = db_cursor.fetchone()
    if not app:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    # Список групп для возможного ручного выбора
    db_cursor.execute(
        "SELECT id, name, min_age, max_age, swimming_year, shift, max_students FROM groups WHERE is_active = 1"
    )
    groups = db_cursor.fetchall()

    return templates.TemplateResponse(
        request,
        "admin_application_detail.html",
        {
            "request": request,
            "application": dict(app),
            "groups": groups,
            "current_user": current_user
        }
    )


# main.py - найдите функцию approve_application и замените её на эту

@app.post("/admin/application/{app_id}/approve")
async def approve_application(
        request: Request,
        app_id: int,
        group_id: Annotated[Optional[int], Form()] = None,
        current_user=Depends(get_current_user),
        db_cursor=Depends(get_db)
):
    require_admin(current_user)

    # Получаем заявку
    db_cursor.execute("SELECT * FROM applications WHERE id = ?", (app_id,))
    app = db_cursor.fetchone()
    if not app:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    # Если не выбран group_id, пробуем автоматический подбор
    if not group_id:
        group = auto_select_group(app["child_age"], app["swimming_years"], app["shift"], db_cursor)
        if not group:
            return templates.TemplateResponse(
                request,
                "admin_application_detail.html",
                {
                    "request": request,
                    "application": dict(app),
                    "groups": db_cursor.execute(
                        "SELECT id, name, min_age, max_age, swimming_year, shift, max_students FROM groups WHERE is_active = 1").fetchall(),
                    "error": "Автоматический подбор группы не удался (нет свободных мест или не подходит по параметрам). Пожалуйста, выберите группу вручную или создайте новую группу.",
                    "current_user": current_user
                },
                status_code=400
            )
        group_id = group["id"]

    # Проверяем, что выбранная группа существует и активна
    db_cursor.execute(
        "SELECT id, max_students FROM groups WHERE id = ? AND is_active = 1",
        (group_id,)
    )
    group = db_cursor.fetchone()
    if not group:
        raise HTTPException(status_code=400, detail="Выбранная группа не существует или неактивна")

    # Проверка на переполнение группы
    db_cursor.execute("SELECT COUNT(*) as cnt FROM enrollments WHERE group_id = ? AND is_active = 1", (group_id,))
    enrolled = db_cursor.fetchone()["cnt"]
    if enrolled >= group["max_students"]:
        raise HTTPException(status_code=400, detail="В группе нет свободных мест")

    # Начинаем создание родителя и ребёнка
    # Проверяем, существует ли родитель с таким телефоном
    db_cursor.execute("SELECT id FROM parents WHERE phone = ?", (app["parent_phone"],))
    parent = db_cursor.fetchone()

    from init_db import hash_password  # Импортируем функцию хеширования

    if not parent:
        # Создаём родителя с хешированным паролем (пароль = телефон)
        hashed_password = hash_password(app["parent_phone"])  # ХЕШИРУЕМ ПАРОЛЬ!
        db_cursor.execute(
            """INSERT INTO parents (full_name, phone, email, password_hash) 
               VALUES (?, ?, ?, ?)""",
            (app["parent_full_name"], app["parent_phone"], app["parent_email"], hashed_password)
        )
        parent_id = db_cursor.lastrowid
        print(f"✅ Создан родитель {app['parent_full_name']} с хешированным паролем")
    else:
        parent_id = parent["id"]
        # Проверяем, есть ли у родителя пароль, если нет - устанавливаем
        db_cursor.execute("SELECT password_hash FROM parents WHERE id = ?", (parent_id,))
        parent_data = db_cursor.fetchone()
        if not parent_data["password_hash"]:
            hashed_password = hash_password(app["parent_phone"])
            db_cursor.execute(
                "UPDATE parents SET password_hash = ? WHERE id = ?",
                (hashed_password, parent_id)
            )
            print(f"✅ Обновлен пароль для родителя {parent_id}")

    # Создаём ребёнка
    db_cursor.execute(
        """
        INSERT INTO children
        (parent_id, full_name, age, class_number, school_name, swimming_years, shift, desired_lessons_per_week)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (parent_id, app["child_full_name"], app["child_age"], app["child_class"],
         app["school_name"], app["swimming_years"], app["shift"], app["desired_lessons_per_week"])
    )
    child_id = db_cursor.lastrowid

    # Зачисляем в группу
    db_cursor.execute(
        "INSERT INTO enrollments (child_id, group_id, enrolled_at) VALUES (?, ?, ?)",
        (child_id, group_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )

    # Обновляем статус заявки
    db_cursor.execute(
        "UPDATE applications SET status = 'approved', processed_at = ?, processed_by = ? WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_user["id"], app_id)
    )
    db_cursor.connection.commit()

    # Отправляем уведомление родителю
    group_info = db_cursor.execute("SELECT name FROM groups WHERE id = ?", (group_id,)).fetchone()
    send_notification_to_parent(
        parent_id,
        "Заявка одобрена",
        f"Ваш ребёнок {app['child_full_name']} зачислен в группу '{group_info['name']}'. Расписание занятий доступно в личном кабинете.",
        db_cursor
    )

    return RedirectResponse(url="/admin/applications?success=approved", status_code=303)


@app.post("/admin/application/{app_id}/reject")
async def reject_application(
    request: Request,
    app_id: int,
    reason: Annotated[str, Form()],
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)

    db_cursor.execute(
        "UPDATE applications SET status = 'rejected', rejection_reason = ?, processed_at = ?, processed_by = ? WHERE id = ?",
        (reason, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_user["id"], app_id)
    )
    db_cursor.connection.commit()

    # Уведомление родителю (если родитель уже есть в базе, но заявка могла быть без авторизации)
    # Просто отправим уведомление на телефон/email (заглушка)
    db_cursor.execute("SELECT parent_phone, parent_email FROM applications WHERE id = ?", (app_id,))
    app = db_cursor.fetchone()
    if app:
        # Здесь можно отправить SMS/email
        print(f"📢 Отказ заявки {app_id}: {reason}")

    return RedirectResponse(url="/admin/applications?success=rejected", status_code=303)


# ---------- Управление тренерами ----------

@app.get("/admin/trainers", response_class=HTMLResponse)
async def admin_trainers(
    request: Request,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)
    db_cursor.execute("SELECT id, full_name, phone, email, login, specialization, is_active FROM trainers")
    trainers = db_cursor.fetchall()
    return templates.TemplateResponse(
        request,
        "admin_trainers.html",
        {
            "request": request,
            "trainers": trainers,
            "current_user": current_user
        }
    )


@app.get("/admin/trainer/create", response_class=HTMLResponse)
async def create_trainer_form(
    request: Request,
    current_user = Depends(get_current_user)
):
    require_admin(current_user)
    return templates.TemplateResponse(request, "admin_trainer_form.html", {"request": request, "current_user": current_user})


@app.post("/admin/trainer/create")
async def create_trainer(
    request: Request,
    full_name: Annotated[str, Form()],
    phone: Annotated[str, Form()],
    email: Annotated[str, Form()],
    login: Annotated[str, Form()],
    password: Annotated[str, Form()],
    specialization: Annotated[str, Form()] = "",
    is_active: Annotated[bool, Form()] = True,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)

    # Проверка уникальности логина
    db_cursor.execute("SELECT id FROM trainers WHERE login = ?", (login,))
    if db_cursor.fetchone():
        raise HTTPException(status_code=400, detail="Тренер с таким логином уже существует")

    db_cursor.execute(
        """
        INSERT INTO trainers (full_name, phone, email, login, password_hash, specialization, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (full_name, phone, email, login, password, specialization, is_active)
    )
    db_cursor.connection.commit()
    return RedirectResponse(url="/admin/trainers?success=created", status_code=303)


@app.get("/admin/trainer/{trainer_id}/edit", response_class=HTMLResponse)
async def edit_trainer_form(
    request: Request,
    trainer_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)
    db_cursor.execute("SELECT * FROM trainers WHERE id = ?", (trainer_id,))
    trainer = db_cursor.fetchone()
    if not trainer:
        raise HTTPException(status_code=404, detail="Тренер не найден")
    return templates.TemplateResponse(
        request,
        "admin_trainer_edit.html",
        {"request": request, "trainer": dict(trainer), "current_user": current_user}
    )


@app.post("/admin/trainer/{trainer_id}/edit")
async def update_trainer(
    request: Request,
    trainer_id: int,
    full_name: Annotated[str, Form()],
    phone: Annotated[str, Form()],
    email: Annotated[str, Form()],
    login: Annotated[str, Form()],
    specialization: Annotated[str, Form()] = "",
    is_active: Annotated[bool, Form()] = True,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)

    # Если меняется логин, проверяем уникальность
    db_cursor.execute("SELECT id FROM trainers WHERE login = ? AND id != ?", (login, trainer_id))
    if db_cursor.fetchone():
        raise HTTPException(status_code=400, detail="Логин уже используется другим тренером")

    db_cursor.execute(
        """
        UPDATE trainers
        SET full_name = ?, phone = ?, email = ?, login = ?, specialization = ?, is_active = ?
        WHERE id = ?
        """,
        (full_name, phone, email, login, specialization, is_active, trainer_id)
    )
    db_cursor.connection.commit()
    return RedirectResponse(url="/admin/trainers?success=updated", status_code=303)


@app.post("/admin/trainer/{trainer_id}/delete")
async def delete_trainer(
    request: Request,
    trainer_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    if trainer_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")

    # При удалении тренера снимаем привязку групп (trainer_id -> NULL)
    db_cursor.execute("UPDATE groups SET trainer_id = NULL WHERE trainer_id = ?", (trainer_id,))
    db_cursor.execute("DELETE FROM trainers WHERE id = ?", (trainer_id,))
    db_cursor.connection.commit()
    return RedirectResponse(url="/admin/trainers?success=deleted", status_code=303)


# ---------- Управление группами ----------

@app.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups(
    request: Request,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)
    db_cursor.execute(
        """
        SELECT g.*, t.full_name as trainer_name,
               (SELECT COUNT(*) FROM enrollments WHERE group_id = g.id AND is_active = 1) as enrolled
        FROM groups g
        LEFT JOIN trainers t ON g.trainer_id = t.id
        ORDER BY g.name
        """
    )
    groups = db_cursor.fetchall()
    # Список тренеров для выпадающего списка
    db_cursor.execute("SELECT id, full_name FROM trainers WHERE is_active = 1")
    trainers = db_cursor.fetchall()
    return templates.TemplateResponse(
        request,
        "admin_groups.html",
        {
            "request": request,
            "groups": groups,
            "trainers": trainers,
            "current_user": current_user
        }
    )


@app.get("/admin/group/create", response_class=HTMLResponse)
async def create_group_form(
    request: Request,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)
    db_cursor.execute("SELECT id, full_name FROM trainers WHERE is_active = 1")
    trainers = db_cursor.fetchall()
    return templates.TemplateResponse(
        request,
        "admin_group_form.html",
        {"request": request, "trainers": trainers, "current_user": current_user}
    )


@app.post("/admin/group/create")
async def create_group(
    request: Request,
    name: Annotated[str, Form()],
    trainer_id: Annotated[Optional[int], Form()] = None,
    min_age: Annotated[int, Form()] = 3,
    max_age: Annotated[int, Form()] = 17,
    swimming_year: Annotated[int, Form()] = 1,
    max_students: Annotated[int, Form()] = 15,
    shift: Annotated[str, Form()] = "day",
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)
    db_cursor.execute(
        """
        INSERT INTO groups (name, trainer_id, min_age, max_age, swimming_year, max_students, shift)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, trainer_id, min_age, max_age, swimming_year, max_students, shift)
    )
    db_cursor.connection.commit()
    return RedirectResponse(url="/admin/groups?success=created", status_code=303)


@app.get("/admin/group/{group_id}/edit", response_class=HTMLResponse)
async def edit_group_form(
    request: Request,
    group_id: int,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)
    db_cursor.execute("SELECT * FROM groups WHERE id = ?", (group_id,))
    group = db_cursor.fetchone()
    if not group:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    db_cursor.execute("SELECT id, full_name FROM trainers WHERE is_active = 1")
    trainers = db_cursor.fetchall()
    return templates.TemplateResponse(
        request,
        "admin_group_edit.html",
        {"request": request, "group": dict(group), "trainers": trainers, "current_user": current_user}
    )


@app.post("/admin/group/{group_id}/edit")
async def update_group(
    request: Request,
    group_id: int,
    name: Annotated[str, Form()],
    trainer_id: Annotated[Optional[int], Form()] = None,
    min_age: Annotated[int, Form()] = 3,
    max_age: Annotated[int, Form()] = 17,
    swimming_year: Annotated[int, Form()] = 1,
    max_students: Annotated[int, Form()] = 15,
    shift: Annotated[str, Form()] = "day",
    is_active: Annotated[bool, Form()] = True,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)
    db_cursor.execute(
        """
        UPDATE groups
        SET name = ?, trainer_id = ?, min_age = ?, max_age = ?, swimming_year = ?, max_students = ?, shift = ?, is_active = ?
        WHERE id = ?
        """,
        (name, trainer_id, min_age, max_age, swimming_year, max_students, shift, is_active, group_id)
    )
    db_cursor.connection.commit()
    return RedirectResponse(url="/admin/groups?success=updated", status_code=303)


@app.post("/admin/group/{group_id}/delete")
async def delete_group(
    request: Request,
    group_id: int,
    transfer_group_id: Annotated[Optional[int], Form()] = None,
    current_user = Depends(get_current_user),
    db_cursor = Depends(get_db)
):
    require_admin(current_user)

    # Проверяем, есть ли ученики в группе
    db_cursor.execute("SELECT COUNT(*) as cnt FROM enrollments WHERE group_id = ? AND is_active = 1", (group_id,))
    enrolled = db_cursor.fetchone()["cnt"]
    if enrolled > 0 and not transfer_group_id:
        # Нужно предложить перевести учеников
        raise HTTPException(status_code=400, detail="В группе есть ученики. Необходимо указать группу для перевода или отчислить их")

    if transfer_group_id:
        # Переводим всех активных учеников в другую группу
        db_cursor.execute(
            "UPDATE enrollments SET group_id = ? WHERE group_id = ? AND is_active = 1",
            (transfer_group_id, group_id)
        )
    else:
        # Отчисляем учеников (устанавливаем is_active = 0)
        db_cursor.execute("UPDATE enrollments SET is_active = 0 WHERE group_id = ?", (group_id,))

    # Удаляем группу
    db_cursor.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    db_cursor.connection.commit()
    return RedirectResponse(url="/admin/groups?success=deleted", status_code=303)


# Добавьте эти эндпоинты в main.py

@app.get("/admin/api/stats")
async def admin_api_stats(
        current_user=Depends(get_current_user),
        db_cursor=Depends(get_db)
):
    """API для получения статистики для админ-дашборда."""
    require_admin(current_user)

    # Новые заявки
    db_cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'new'")
    new_applications = db_cursor.fetchone()[0]

    # Активные группы
    db_cursor.execute("SELECT COUNT(*) FROM groups WHERE is_active = 1")
    active_groups = db_cursor.fetchone()[0]

    # Всего учеников (активные зачисления)
    db_cursor.execute("SELECT COUNT(DISTINCT child_id) FROM enrollments WHERE is_active = 1")
    total_students = db_cursor.fetchone()[0]

    # Активные тренеры
    db_cursor.execute("SELECT COUNT(*) FROM trainers WHERE is_active = 1")
    active_trainers = db_cursor.fetchone()[0]

    return {
        "new_applications": new_applications,
        "active_groups": active_groups,
        "total_students": total_students,
        "active_trainers": active_trainers
    }


@app.get("/admin/api/recent_applications")
async def admin_api_recent_applications(
        current_user=Depends(get_current_user),
        db_cursor=Depends(get_db)
):
    """API для получения последних заявок."""
    require_admin(current_user)

    db_cursor.execute(
        """
        SELECT id, parent_full_name, child_full_name, child_age, status, created_at
        FROM applications
        ORDER BY created_at DESC
        LIMIT 10
        """
    )
    rows = db_cursor.fetchall()
    # Преобразуем каждую заявку
    applications = []
    for row in rows:
        app = dict(row)
        # Преобразуем поле created_at (и возможно processed_at)
        if app.get('created_at') and hasattr(app['created_at'], 'strftime'):
            app['created_at'] = app['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        if app.get('processed_at') and hasattr(app['processed_at'], 'strftime'):
            app['processed_at'] = app['processed_at'].strftime('%Y-%m-%d %H:%M:%S')
        applications.append(app)

    return {
        "applications": [dict(app) for app in applications]
    }



# ======================== ДАШБОРД (ПЕРЕНАПРАВЛЯЕТ ПОЛЬЗОВАТЕЛЯ В ЗАВИСИМОСТИ ОТ РОЛИ) ========================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_redirect(
    request: Request,
    current_user = Depends(get_current_user)
):
    """Редирект на соответствующий дашборд в зависимости от роли."""
    if not current_user:
        return RedirectResponse(url="/login", status_code=303)
    if current_user["type"] == "parent":
        return RedirectResponse(url="/parent/profile", status_code=303)
    elif current_user["type"] == "trainer":
        return RedirectResponse(url="/trainer/dashboard", status_code=303)
    elif current_user["type"] == "admin":
        # Для админа показываем страницу со ссылками на все разделы
        return templates.TemplateResponse(request, "admin_dashboard.html", {"request": request, "current_user": current_user})
    else:
        return RedirectResponse(url="/login", status_code=303)


# Добавьте в main.py (после других эндпоинтов)

@app.get("/api/vk/auth")
async def vk_auth_callback(
        request: Request,
        code: Optional[str] = Query(None),
        db_cursor=Depends(get_db)
):
    """Callback для VK ID авторизации (OAuth)"""
    if not code:
        return RedirectResponse(url="/login?error=vk_auth_failed")

    # Здесь будет обмен code на access_token
    # Пока возвращаем заглушку
    return {"status": "success", "message": "VK auth callback received"}


@app.post("/api/vk/webhook")
async def vk_webhook(
        request: Request,
        db_cursor=Depends(get_db)
):
    """Webhook для VK Callback API (альтернатива LongPoll)"""
    try:
        data = await request.json()

        # Проверка на подтверждение сервера
        if data.get("type") == "confirmation":
            from config import VK_CONFIG
            return {"response": VK_CONFIG.get("confirmation_code", "000000")}

        # Обработка новых сообщений
        if data.get("type") == "message_new":
            from vk_bot import vk_bot_instance
            if vk_bot_instance:
                message = data["object"]["message"]
                user_id = message["from_id"]
                text = message.get("text", "")

                response = vk_bot_instance.handle_message(user_id, text)
                if response:
                    vk_bot_instance.send_message(user_id, response)

            return {"response": "ok"}

        return {"response": "ok"}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"response": "error"}