import os
import telebot
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import json
from flask import Flask, request
import logging
import io
import cloudinary
import cloudinary.uploader
import cloudinary.api
from telebot import types  # Импорт types в начале файла

# =================== НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_SHEETS_KEY = os.environ.get('GOOGLE_SHEETS_KEY')
TIMEZONE_OFFSET = int(os.environ.get('TIMEZONE_OFFSET', 3))
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

# Ключи Cloudinary - ИСПРАВЛЕННЫЕ НАЗВАНИЯ ПЕРЕМЕННЫХ
CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY')  # Исправлено
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET')  # Исправлено

# Инициализация бота и Flask
bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# =================== НАСТРОЙКА ЛОГИРОВАНИЯ ===================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =================== НАСТРОЙКА CLOUDINARY ===================
if all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )
    logger.info("✅ Cloudinary настроен успешно")
    logger.info(f"Cloud name: {CLOUDINARY_CLOUD_NAME}")
    logger.info(f"API key present: {bool(CLOUDINARY_API_KEY)}")
else:
    logger.warning("⚠️ Ключи Cloudinary не настроены. Загрузка фото будет невозможна.")
    logger.warning(f"Проверьте переменные окружения:")
    logger.warning(f"CLOUDINARY_CLOUD_NAME: {CLOUDINARY_CLOUD_NAME}")
    logger.warning(f"CLOUDINARY_API_KEY: {CLOUDINARY_API_KEY}")
    logger.warning(f"CLOUDINARY_API_SECRET: {CLOUDINARY_API_SECRET}")

# =================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===================
def get_current_datetime():
    """Получение текущей даты и времени"""
    utc_now = datetime.utcnow()
    time_delta = timedelta(hours=TIMEZONE_OFFSET)
    local_now = utc_now + time_delta
    date_obj = local_now.date()  # Объект даты для Google Таблиц
    date_str = local_now.strftime("%d.%m.%Y")  # Строка для отображения
    time_str = local_now.strftime("%H_%M_%S")
    display_time = local_now.strftime("%H:%M")
    return date_obj, date_str, time_str, display_time

def get_username(user):
    """Получение имени пользователя"""
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        name = user.first_name
        if user.last_name:
            name += f" {user.last_name}"
        return name
    else:
        return f"user_{user.id}"

def create_status_keyboard():
    """Создание клавиатуры с кнопкой 'Проверить статус'"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    status_button = types.KeyboardButton('Проверить статус')
    markup.add(status_button)
    return markup

def format_number_for_sheets(number):
    """Форматирует число для записи в Google Таблицы (с запятой вместо точки)"""
    if isinstance(number, (int, float)):
        # Преобразуем число в строку с заменой точки на запятой
        return str(number).replace('.', ',')
    return str(number)

# =================== GOOGLE SHEETS ФУНКЦИИ ===================
def get_google_credentials():
    """Создание учетных данных Google"""
    try:
        if not GOOGLE_CREDENTIALS_JSON:
            raise ValueError("GOOGLE_CREDENTIALS_JSON не установлен")
        
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict,
            [
                'https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/spreadsheets'
            ]
        )
        return credentials
    except Exception as e:
        logger.error(f"Ошибка создания Google credentials: {e}")
        return None

def connect_to_sheets():
    """Подключение к Google Таблицам"""
    try:
        credentials = get_google_credentials()
        if not credentials:
            return None
            
        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(GOOGLE_SHEETS_KEY)
        sheet = spreadsheet.sheet1
        
        return sheet
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Таблицам: {e}")
        return None

def format_cell_for_google_sheets(value):
    """
    Форматирует значение для записи в Google Таблицы.
    Возвращает значение в правильном формате для записи.
    """
    if isinstance(value, (int, float)):
        # Для чисел с плавающей точкой
        if isinstance(value, float):
            # Проверяем, есть ли дробная часть
            if value == int(value):
                # Если число целое, записываем как целое
                return int(value)
            else:
                # Если есть дробная часть, записываем как float
                return float(value)
        else:
            # Целые числа
            return value
    else:
        # Для строк и других типов
        return value

# =================== CLOUDINARY ФУНКЦИИ ===================
def upload_to_cloudinary(file_bytes, filename, username):
    """Загрузка файла на Cloudinary"""
    try:
        if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
            return None, "Cloudinary не настроен"
        
        # Создаем уникальное имя файла с именем пользователя
        safe_username = username.replace('@', '').replace('.', '_').replace(' ', '_')
        public_id = f"telegram_bot/{safe_username}_{filename}"
        
        # Загружаем файл
        result = cloudinary.uploader.upload(
            file_bytes,
            public_id=public_id,
            folder="telegram_bot",
            resource_type="auto"  # Автоматически определяем тип ресурса
        )
        
        # Возвращаем URL файла
        file_url = result.get('secure_url')
        if file_url:
            logger.info(f"Фото успешно загружено на Cloudinary: {file_url}")
            return file_url, None
        else:
            logger.error("Cloudinary не вернул URL")
            return None, "Cloudinary не вернул URL"
            
    except Exception as e:
        logger.error(f"Ошибка загрузки на Cloudinary: {e}", exc_info=True)
        return None, str(e)

# =================== ОБРАБОТЧИКИ TELEGRAM КОМАНД ===================
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Обработка команды /start"""
    welcome_text = """
👋 Привет! Я бот для учета расходов и фото.

📝 **Что я умею:**
1. 📊 Записывать расходы: <сумма> <категория>
   Пример: 1500 продукты
2. 📸 Сохранять фото в облако
3. 📁 Хранить ссылки на все файлы в таблице

💡 **Доступные команды:**
/start - это сообщение
/help - справка по использованию
/status - проверить статус бота

📲 Или просто нажмите кнопку ниже ⬇️
"""
    bot.send_message(message.chat.id, welcome_text, reply_markup=create_status_keyboard())
    logger.info(f"Пользователь {message.from_user.id} запустил бота")

