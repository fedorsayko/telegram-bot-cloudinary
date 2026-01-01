import os
import telebot
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import json
from flask import Flask, request
import logging
import cloudinary
import cloudinary.uploader
from telebot import types, apihelper
import time

# =================== НАСТРОЙКИ ТАЙМАУТОВ ===================
apihelper.CONNECT_TIMEOUT = 60
apihelper.READ_TIMEOUT = 60

# =================== НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ===================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GOOGLE_SHEETS_KEY = os.environ.get('GOOGLE_SHEETS_KEY')
TIMEZONE_OFFSET = int(os.environ.get('TIMEZONE_OFFSET', 3))
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')

CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET')

bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=True)
app = Flask(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =================== НАСТРОЙКА CLOUDINARY ===================
if all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]):
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True
    )
else:
    logger.warning("⚠️ Ключи Cloudinary не настроены.")

# =================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===================
def get_current_datetime():
    utc_now = datetime.utcnow()
    time_delta = timedelta(hours=TIMEZONE_OFFSET)
    local_now = utc_now + time_delta
    date_iso = local_now.date().isoformat()
    date_str = local_now.strftime("%d.%m.%Y")
    time_str = local_now.strftime("%H_%M_%S")
    display_time = local_now.strftime("%H:%M")
    return date_iso, date_str, time_str, display_time

def get_username(user):
    if user.username: return f"@{user.username}"
    return f"{user.first_name} {user.last_name or ''}".strip() or f"id_{user.id}"

# =================== GOOGLE SHEETS ФУНКЦИИ ===================
def connect_to_sheets():
    try:
        if not GOOGLE_CREDENTIALS_JSON: return None
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict, ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/spreadsheets']
        )
        client = gspread.authorize(creds)
        return client.open_by_key(GOOGLE_SHEETS_KEY).sheet1
    except Exception as e:
        logger.error(f"Ошибка Sheets: {e}")
        return None

# =================== ОБРАБОТЧИКИ ===================
@bot.message_handler(commands=['start'])
def handle_start(message):
    # Удаляем все кнопки и подсказки интерфейса
    bot.send_message(
        message.chat.id, 
        "📊 Бот готов к работе.\nПример: `150 Кофе` или отправьте фото.", 
        reply_markup=types.ReplyKeyboardRemove()
    )

@bot.message_handler(content_types=['text'])
def handle_text(message):
    try:
        parts = message.text.split(' ', 1)
        if len(parts) < 2:
            bot.reply_to(message, "❌ Формат: <сумма> <категория>")
            return

        amount = float(parts[0].replace(',', '.'))
        category = parts[1].strip()
        
        date_iso, _, _, display_time = get_current_datetime()
        sheet = connect_to_sheets()
        
        if sheet:
            row = [get_username(message.from_user), date_iso, display_time, amount, category]
            sheet.append_row(row, value_input_option='USER_ENTERED')
            bot.reply_to(message, f"✅ Записано: {amount} в {category}")
        else:
            bot.reply_to(message, "❌ Ошибка подключения к таблице")
            
    except ValueError:
        bot.reply_to(message, "❌ Сумма должна быть числом")
    except Exception as e:
        logger.error(f"Ошибка текста: {e}")
        bot.reply_to(message, "❌ Произошла ошибка при сохранении")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    processing_msg = bot.reply_to(message, "⏳ Загрузка фото...")
    try:
        date_iso, date_str, time_str, display_time = get_current_datetime()
        username = get_username(message.from_user)
        
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        public_id = f"{username}_{date_str}_{time_str}".replace('@', '')
        upload_result = cloudinary.uploader.upload(
            downloaded_file, 
            public_id=public_id, 
            folder="telegram_bot"
        )
        file_url = upload_result.get('secure_url')

        sheet = connect_to_sheets()
        if sheet:
            row = [username, date_iso, display_time, 0, "фото", file_url]
            sheet.append_row(row, value_input_option='USER_ENTERED')
            bot.edit_message_text(f"✅ Фото сохранено!\n🔗 {file_url}", message.chat.id, processing_msg.message_id)
        else:
            bot.edit_message_text("❌ Фото в облаке, но ошибка в Sheets", message.chat.id, processing_msg.message_id)
            
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        bot.edit_message_text(f"❌ Ошибка обработки фото", message.chat.id, processing_msg.message_id)

# =================== FLASK & WEBHOOK ===================
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'Error', 400

@app.route('/')
def home(): 
    return "Bot is running", 200

# =================== ЗАПУСК ===================
if __name__ == '__main__':
    bot.remove_webhook()
    time.sleep(1)
    
    render_url = os.environ.get('RENDER_EXTERNAL_URL')
    if render_url:
        webhook_url = f"{render_url}/webhook"
        bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Webhook установлен на: {webhook_url}")
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)