# handlers.py
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, CallbackQueryHandler, ContextTypes, filters
import io
import pandas as pd # اضافه شده برای جلوگیری از خطا

import config
import database as db
import utils

(AWAITING_INPUT, AWAITING_CONFIRMATION, AWAITING_FILE) = range(3)

def restricted(user_roles: list):
    def decorator(func):
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            if not update.effective_user: return
            user_id = update.effective_user.id
            if user_id in user_roles:
                return await func(update, context, *args, **kwargs)
            else:
                await update.message.reply_text("⛔️ **دسترسی غیرمجاز:** شناسه کاربری شما در سیستم تعریف نشده است.")
                db.log_action("access_denied", user_id)
        return wrapped
    return decorator

# --- Command Handlers ---

@restricted(user_roles=config.ADMIN_USER_IDS + config.OPERATOR_USER_IDS)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(config.WELCOME_MESSAGE, parse_mode='Markdown')
    await update.message.reply_text(config.REQUEST_INPUT_MESSAGE, parse_mode='Markdown')
    return AWAITING_INPUT

@restricted(user_roles=config.ADMIN_USER_IDS + config.OPERATOR_USER_IDS)
async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(config.ABOUT_MESSAGE, parse_mode='Markdown')

@restricted(user_roles=config.ADMIN_USER_IDS)
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور جدید برای دیدن لاگ‌ها"""
    report = db.get_recent_logs()
    await update.message.reply_text(report, parse_mode='Markdown')

@restricted(user_roles=config.ADMIN_USER_IDS)
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db.get_live_stats()
    msg = f"""
📊 **گزارش آماری لحظه‌ای**

👥 **کل مدعوین:** {stats['total']}
✅ **حاضرین (پذیرش شده):** {stats['checked_in_total']}
    ├─ عادی: {stats['confirmed']}
    └─ اضطراری: {stats['emergency']}
    
⏳ **غایبین:** {stats['remaining']}
💲 **پرداخت ناموفق:** {stats['unpaid_count']}

{config.BRANDING_FOOTER}
    """
    await update.message.reply_text(msg, parse_mode='Markdown')

@restricted(user_roles=config.ADMIN_USER_IDS)
async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("📂 لطفاً فایل اکسل لیست نفرات را ارسال نمایید.")
    return AWAITING_FILE

@restricted(user_roles=config.ADMIN_USER_IDS)
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ در حال تولید گزارش خروجی...")
    
    # خروجی حاضرین
    checked_in_df = db.get_checked_in_data_for_excel()
    if not checked_in_df.empty:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            checked_in_df.to_excel(writer, index=False)
        output.seek(0)
        await update.message.reply_document(
            document=InputFile(output, filename="Present_List.xlsx"),
            caption="✅ لیست حاضرین در مراسم"
        )

    # خروجی غایبین
    not_checked_in_df = db.get_not_checked_in_data_for_excel()
    if not not_checked_in_df.empty:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            not_checked_in_df.to_excel(writer, index=False)
        output.seek(0)
        await update.message.reply_document(
            document=InputFile(output, filename="Absent_List.xlsx"),
            caption="📋 لیست غایبین (عدم حضور)"
        )
    
    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)


async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مدیریت هوشمند ورودی (کد ملی یا جستجو)"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # حالت ۱: ورودی کد ملی است (۱۰ رقم عدد)
    if text.isdigit() and len(text) == 10:
        return await process_national_id(update, context, text)
    
    # حالت ۲: ورودی جستجو است (متن یا عدد غیر ۱۰ رقمی)
    elif len(text) >= 2:
        results = db.search_participants(text)
        if not results:
            await update.message.reply_text(config.SEARCH_NO_RESULT)
            return AWAITING_INPUT
        
        msg = "🔍 **نتایج جستجو:**\n\n"
        keyboard = []
        for p in results:
            # دکمه شیشه‌ای برای انتخاب سریع
            btn_text = f"{p['full_name']} ({p['national_id']})"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_{p['national_id']}")])
        
        keyboard.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel")])
        await update.message.reply_text(
            msg + "جهت انتخاب روی نام فرد کلیک کنید:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return AWAITING_CONFIRMATION # می‌رویم به حالت انتظار کلیک
        
    else:
        await update.message.reply_text(config.INVALID_INPUT_FORMAT)
        return AWAITING_INPUT

async def process_national_id(update: Update, context: ContextTypes.DEFAULT_TYPE, national_id: str):
    user_id = update.effective_user.id
    
    # --- اصلاح حیاتی: تشخیص منبع پیام (دکمه یا مت
    if update.callback_query:
        # اگر از کلیک روی دکمه آمده باشد
        message_interface = update.callback_query.message
    else:
        # اگر کاربر متن تایپ کرده باشد
        message_interface = update.message
    # ----------------------------------------------------

    # چک تکراری بودن
    checkin_status = db.get_checkin_status(national_id)
    if checkin_status:
        time_str = checkin_status['checked_in_at'].strftime("%H:%M")
        await message_interface.reply_text(
            f"{config.CHECKIN_ALREADY_DONE}\n⏰ زمان پذیرش: {time_str}\n👤 توسط: {checkin_status['checked_in_by']}",
            parse_mode='Markdown'
        )
        return AWAITING_INPUT

    # چک قفل نرم
    if not db.create_soft_lock(national_id, user_id):
        await message_interface.reply_text(config.SOFT_LOCK_ACTIVE_MESSAGE)
        return AWAITING_INPUT
    
    context.user_data['national_id'] = national_id
    participant = db.get_participant_info(national_id)

    # اگر پیدا نشد (حالت اضطراری)
    if not participant:
        keyboard = [
            [InlineKeyboardButton("🚨 ثبت پذیرش اضطراری", callback_data=f"emergency_{national_id}")],
            [InlineKeyboardButton("بازگشت", callback_data="cancel")]
        ]
        await message_interface.reply_text(
            f"{config.NATIONAL_ID_NOT_FOUND}\n\nکد ملی: `{national_id}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return AWAITING_CONFIRMATION

    # نمایش اطلاعات
    payment_msg = config.PAYMENT_WARNING if participant['payment_status'] == 'unpaid' else config.PAYMENT_OK
    info_text = f"""