@bot.message_handler(func=lambda message: message.text == 'Проверить статус')
def handle_status_button(message):
    """Обработка нажатия на кнопку 'Проверить статус'"""
    handle_status(message)

@bot.message_handler(commands=['help'])
def handle_help(message):
    """Обработка команды /help"""
    help_text = """
📚 **Справка по использованию:**

💰 **Для записи расходов:**
<сумма> <категория>
Пример: 1500 продукты, 250 такси

🖼️ **Для загрузки фото:**
Просто отправьте фотографию любого формата

📊 **Данные сохраняются:**
• Расходы - в Google Таблицу
• Фото - в Cloudinary
• Ссылки на фото - в таблицу
"""
    bot.reply_to(message, help_text, reply_markup=create_status_keyboard())

@bot.message_handler(commands=['status'])
def handle_status(message):
    """Проверка статуса бота"""
    try:
        _, date_str, _, display_time = get_current_datetime()
        
        # Проверяем подключения
        sheets_connected = "✅" if connect_to_sheets() else "❌"
        cloudinary_connected = "✅" if all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]) else "❌"
        
        status_text = f"""
🤖 **Статус бота:**

🟢 Бот активен
📅 Дата: {date_str}
⏰ Время: {display_time}
🌍 Часовой пояс: UTC+{TIMEZONE_OFFSET}

🔗 **Подключения:**
📊 Google Таблицы: {sheets_connected}
☁️ Cloudinary: {cloudinary_connected}

💬 Бот готов к работе!
"""
        bot.reply_to(message, status_text, reply_markup=create_status_keyboard())
        logger.info(f"Пользователь {message.from_user.id} запросил статус")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при проверке статуса: {str(e)}"
        bot.reply_to(message, error_msg)
        logger.error(f"Ошибка в handle_status: {e}")

