"""Standalone mode only. Never run using a token already polled by another bot."""
import asyncio,logging,os
from aiogram import Bot,Dispatcher
from app.telegram_admin.extension import install
from app.config import settings
async def main():
    cfg=settings()
    if not cfg.telegram_bot_token.get_secret_value():raise RuntimeError('TELEGRAM_BOT_TOKEN not configured')
    bot=Bot(cfg.telegram_bot_token.get_secret_value());dp=Dispatcher()
    install(dp,os.environ.get('TELEGRAM_EXTENSION_CONFIG','/run/secrets/telegram.json'))
    try:await dp.start_polling(bot)
    finally:await bot.session.close()
if __name__=='__main__':asyncio.run(main())
