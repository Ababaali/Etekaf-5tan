from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, ConversationHandler, CallbackQueryHandler, ContextTypes, filters
import io

import config
import database as db
import utils

# --- States for ConversationHandler ---
(AWAITING_NATIONAL_ID, AWAITING_CONFIRMATION, AWAITING_FILE) = range(3)


# --- Decorator for role-based access ---
def restricted(user_roles: list):
    def decorator(func):
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            if user_id in user_roles:
                return await func(update, context, *args, **kwargs)
            else:
                await update.message.reply_text("شما دسترسی لازم برای استفاده از این دستور را ندارید.")
                db.log_action("access_denied", user_id, details=f"Attempted to use {func.__name__}")
        return wrapped
    return decorator

# --- Command Handlers ---

@restricted(user_roles=config.ADMIN_USER_IDS + config.OPERATOR_USER_IDS)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handler for /start command."""
    await update.message.reply_text(config.WELCOME_MESSAGE)
    db.log_action("start_command", update.effective_user.id)
    return AWAITING_NATIONAL_ID

@restricted(user_roles=config.ADMIN_USER_IDS + config.OPERATOR_USER_IDS)
async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /about command."""
    await update.message.reply_text(config.ABOUT_MESSAGE)

@restricted(user_roles=config.ADMIN_USER_IDS)
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /stats command (Admin only)."""
    stats = db.get_live_stats()
    stats_message = f"""
📊 **آمار لحظه‌ای پذیرش**

- **کل ثبت‌نام اولیه:** {stats['total']} نفر
- **تعداد پذیرش شده:** {stats['checked_in_total']} نفر
    - (تایید شده: {stats['confirmed']} | اضطراری: {stats['emergency']})
- **تعداد باقی‌مانده:** {stats['remaining']} نفر
- **تعداد با پرداخت ناموفق:** {stats['unpaid_count']} نفر
    """
    await update.message.reply_text(stats_message, parse_mode='Markdown')
    db.log_action("stats_command", update.effective_user.id)
    
@restricted(user_roles=config.ADMIN_USER_IDS)
async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the file upload process (Admin only)."""
    await update.message.reply_text("لطفاً فایل اکسل ثبت‌نام اولیه را ارسال کنید.")
    return AWAITING_FILE