# =================== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ (РАСХОДЫ) ===================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    """Обработка сообщений с расходами"""
    # Игнорируем команды и кнопку статуса
    if message.text.startswith('/') or message.text == 'Проверить статус':
        return
    
    try:
        text = message.text.strip()
        
        # Разделяем сумму и категорию
        parts = text.split(' ', 1)
        
        if len(parts) != 2:
            error_msg = "❌ Неправильный формат.\n\nИспользуйте: <сумма> <категория>\nПример: 1500 продукты"
            bot.reply_to(message, error_msg, reply_markup=create_status_keyboard())
            return
        
        amount_str, category = parts
        
        # Проверяем и преобразуем сумму
        try:
            amount_str = amount_str.replace(',', '.')
            amount = float(amount_str)
            
            if amount <= 0:
                raise ValueError("Сумма должна быть больше 0")
                
        except ValueError:
            error_msg = "❌ Сумма должна быть положительным числом.\nПример: 1500 или 1500,50"
            bot.reply_to(message, error_msg, reply_markup=create_status_keyboard())
            return
        
        # Получаем данные пользователя
        user = message.from_user
        username = get_username(user)
        date_obj, date_str, _, display_time = get_current_datetime()
        
        # Подключаемся к Google Таблицам
        sheet = connect_to_sheets()
        if not sheet:
            error_msg = "❌ Ошибка подключения к Google Таблицы"
            bot.reply_to(message, error_msg, reply_markup=create_status_keyboard())
            return
        
        # Находим первую пустую строку
        all_values = sheet.get_all_values()
        next_row = len(all_values) + 1
        
        # Подготавливаем данные для записи
        # Для отображения пользователю форматируем с запятой
        amount_formatted_display = format_number_for_sheets(amount)
        
        # Для записи в таблицу используем функцию форматирования для Google Таблиц
        amount_for_sheets = format_cell_for_google_sheets(amount)
        
        # ЗАПИСЫВАЕМ ДАННЫЕ В ТАБЛИЦУ:
        # 1) Имя пользователя
        # 2) Дата (объект date) - теперь Google Таблицы распознают как дату
        # 3) Время
        # 4) Сумма
        # 5) Категория
        data_to_write = [
            username,
            date_obj,          # Используем объект date вместо строки
            display_time,      # Время отправки данных
            amount_for_sheets,  # Передаем отформатированное значение для Google Таблиц
            category.strip()
        ]
        
        # Записываем данные в таблицу
        sheet.update(f'A{next_row}:E{next_row}', [data_to_write])
        
        # Форматируем ячейки после записи
        try:
            # Устанавливаем числовой формат для суммы (столбец D)
            sheet.format(f"D{next_row}", {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "#,##0.00"
                }
            })
            
            # Устанавливаем формат даты (столбец B)
            sheet.format(f"B{next_row}", {
                "numberFormat": {
                    "type": "DATE",
                    "pattern": "dd.mm.yyyy"
                }
            })
        except Exception as e:
            logger.warning(f"Не удалось установить формат для ячеек: {e}")
        
        # Формируем ответ пользователю
        response = f"""
✅ **Расход успешно записан!**

👤 Пользователь: {username}
📅 Дата: {date_str}
⏰ Время: {display_time}
💰 Сумма: {amount_formatted_display}
🏷️ Категория: {category}

Данные сохранены в Google Таблицу.
"""
        bot.reply_to(message, response, reply_markup=create_status_keyboard())
        logger.info(f"✅ Расход записан: {username} - {amount_formatted_display} - {category}")
        
    except Exception as e:
        error_msg = f"❌ Произошла ошибка: {str(e)}\n\nПопробуйте еще раз."
        bot.reply_to(message, error_msg, reply_markup=create_status_keyboard())
        logger.error(f"Ошибка обработки текста: {e}")

