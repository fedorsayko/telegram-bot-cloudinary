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
from telebot import types
import time

# =================== НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_SHEETS_KEY = os.environ.get('GOOGLE_SHEETS_KEY')
TIMEZONE_OFFSET = int(os.environ.get('TIMEZONE_OFFSET', 3))
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET')

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
else:
    logger.warning("⚠️ Ключи Cloudinary не настроены.")

# =================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===================
def get_current_datetime():
    utc_now = datetime.utcnow()
    time_delta = timedelta(hours=TIMEZONE_OFFSET)
    local_now = utc_now + time_delta
    date_str = local_now.strftime("%d.%m.%Y")
    date_iso = local_now.date().isoformat()    # Формат YYYY-MM-DD
    time_str = local_now.strftime("%H_%M_%S")
    display_time = local_now.strftime("%H:%M")
    return date_iso, date_str, time_str, display_time

def get_username(user):
    if user.username:
        return f"@{user.username}"
    elif user.first_name:
        name = user.first_name
        if user.last_name:
            name += f" {user.last_name}"
        return name
    return f"user_{user.id}"

def create_status_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    markup.add(types.KeyboardButton('Проверить статус'))
    return markup

def format_number_for_sheets(number):
    return str(number).replace('.', ',')

# =================== GOOGLE SHEETS ФУНКЦИИ ===================
def get_google_credentials():
    try:
        if not GOOGLE_CREDENTIALS_JSON:
            raise ValueError("GOOGLE_CREDENTIALS_JSON не установлен")
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        return ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict,
            ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/spreadsheets']
        )
    except Exception as e:
        logger.error(f"Ошибка создания Google credentials: {e}")
        return None

def connect_to_sheets():
    try:
        credentials = get_google_credentials()
        if not credentials: return None
        client = gspread.authorize(credentials)
        spreadsheet = client.open_by_key(GOOGLE_SHEETS_KEY)
        return spreadsheet.sheet1
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Таблицам: {e}")
        return None

def test_google_sheets_connection():
    try:
        start_time = time.time()
        sheet = connect_to_sheets()
        if sheet:
            sheet.row_values(1)
            elapsed_time = time.time() - start_time
            return True, f"✅ (ответ за {elapsed_time:.2f}с)"
        return False, "❌ (ошибка подключения)"
    except Exception as e:
        return False, f"❌ ({str(e)[:50]})"

def format_cell_for_google_sheets(value):
    if isinstance(value, float):
        return value if value != int(value) else int(value)
    return value

# =================== CLOUDINARY ФУНКЦИИ ===================
def upload_to_cloudinary(file_bytes, filename, username):
    try:
        if not all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
            return None, "Cloudinary не настроен"
        safe_username = username.replace('@', '').replace('.', '_').replace(' ', '_')
        public_id = f"telegram_bot/{safe_username}_{filename}"
        result = cloudinary.uploader.upload(file_bytes, public_id=public_id, folder="telegram_bot", resource_type="auto")
        return result.get('secure_url'), None
    except Exception as e:
        return None, str(e)

# =================== ОБРАБОТЧИКИ TELEGRAM КОМАНД ===================
@bot.message_handler(commands=['start'])
def handle_start(message):
    welcome_text = "👋 Привет! Я бот для учета расходов и фото.\n\n1. 📊 Расходы: <сумма> <категория>\n2. 📸 Фото: просто отправьте файл."
    bot.send_message(message.chat.id, welcome_text, reply_markup=create_status_keyboard())

@bot.message_handler(func=lambda message: message.text == 'Проверить статус')
def handle_status_button(message):
    handle_status(message)

@bot.message_handler(commands=['status'])
def handle_status(message):
    _, date_str, _, display_time = get_current_datetime()
    sheets_connected, sheets_msg = test_google_sheets_connection()
    status_text = f"🤖 Статус:\n📅 Дата: {date_str}\n⏰ Время: {display_time}\n📊 Sheets: {sheets_msg}"
    bot.send_message(message.chat.id, status_text, reply_markup=create_status_keyboard())

# =================== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ (РАСХОДЫ) ===================
@bot.message_handler(content_types=['text'])
def handle_text(message):
    if message.text.startswith('/') or message.text == 'Проверить статус':
        return
    try:
        text = message.text.strip()
        parts = text.split(' ', 1)
        if len(parts) != 2:
            bot.reply_to(message, "❌ Формат: <сумма> <категория>")
            return
        
        amount_str, category = parts
        amount = float(amount_str.replace(',', '.'))
        
        user = message.from_user
        username = get_username(user)
        date_iso, date_str, _, display_time = get_current_datetime()
        
        sheet = connect_to_sheets()
        if not sheet:
            bot.reply_to(message, "❌ Ошибка БД")
            return
        
        next_row = len(sheet.get_all_values()) + 1
        data_to_write = [username, date_iso, display_time, format_cell_for_google_sheets(amount), category.strip()]
        
        # КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: добавление value_input_option='USER_ENTERED'
        sheet.update(f'A{next_row}:E{next_row}', [data_to_write], value_input_option='USER_ENTERED')
        
        try:
            sheet.format(f"D{next_row}", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}})
            sheet.format(f"B{next_row}", {"numberFormat": {"type": "DATE", "pattern": "dd.mm.yyyy"}})
        except: pass
        
        bot.reply_to(message, f"✅ Записано: {amount} в {category}")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {str(e)}")

# =================== ОБРАБОТКА ФОТОГРАФИЙ ===================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        processing_msg = bot.reply_to(message, "🖼 Обработка фото...")
        user = message.from_user
        username = get_username(user)
        date_iso, date_str, time_str, display_time = get_current_datetime()
        
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        file_url, error = upload_to_cloudinary(downloaded_file, f"{date_str}_{time_str}.jpg", username)
        
        if error:
            bot.edit_message_text(f"❌ Ошибка Cloudinary: {error}", message.chat.id, processing_msg.message_id)
            return

        sheet = connect_to_sheets()
        if sheet:
            next_row = len(sheet.get_all_values()) + 1
            data_to_write = [username, date_iso, display_time, 0, "фото", file_url]
            
            # КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: добавление value_input_option='USER_ENTERED'
            sheet.update(f'A{next_row}:F{next_row}', [data_to_write], value_input_option='USER_ENTERED')
            
            try:
                sheet.format(f"B{next_row}", {"numberFormat": {"type": "DATE", "pattern": "dd.mm.yyyy"}})
            except: pass

        bot.edit_message_text(f"✅ Фото загружено!\n🔗 {file_url}", message.chat.id, processing_msg.message_id)
        bot.send_message(message.chat.id, "Что дальше?", reply_markup=create_status_keyboard())
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")

# =================== FLASK РОУТЫ И ЗАПУСК ===================
@app.route('/')
def home(): return "Бот работает!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Error', 400

@app.route('/set_webhook')
def set_webhook_manual():
    url = f"{os.environ.get('RENDER_EXTERNAL_URL', request.host_url.rstrip('/'))}/webhook"
    bot.remove_webhook()
    bot.set_webhook(url=url)
    return f"Webhook set to {url}", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)