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

# =================== НАСТРОЙКИ ===================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_SHEETS_KEY = os.environ.get('GOOGLE_SHEETS_KEY')
TIMEZONE_OFFSET = int(os.environ.get('TIMEZONE_OFFSET', 3))
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

# Cloudinary конфигурация (получаем из переменных окружения Render)
CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET')

# Проверка наличия критических переменных
if not all([TELEGRAM_TOKEN, GOOGLE_SHEETS_KEY, GOOGLE_CREDENTIALS_JSON]):
    raise ValueError("❌ Отсутствуют обязательные переменные: TELEGRAM_TOKEN, GOOGLE_SHEETS_KEY, GOOGLE_CREDENTIALS_JSON")

if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    print("⚠️  Предупреждение: Не настроены переменные Cloudinary. Загрузка фото будет невозможна.")

# Инициализация бота и Flask
bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Настройка Cloudinary (если все ключи есть)
if all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )
    logger.info("✅ Cloudinary настроен")
else:
    logger.warning("❌ Cloudinary не настроен. Фото загружаться не будут.")

# =================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===================
def get_current_datetime():
    """Получение текущей даты и времени"""
    utc_now = datetime.utcnow()
    time_delta = timedelta(hours=TIMEZONE_OFFSET)
    local_now = utc_now + time_delta
    date_str = local_now.strftime("%d.%m.%Y")
    time_str = local_now.strftime("%H_%M_%S")
    display_time = local_now.strftime("%H:%M")
    return date_str, time_str, display_time

def get_username(user):
    """Получение имени пользователя для записи"""
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        name = user.first_name
        if user.last_name:
            name += f" {user.last_name}"
        return name
    else:
        return f"user_{user.id}"

# =================== GOOGLE SHEETS ФУНКЦИИ ===================
def get_google_credentials():
    """Создание учетных данных Google из JSON строки"""
    try:
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

# =================== CLOUDINARY ФУНКЦИИ ===================
def upload_to_cloudinary(file_bytes, user_filename):
    """Загрузка файла на Cloudinary"""
    try:
        if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
            return None, "Cloudinary не настроен"
        
        # Загружаем файл в Cloudinary
        # folder="telegram_bot" создаст папку в интерфейсе Cloudinary
        result = cloudinary.uploader.upload(
            file_bytes,
            public_id=f"telegram_bot/{user_filename}",
            folder="telegram_bot"
        )
        
        # secure_url - HTTPS ссылка на файл
        file_url = result.get('secure_url')
        if not file_url:
            return None, "Cloudinary не вернул URL"
            
        return file_url, None
        
    except Exception as e:
        logger.error(f"Ошибка загрузки на Cloudinary: {e}")
        return None, str(e)

# =================== ОБРАБОТЧИКИ TELEGRAM ===================
@bot.message_handler(commands=['start'])
def handle_start(message):
    """Обработка команды /start"""
    welcome_text = """
👋 Привет! Я бот для учета расходов и документов.

📝 **Что я умею:**

1. 📊 **Записывать расходы** (текст)
   Формат: <сумма> <категория>
   Пример: `1500 продукты`

2. 📸 **Сохранять фото** 
   Просто отправь фото - оно сохранится в облаке

3. 📄 **Сохранять документы** (скоро)

💡 **Подсказки:**
/help - подробная справка
/status - проверить работу
"""
    bot.reply_to(message, welcome_text)
    logger.info(f"Пользователь {message.from_user.id} запустил бота")

@bot.message_handler(commands=['help'])
def handle_help(message):
    """Обработка команды /help"""
    help_text = """
📚 **Справка по использованию:**

💰 **Для записи расходов:**
`<сумма> <категория>`
Пример: `1500 продукты`, `250 такси`

🖼️ **Для загрузки фото:**
Просто отправьте фотографию (любого формата)

📁 **Файлы сохраняются:**
- В облачном хранилище Cloudinary
- Название: `Имя_Дата_Время.jpg`
- Ссылка сохраняется в таблицу

🔧 **Команды:**
/start - начать
/help - эта справка  
/status - проверить статус
"""
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['status'])
def handle_status(message):
    """Проверка статуса бота"""
    try:
        date_str, _, display_time = get_current_datetime()
        sheets_status = "✅" if connect_to_sheets() else "❌"
        cloudinary_status = "✅" if all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]) else "❌"
        
        status_text = f"""
🤖 **Статус бота:**

✅ Бот работает
📅 Дата: {date_str}
⏰ Время: {display_time}
🌍 Часовой пояс: UTC+{TIMEZONE_OFFSET}

📊 **Google Таблицы:** {sheets_status}
☁️ **Cloudinary:** {cloudinary_status}
"""
        bot.reply_to(message, status_text)
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка при проверке статуса: {e}")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    """Обработка текстовых сообщений с расходами"""
    try:
        text = message.text.strip()
        
        if text.startswith('/'):
            return
        
        parts = text.split(' ', 1)
        
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: <сумма> <категория>\nПример: `1500 продукты`")
            return
        
        amount_str, category = parts
        
        try:
            amount_str = amount_str.replace(',', '.')
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError
        except:
            bot.reply_to(message, "❌ Сумма должна быть положительным числом")
            return
        
        user = message.from_user
        username = get_username(user)
        date_str, _, display_time = get_current_datetime()
        
        sheet = connect_to_sheets()
        if not sheet:
            bot.reply_to(message, "❌ Ошибка подключения к таблице")
            return
        
        all_values = sheet.get_all_values()
        next_row = len(all_values) + 1
        
        data_to_write = [
            username,
            date_str,
            str(amount),
            category.strip()
        ]
        
        sheet.update(f'A{next_row}:D{next_row}', [data_to_write])
        
        response = f"""