# =================== ОБРАБОТКА ФОТОГРАФИЙ ===================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    """Обработка фотографий для Cloudinary"""
    try:
        # Отправляем быстрый ответ
        processing_msg = bot.reply_to(message, "🖼 Получил фото, начинаю обработку...")
        
        # Получаем данные пользователя
        user = message.from_user
        username = get_username(user)
        date_obj, date_str, time_str, display_time = get_current_datetime()
        
        # Получаем фото наилучшего качества
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Создаем имя файла
        filename = f"{date_str}_{time_str}.jpg"
        
        # Загружаем на Cloudinary
        file_url, error = upload_to_cloudinary(downloaded_file, filename, username)
        
        if error:
            logger.error(f"Ошибка загрузки фото: {error}")
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                text=f"✅ Фото получено, но не удалось загрузить в облако.\nОшибка: {error[:50]}..."
            )
            # Отправляем клавиатуру отдельным сообщением
            bot.send_message(message.chat.id, "Попробуйте еще раз.", reply_markup=create_status_keyboard())
            return
        
        # Сохраняем информацию в Google Таблицу
        sheet = connect_to_sheets()
        if sheet:
            try:
                # Проверяем заголовки
                headers = sheet.row_values(1)
                if len(headers) < 6:
                    sheet.update('F1', [['Ссылка на файл']])
                
                # Находим пустую строку
                all_values = sheet.get_all_values()
                next_row = len(all_values) + 1
                
                # ЗАПИСЫВАЕМ ДАННЫЕ В ТАБЛИЦУ:
                # 1) Имя пользователя
                # 2) Дата (объект date) - теперь Google Таблицы распознают как дату
                # 3) Время
                # 4) Сумма = 0 (как ЧИСЛО, а не строка)
                # 5) Категория = "фото"
                # 6) Ссылка на файл
                data_to_write = [
                    username,
                    date_obj,      # Используем объект date вместо строки
                    display_time,   # Время отправки фото
                    0,              # Сумма = 0 (записываем как число, а не строку)
                    "фото",         # Категория = "фото"
                    file_url
                ]
                
                sheet.update(f'A{next_row}:F{next_row}', [data_to_write])
                
                # Форматируем ячейки после записи
                try:
                    sheet.format(f"D{next_row}", {
                        "numberFormat": {
                            "type": "NUMBER",
                            "pattern": "#,##0.00"
                        }
                    })
                    
                    # Устанавливаем формат даты (столбец B)
                    sheet.format(f"B{next_row}", {
                        "numberFormat": {
                            "type": "DATE",
                            "pattern": "dd.mm.yyyy"
                        }
                    })
                except Exception as e:
                    logger.warning(f"Не удалось установить формат для ячеек: {e}")
                
                logger.info(f"Информация о фото записана в таблицу: {filename}")
                
            except Exception as e:
                logger.warning(f"Не удалось записать в таблицу (но фото загружено): {e}")
        
        # Отправляем финальный ответ
        success_msg = f"""
✅ Фото успешно загружено!

👤 Пользователь: {username}
📅 Дата: {date_str}
⏰ Время: {display_time}
🖼 Категория: фото
🔗 Ссылка: {file_url}

Фото доступно по ссылке выше.
"""
        # Редактируем сообщение без клавиатуры
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=processing_msg.message_id,
            text=success_msg
        )
        
        # Отправляем клавиатуру отдельным сообщением
        bot.send_message(message.chat.id, "Что дальше?", reply_markup=create_status_keyboard())
        logger.info(f"Фото успешно обработано: {filename}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка в handle_photo: {e}", exc_info=True)
        
        # Пытаемся отправить сообщение об ошибке
        try:
            bot.reply_to(message, "❌ Не удалось обработать фото. Попробуйте еще раз.", reply_markup=create_status_keyboard())
        except:
            pass

# =================== FLASK РОУТЫ ДЛЯ WEBHOOK ===================
@app.route('/')
def home():
    """Главная страница для проверки работы сервиса"""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>🤖 Telegram Бот для учета расходов</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            line-height: 1.6;
            background-color: #f5f5f5;
        }
        .container {
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            text-align: center;
        }
        .status {
            background: #4CAF50;
            color: white;
            padding: 15px;
            border-radius: 5px;
            text-align: center;
            margin: 20px 0;
        }
        .feature {
            background: #e8f5e9;
            padding: 15px;
            margin: 10px 0;
            border-radius: 5px;
            border-left: 4px solid #4CAF50;
        }
        code {
            background: #f1f1f1;
            padding: 2px 5px;
            border-radius: 3px;
            font-family: monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Telegram Бот для учета расходов и фото</h1>
        
        <div class="status">
            <h2>✅ Сервис работает нормально</h2>
            <p>Бот готов принимать сообщения через Telegram</p>
        </div>
        
        <div class="feature">
            <h3>📝 Запись расходов</h3>
            <p>Формат: <code>&lt;сумма&gt; &lt;категория&gt;</code></p>
            <p>Пример: <code>1500 продукты</code></p>
        </div>
        
        <div class="feature">
            <h3>📸 Загрузка фото</h3>
            <p>Просто отправьте фото боту — оно сохранится в облаке</p>
        </div>
        
        <div class="feature">
            <h3>📊 Хранение данных</h3>
            <p>• Расходы сохраняются в Google Таблицу</p>
            <p>• Фото загружаются в Cloudinary</p>
            <p>• Ссылки на фото хранятся в таблице</p>
        </div>
        
        <p style="text-align: center; margin-top: 30px;">
            <a href="/health">Проверить здоровье сервиса</a> | 
            <a href="/set_webhook">Настроить вебхук</a>
        </p>
    </div>
</body>
</html>
"""

@app.route('/health')
def health_check():
    """Проверка здоровья сервиса"""
    return {
        "status": "healthy",
        "service": "telegram-bot-cloudinary",
        "timestamp": datetime.utcnow().isoformat()
    }, 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной endpoint для вебхуков от Telegram"""
    if request.headers.get('content-type') == 'application/json':
        try:
            # Получаем обновление от Telegram
            json_string = request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_string)
            
            # Обрабатываем обновление
            bot.process_new_updates([update])
            
            logger.info("✅ Webhook успешно обработан")
            return 'OK', 200
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки webhook: {e}")
            return 'Error', 500
    else:
        logger.warning("❌ Неверный content-type в webhook")
        return 'Bad Request', 400

