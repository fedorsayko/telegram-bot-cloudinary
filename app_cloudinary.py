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

# ... (остальные импорты и настройки остаются без изменений)

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
    return date_obj, date_str, time_str, display_time  # Возвращаем date_obj первым

# ... (остальные функции без изменений)

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
        date_obj, date_str, _, display_time = get_current_datetime()  # Получаем date_obj
        
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
            amount_for_sheets, # Передаем отформатированное значение для Google Таблиц
            category.strip()
        ]
        
        # Записываем данные в таблицу
        sheet.update(f'A{next_row}:E{next_row}', [data_to_write])
        
        # Форматируем ячейку с числом после записи
        try:
            # Устанавливаем числовой формат для только что записанной ячейки (столбец D)
            sheet.format(f"D{next_row}", {
                "numberFormat": {
                    "type": "NUMBER",
                    "pattern": "#,##0.00"
                }
            })
            
            # Также устанавливаем формат даты для ячейки B
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
📅 Дата: {date_str}  # Используем строковое представление для отображения
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
        date_obj, date_str, time_str, display_time = get_current_datetime()  # Получаем date_obj
        
        # Получаем фото наилучшего качества
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Создаем имя файла (используем строковое представление для имени файла)
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
                    display_time,  # Время отправки фото
                    0,             # Сумма = 0 (записываем как число, а не строку)
                    "фото",        # Категория = "фото" (вместо названия файла)
                    file_url
                ]
                
                sheet.update(f'A{next_row}:F{next_row}', [data_to_write])
                
                # Форматируем ячейку с нулем как число и дату как дату
                try:
                    sheet.format(f"D{next_row}", {
                        "numberFormat": {
                            "type": "NUMBER",
                            "pattern": "#,##0.00"
                        }
                    })
                    
                    # Устанавливаем формат даты для ячейки B
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
        
        # ... (остальной код функции без изменений)

# ... (остальной код без изменений)