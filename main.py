# main.py
from telegram.ext import Application, ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
import config
import handlers
import database as db

def main() -> None:
    db.initialize_database()

    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .base_url(f"{config.BALE_API_BASE_URL}bot")
        .base_file_url(f"{config.BALE_API_BASE_URL}file/bot")
        .build()
    )

    # هندلر اصلی پذیرش (شامل جستجو و کد ملی)
    checkin_conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", handlers.start_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_input) # ورودی متنی برای شروع
        ],
        states={
            handlers.AWAITING_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_input)
            ],
            handlers.AWAITING_CONFIRMATION: [
                CallbackQueryHandler(handlers.handle_callback)
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel)],
    )

    upload_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("upload", handlers.upload_command)],
        states={
            handlers.AWAITING_FILE: [MessageHandler(filters.Document.ALL, handlers.handle_file_upload)]
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel)],
    )
    
    application.add_handler(checkin_conv_handler)
    application.add_handler(upload_conv_handler)
    
    # دستورات اضافه شده
    application.add_handler(CommandHandler("about", handlers.about_command))
    application.add_handler(CommandHandler("stats", handlers.stats_command))
    application.add_handler(CommandHandler("export", handlers.export_command))
    application.add_handler(CommandHandler("logs", handlers.logs_command)) # دستور لاگ

    print(f"Bale Bot Started for {config.PROGRAM_TITLE}...")
    application.run_polling()

if __name__ == "__main__":
    main()