@app.route('/set_webhook', methods=['GET'])
def set_webhook_manual():
    """Страница для ручной настройки вебхука"""
    # Получаем текущий URL сервиса
    service_url = os.environ.get('RENDER_EXTERNAL_URL', '')
    
    if not service_url:
        # Пытаемся определить URL автоматически
        service_url = request.host_url.rstrip('/')
    
    webhook_url = f"{service_url}/webhook"
    
    try:
        # Устанавливаем вебхук
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url)
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Настройка вебхука</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 20px; }}
                .success {{ background: #d4edda; color: #155724; padding: 20px; border-radius: 5px; }}
                .info {{ background: #d1ecf1; color: #0c5460; padding: 15px; border-radius: 5px; margin: 20px 0; }}
                code {{ background: #f8f9fa; padding: 5px; border-radius: 3px; }}
            </style>
        </head>
        <body>
            <h1>⚙️ Настройка вебхука Telegram</h1>
            
            <div class="success">
                <h2>✅ Вебхук успешно установлен!</h2>
                <p><strong>URL вебхука:</strong></p>
                <p><code>{webhook_url}</code></p>
                <p>Теперь бот может получать сообщения от Telegram.</p>
            </div>
            
            <div class="info">
                <h3>🔧 Проверка вебхука</h3>
                <p>Вы можете проверить статус вебхука командой:</p>
                <p><code>https://api.telegram.org/bot[ВАШ_ТОКЕН]/getWebhookInfo</code></p>
                <p>Если бот не отвечает, убедитесь что вебхук установлен корректно.</p>
            </div>
            
            <p><a href="/">Вернуться на главную</a></p>
        </body>
        </html>
        """, 200
        
    except Exception as e:
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Ошибка настройки вебхука</title>
        </head>
        <body>
            <h1>❌ Ошибка настройки вебхука</h1>
            <p><strong>Ошибка:</strong> {str(e)}</p>
            <p><strong>Webhook URL:</strong> {webhook_url}</p>
            <p>Попробуйте установить вебхук вручную через браузер:</p>
            <p><code>https://api.telegram.org/bot[ВАШ_ТОКЕН]/setWebhook?url={webhook_url}</code></p>
            <p><a href="/">Вернуться на главную</a></p>
        </body>
</html>
        """, 500

# =================== ЗАПУСК ПРИЛОЖЕНИЯ ===================
if __name__ == '__main__':
    # Проверяем наличие обязательных переменных окружения
    required_vars = ['TELEGRAM_TOKEN', 'GOOGLE_SHEETS_KEY', 'GOOGLE_CREDENTIALS_JSON']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    
    if missing_vars:
        logger.error(f"❌ Отсутствуют обязательные переменные: {missing_vars}")
        logger.error("Добавьте эти переменные в настройках Render")
        exit(1)
    
    logger.info("=" * 50)
    logger.info("🚀 Запуск Telegram бота с Cloudinary")
    logger.info("=" * 50)
    
    # Пытаемся установить вебхук при запуске
    try:
        service_url = os.environ.get('RENDER_EXTERNAL_URL', '')
        if service_url:
            webhook_url = f"{service_url}/webhook"
            bot.remove_webhook()
            bot.set_webhook(url=webhook_url)
            logger.info(f"✅ Вебхук установлен: {webhook_url}")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось установить вебхук автоматически: {e}")
        logger.info("ℹ️ Вы можете установить вебхук вручную через /set_webhook")
    
    # Запускаем Flask приложение
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Сервер запускается на порту {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False
    )