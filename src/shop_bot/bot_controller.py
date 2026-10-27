import asyncio
import logging

from yookassa import Configuration
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode 

from shop_bot.data_manager import database
from shop_bot.bot.handlers import get_user_router
from shop_bot.bot.admin_handlers import get_admin_router
from shop_bot.bot.middlewares import BanMiddleware
from shop_bot.bot import handlers

logger = logging.getLogger(__name__)

class BotController:
    def __init__(self):
        self._dp = None
        self._bot = None
        self._task = None
        self._is_running = False
        self._loop = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        logger.info("Цикл событий установлен.")

    def get_bot_instance(self) -> Bot | None:
        return self._bot

    async def _start_polling(self):
        self._is_running = True
        logger.info("Запущен опрос Telegram (Основной-бот).")
        try:
            await self._dp.start_polling(self._bot)
        except asyncio.CancelledError:
            logger.info("Опрос остановлен (задача отменена).")
        except Exception as e:
            logger.error(f"Ошибка во время опроса: {e}", exc_info=True)
        finally:
            logger.info("Опрос корректно остановлен.")
            self._is_running = False
            self._task = None
            if self._bot:
                await self._bot.close()
            self._bot = None
            self._dp = None

    def start(self):
        if self._is_running:
            return {"status": "error", "message": "Бот уже запущен."}
        
        if not self._loop or not self._loop.is_running():
            return {"status": "error", "message": "Критическая ошибка: цикл событий не установлен."}

        token = database.get_setting("telegram_bot_token")
        bot_username = database.get_setting("telegram_bot_username")
        admin_id = database.get_setting("admin_telegram_id")
        if not admin_id:
            admin_ids = database.get_admin_ids()
            if admin_ids:
                admin_id = admin_ids[0]

        if not all([token, bot_username, admin_id]):
            return {
                "status": "error",
                "message": "Невозможно запустить: не все обязательные настройки Telegram заполнены (токен, username, ID админа)."
            }

        try:
            self._bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            self._dp = Dispatcher()
            
            # Вешаем BanMiddleware на уровни событий, где доступен event_from_user
            # Вместо уровня update, чтобы корректно отлавливать сообщения/колбэки забаненных пользователей
            self._dp.message.middleware(BanMiddleware())
            self._dp.callback_query.middleware(BanMiddleware())
            
            user_router = get_user_router()
            admin_router = get_admin_router()

            if not isinstance(user_router, Router):
                raise TypeError(f"get_user_router() must return Router instance, got: {type(user_router)}")
            if not isinstance(admin_router, Router):
                raise TypeError(f"get_admin_router() must return Router instance, got: {type(admin_router)}")
            
            self._dp.include_router(user_router)
            self._dp.include_router(admin_router)
            
            try:
                asyncio.run_coroutine_threadsafe(self._bot.delete_webhook(drop_pending_updates=True), self._loop)
            except Exception as e:
                logger.warning(f"Не удалось удалить вебхук перед запуском опроса: {e}")

            current_methods = handlers.get_current_payment_methods()
            if current_methods.get("yookassa"):
                Configuration.account_id = database.get_setting("yookassa_shop_id")
                Configuration.secret_key = database.get_setting("yookassa_secret_key")
            
            handlers.PAYMENT_METHODS = current_methods
            handlers.TELEGRAM_BOT_USERNAME = bot_username
            handlers.ADMIN_ID = admin_id

            self._task = asyncio.run_coroutine_threadsafe(self._start_polling(), self._loop)
            try:
                database.update_setting("bot_enabled", "true")
            except Exception as se:
                logger.warning(f"Не удалось обновить статус bot_enabled: {se}")
            logger.info("Команда на запуск передана в цикл событий.")
            return {"status": "success", "message": "Команда на запуск бота отправлена."}
            
        except Exception as e:
            logger.error(f"Не удалось запустить бота: {e}", exc_info=True)
            self._bot = None
            self._dp = None
            return {"status": "error", "message": f"Ошибка при запуске: {e}"}

    def stop(self):
        if not self._is_running:
            return {"status": "error", "message": "Бот не запущен."}

        if not self._loop or not self._dp:
            return {"status": "error", "message": "Критическая ошибка: компоненты бота недоступны."}

        logger.info("Отправляю сигнал на корректную остановку...")
        try:
            database.update_setting("bot_enabled", "false")
        except Exception as se:
            logger.warning(f"Не удалось обновить статус bot_enabled: {se}")
        asyncio.run_coroutine_threadsafe(self._dp.stop_polling(), self._loop)
        
        return {"status": "success", "message": "Команда на остановку бота отправлена."}

    def get_status(self):
        return {"is_running": self._is_running}