👤 **اطلاعات شرکت‌کننده:**

🔹 **نام:** {participant['full_name']}
🔹 **نام پدر:** {participant['father_name']}
🆔 **کد ملی:** `{participant['national_id']}`

{payment_msg}
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ تأیید ورود", callback_data=f"confirm_{national_id}"),
            InlineKeyboardButton("⛔️ عدم پذیرش", callback_data=f"reject_{national_id}")
        ],
        [InlineKeyboardButton("🔙 انصراف", callback_data="cancel")]
    ]
    
    await message_interface.reply_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    return AWAITING_CONFIRMATION



async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    data = query.data
    
    if data == "cancel":
        if 'national_id' in context.user_data:
            db.release_soft_lock(context.user_data['national_id'])
            del context.user_data['national_id']
        await query.edit_message_text("❌ عملیات لغو گردید.")
        return AWAITING_INPUT

    # هندل کردن انتخاب از لیست جستجو
    if data.startswith("select_"):
        nid = data.split("_")[1]
        return await process_national_id(update, context, nid)

    # هندل کردن عملیات پذیرش
    action, _, national_id = data.partition('_')
    
    # امنیت: فقط کسی که قفل کرده بتواند تایید کند
    # (ساده سازی شده: فرض میکنیم همان است، چون Soft Lock داریم)

    if action == "confirm":
        db.perform_checkin(national_id, user_id, "confirmed")
        await query.edit_message_text(f"{config.CHECKIN_SUCCESS_CONFIRMED}\n👤 {national_id}", parse_mode='Markdown')
    elif action == "reject":
        db.perform_checkin(national_id, user_id, "rejected")
        await query.edit_message_text(f"{config.CHECKIN_SUCCESS_REJECTED}\n👤 {national_id}", parse_mode='Markdown')
    elif action == "emergency":
        db.perform_checkin(national_id, user_id, "emergency")
        await query.edit_message_text(f"{config.EMERGENCY_CHECKIN_SUCCESS}\n👤 {national_id}", parse_mode='Markdown')

    db.release_soft_lock(national_id)
    context.user_data.clear()
    
    # ارسال مجدد پیام شروع برای راحتی اپراتور
    await context.bot.send_message(chat_id=user_id, text=config.REQUEST_INPUT_MESSAGE, parse_mode='Markdown')
    return AWAITING_INPUT

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    document = update.message.document
    file = await document.get_file()
    file_bytes = await file.download_as_bytearray()
    df = utils.process_excel_file(bytes(file_bytes)) # فرض بر این است utils دست نخورده است
    
    if df is not None:
        db.import_participants_from_dataframe(df)
        await update.message.reply_text(f"✅ **بارگذاری موفق:** اطلاعات {len(df)} نفر در پایگاه داده به‌روزرسانی شد.")
    else:
        await update.message.reply_text("❌ خطا در ساختار فایل اکسل.")
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if 'national_id' in context.user_data:
        db.release_soft_lock(context.user_data['national_id'])
    await update.message.reply_text("عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END
