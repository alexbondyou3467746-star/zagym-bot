", reply_markup=get_main_keyboard())
        context.user_data.clear()
        return ConversationHandler.END
    
    success, result = book_session(session_id, user_id, user_name, phone)
    
    if success:
        workout_type, day, time, remaining = result
        short_day = DAYS_SHORT.get(day, day)
        await update.message.reply_text(
            f"✅ **Вы записаны!**\n\n🏋️ {workout_type}\n📅 {short_day}\n⏰ {time.replace(':', '.')}\n📊 Осталось мест: {remaining}\n\nЖдем вас! 💪",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        try:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=f"📢 НОВАЯ ЗАПИСЬ 📢\n\n👤 {user_name}\n📞 {phone}\n🏋️ {workout_type}\n📆 {day}\n⏱️ {time.replace(':', '.')}", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Ошибка отправки в канал: {e}")
    else:
        await update.message.reply_text(f"❌ {result}", reply_markup=get_main_keyboard())
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=get_main_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

# --- ЗАПУСК ---
def main():
    try:
        init_database()
        populate_initial_data()
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
    
    app = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^(📝 Записаться)$'), handle_reply_buttons)],
        states={
            SELECTING_CLASS: [CallbackQueryHandler(handle_inline_buttons, pattern='^type_|^back_to_')],
            SELECTING_DATE: [CallbackQueryHandler(handle_inline_buttons, pattern='^session_|^back_to_')],
            ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            REQUESTING_PHONE: [MessageHandler(filters.CONTACT | filters.TEXT & ~filters.COMMAND, handle_phone)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_inline_buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply_buttons))
    
    jq = app.job_queue
    if jq:
        tz = pytz.timezone('Europe/Minsk')
        jq.run_daily(send_daily_schedule, time=time(hour=15, minute=0, tzinfo=tz))
        logger.info("📅 Рассылка настроена на 15:00")
        jq.run_daily(reset_weekly_spots, time=time(hour=14, minute=0, tzinfo=tz))
        logger.info("🔄 Сброс мест настроен на воскресенье 14:00")
    
    logger.info("🚀 Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if name == "__main__":
    main()
