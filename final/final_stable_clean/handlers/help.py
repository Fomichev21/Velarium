from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(
        "Команды бота:\n\n"
        "/start — открыть главное меню\n"
        "/help — краткая помощь\n\n"
        "Дальше все основные сценарии идут через кнопки: покупка, профиль, промокод и поддержка."
    )