@restricted(user_roles=config.ADMIN_USER_IDS)
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exports checked-in and not-checked-in lists (Admin only)."""
    # Checked-in export
    checked_in_df = db.get_checked_in_data_for_excel()
    if not checked_in_df.empty:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            checked_in_df.to_excel(writer, index=False, sheet_name='Checked-In')
        output.seek(0)
        await update.message.reply_document(
            document=InputFile(output, filename="checked_in_list.xlsx"),
            caption="لیست افراد پذیرش شده"
        )

    # Not checked-in export
    not_checked_in_df = db.get_not_checked_in_data_for_excel()
    if not not_checked_in_df.empty:
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            not_checked_in_df.to_excel(writer, index=False, sheet_name='Not-Checked-In')
        output.seek(0)
        await update.message.reply_document(
            document=InputFile(output, filename="not_checked_in_list.xlsx"),
            caption="لیست افراد باقی‌مانده (پذیرش نشده)"
        )
    
    if checked_in_df.empty and not_checked_in_df.empty:
        await update.message.reply_text("داده‌ای برای خروجی گرفتن وجود ندارد.")

    db.log_action("export_command", update.effective_user.id)


async def handle_national_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving a national ID."""
    user_id = update.effective_user.id
    national_id = update.message.text.strip()
    db.log_action("nid_entry", user_id, national_id)

    if not utils.validate_national_id(national_id):
        await update.message.reply_text(config.INVALID_NATIONAL_ID_FORMAT)
        return AWAITING_NATIONAL_ID

    if db.get_checkin_status(national_id):
        await update.message.reply_text(config.CHECKIN_ALREADY_DONE)
        db.log_action("nid_duplicate_checkin_attempt", user_id, national_id)
        return AWAITING_NATIONAL_ID

    if not db.create_soft_lock(national_id, user_id):
        await update.message.reply_text(config.SOFT_LOCK_ACTIVE_MESSAGE)
        db.log_action("nid_soft_lock_active", user_id, national_id)
        return AWAITING_NATIONAL_ID
    
    context.user_data['national_id'] = national_id
    participant = db.get_participant_info(national_id)

    if not participant:
        keyboard = [
            [InlineKeyboardButton("🆘 پذیرش اضطراری", callback_data=f"emergency_{national_id}")],
            [InlineKeyboardButton("لغو", callback_data="cancel")]
        ]
        await update.message.reply_text(
            config.NATIONAL_ID_NOT_FOUND,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return AWAITING_CONFIRMATION

    # Prepare participant info message
    payment_warning = "⚠️ وضعیت پرداخت: *ناموفق*" if participant['payment_status'] == 'unpaid' else "✅ وضعیت پرداخت: *موفق*"
    info_text = f"""
👤 **اطلاعات فرد:**

- **نام:** {participant['full_name']}
- **نام پدر:** {participant['father_name']}
- **کد ملی:** {participant['national_id']}

{payment_warning}
    """
    keyboard = [
        [
            InlineKeyboardButton("✅ تأیید پذیرش", callback_data=f"confirm_{national_id}"),
            InlineKeyboardButton("❌ رد پذیرش", callback_data=f"reject_{national_id}")
        ],
        [InlineKeyboardButton("لغو عملیات", callback_data="cancel")]
    ]
    await update.message.reply_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    return AWAITING_CONFIRMATION


async def handle_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles button clicks for confirmation, rejection, etc."""
    query = update.callback_query
    await query.answer()
    
    action, _, national_id = query.data.partition('_')
    user_id = query.from_user.id

    # Make sure the user clicking the button is the one who initiated the process
    if 'national_id' not in context.user_data or context.user_data['national_id'] != national_id:
        if action != 'cancel': # Allow anyone to cancel
           await query.edit_message_text(text="این درخواست توسط شما آغاز نشده است.")
           return AWAITING_CONFIRMATION

    if action == "confirm":
        db.perform_checkin(national_id, user_id, "confirmed")
        await query.edit_message_text(text=config.CHECKIN_SUCCESS_CONFIRMED)
    elif action == "reject":
        db.perform_checkin(national_id, user_id, "rejected")
        await query.edit_message_text(text=config.CHECKIN_SUCCESS_REJECTED)
    elif action == "emergency":
        db.perform_checkin(national_id, user_id, "emergency")
        await query.edit_message_text(text=config.EMERGENCY_CHECKIN_SUCCESS)
    elif action == "cancel":
        await query.edit_message_text(text="عملیات لغو شد.")
        db.log_action("action_canceled", user_id, national_id)

    # Release the lock regardless of the action
    db.release_soft_lock(national_id)
    context.user_data.clear() # Clear data for next person
    return AWAITING_NATIONAL_ID

async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving the Excel file."""
    user_id = update.effective_user.id
    document = update.message.document
    if not document or not document.file_name.endswith(('.xlsx', '.xls')):
        await update.message.reply_text("فرمت فایل نامعتبر است. لطفاً یک فایل اکسل (.xlsx) ارسال کنید.")
        return AWAITING_FILE

    file = await document.get_file()
    file_bytes = await file.download_as_bytearray()
    
    df = utils.process_excel_file(bytes(file_bytes))
    
    if df is None:
        await update.message.reply_text("خطا در پردازش فایل. لطفاً مطمئن شوید ستون‌های national_id, full_name, father_name, payment_status در فایل شما وجود دارند.")
        return AWAITING_FILE

    db.import_participants_from_dataframe(df)
    db.log_action("file_upload_success", user_id, details=f"Imported {len(df)} rows.")
    await update.message.reply_text(f"✅ فایل با موفقیت پردازش و {len(df)} رکورد در دیتابیس وارد/آپدیت شد.")
    
    return ConversationHandler.END # End the file upload conversation


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    if 'national_id' in context.user_data:
        db.release_soft_lock(context.user_data['national_id'])
        context.user_data.clear()

    await update.message.reply_text(
        "عملیات لغو شد.", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END
