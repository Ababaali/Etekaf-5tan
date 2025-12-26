from telegram.ext import Application, ConversationHandler, CommandHandler, MessageHandler, filters, CallbackQueryHandler
import config
import handlers
import database as db

def main() -> None:
    """Start the Bale bot."""
    # 1. Initialize the database (no change here)
    db.initialize_database()

    # 2. Create the Application and point it to the Bale API servers
    #    This is the core change for switching to Bale.
    #    We use the base_url from our config file.
    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .base_url(f"{config.BALE_API_BASE_URL}bot")
        .base_file_url(f"{config.BALE_API_BASE_URL}file/bot")
        .build()
    )

    # 3. Set up conversation handlers (no change in logic)
    # Conversation handler for the main check-in process
    checkin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", handlers.start_command)],
        states={
            handlers.AWAITING_NATIONAL_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.handle_national_id)
            ],
            handlers.AWAITING_CONFIRMATION: [
                CallbackQueryHandler(handlers.handle_confirmation_callback)
            ],
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel)],
    )

    # Conversation handler for file uploads
    upload_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("upload", handlers.upload_command)],
        states={
            handlers.AWAITING_FILE: [MessageHandler(filters.Document.ALL, handlers.handle_file_upload)]
        },
        fallbacks=[CommandHandler("cancel", handlers.cancel)],
    )
    
    application.add_handler(checkin_conv_handler)
    application.add_handler(upload_conv_handler)
    
    # 4. Add other command handlers (no change in logic)
    application.add_handler(CommandHandler("about", handlers.about_command))
    application.add_handler(CommandHandler("stats", handlers.stats_command))
    application.add_handler(CommandHandler("export", handlers.export_command))


    # 5. Run the bot (no change here)
    print("Bale bot is running... Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == "__main__":
    main()