✅ **Расход записан!**

👤 Пользователь: {username}
📅 Дата: {date_str}
💰 Сумма: {amount}
🏷️ Категория: {category}
⏰ Время: {display_time}
"""
        bot.reply_to(message, response)
        logger.info(f"Расход записан: {username} - {amount} - {category}")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")
        logger.error(f"Ошибка обработки текста: {e}")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    """Обработка фотографий для Cloudinary"""
    try:
        # Отправляем быстрый ответ
        msg = bot.reply_to(message, "🖼 Получил фото, обрабатываю...")
        
        user = message.from_user
        username = get_username(user)
        date_str, time_str, display_time = get_current_datetime()
        
        # Получаем фото (наибольшее доступное качество)
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Создаем имя файла
        filename = f"{username}_{date_str}_{time_str}.jpg"
        # Заменяем символы, которые могут вызвать проблемы
        safe_filename = filename.replace('@', '').replace('.', '_').replace(':', '_').replace(' ', '_')
        
        # Загружаем на Cloudinary
        file_url, error = upload_to_cloudinary(downloaded_file, safe_filename)
        
        if error:
            logger.error(f"Ошибка загрузки фото в Cloudinary: {error}")
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg.message_id,
                text="✅ Фото получено, но не удалось загрузить в облако."
            )
            return
        
        # Записываем информацию в таблицу Google Sheets
        sheet = connect_to_sheets()
        if sheet:
            try:
                all_values = sheet.get_all_values()
                next_row = len(all_values) + 1
                
                # Проверяем, есть ли колонка для ссылок (колонка E)
                if len(sheet.row_values(1)) < 5:
                    sheet.update('E1', [['Ссылка на файл']])
                
                data_to_write = [
                    username,
                    date_str,
                    "ФОТО",
                    safe_filename,
                    file_url
                ]
                
                sheet.update(f'A{next_row}:E{next_row}', [data_to_write])
                logger.info(f"Информация о фото записана в таблицу: {safe_filename}")
            except Exception as e:
                logger.warning(f"Не удалось записать в таблицу: {e}")
        
        # Отправляем финальный ответ пользователю
        response = f"✅ Фото сохранено в облако!\n📁 Файл: {safe_filename}\n🔗 Ссылка: {file_url}"
            
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg.message_id,
            text=response
        )
        logger.info(f"Фото успешно обработано: {safe_filename}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка в handle_photo: {e}", exc_info=True)
        try:
            bot.reply_to(message, "❌ Не удалось обработать фото. Попробуйте еще раз.")
        except:
            pass

# =================== FLASK РОУТЫ ===================
@app.route('/')
def home():
    """Главная страница для проверки работы сервиса"""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>🤖 Бот учета расходов и фото</title>
    <style>
        body { font-family: Arial, sans-serif; padding: 20px; max-width: 800px; margin: auto; }
        .card { background: #f5f5f5; padding: 20px; border-radius: 10px; margin: 20px 0; }
    </style>
</head>
<body>
    <h1>🤖 Telegram Бот для расходов и фото</h1>
    
    <div class="card">
        <h2>✅ Сервис работает</h2>
        <p>Бот готов принимать:</p>
        <ul>
            <li>📝 Записи расходов</li>
            <li>📸 Фотографии (сохраняются в Cloudinary)</li>
        </ul>
    </div>
    
    <div class="card">
        <h3>📊 Формат записи расходов:</h3>
        <code>&lt;сумма&gt; &lt;категория&gt;</code>
        <p>Пример: <code>1500 продукты</code></p>
    </div>
    
    <p><a href="/health">Проверить здоровье сервиса</a></p>
</body>
</html>
"""

@app.route('/health')
def health_check():
    """Endpoint для проверки здоровья сервиса"""
    return {"status": "healthy", "service": "telegram-bot-cloudinary"}, 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной endpoint для вебхуков от Telegram"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    else:
        return 'Bad Request', 400

@app.route('/set_webhook', methods=['GET'])
def set_webhook_manual():
    """Ручная установка вебхука (для отладки)"""
    # В Render обычно есть переменная RENDER_EXTERNAL_URL
    render_external_url = os.environ.get('RENDER_EXTERNAL_URL', '')
    
    if not render_external_url:
        return """
        <h1>⚠️ URL сервиса не определен</h1>
        <p>Установите переменную окружения RENDER_EXTERNAL_URL или используйте команду в браузере:</p>
        <code>https://api.telegram.org/botВАШ_ТОКЕН/setWebhook?url=https://ВАШ_СЕРВИС.onrender.com/webhook</code>
        <p><a href="/">Вернуться на главную</a></p>
        """, 400
    
    try:
        webhook_url = f"{render_external_url}/webhook"
        bot.remove_webhook()
        success = bot.set_webhook(url=webhook_url)
        
        if success:
            return f"""
            <h1>✅ Вебхук установлен!</h1>
            <p>URL: {webhook_url}</p>
            <p>Статус: Активен</p>
            <p><a href="/">Вернуться на главную</a></p>
            """, 200
        else:
            return """
            <h1>❌ Ошибка установки вебхука</h1>
            <p><a href="/">Вернуться на главную</a></p>
            """, 500
            
    except Exception as e:
        return f"""
        <h1>❌ Ошибка установки вебхука</h1>
        <p>Ошибка: {str(e)}</p>
        <p><a href="/">Вернуться на главную</a></p>
        """, 500

# =================== ЗАПУСК ПРИЛОЖЕНИЯ ===================
if __name__ == '__main__':
    logger.info("🚀 Бот с загрузкой в Cloudinary запускается...")
    
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"📡 Сервер запускается на порту {port}")
    
    app.run(host='0.0.0.0', port=port, debug=False)