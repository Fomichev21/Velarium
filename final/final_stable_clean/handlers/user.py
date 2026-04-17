from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import ROLE_ADMIN, TARIFFS, settings
from database import (
    add_user,
    get_balance,
    get_referral_stats,
    get_role,
    get_user,
    get_vpn_key,
    is_banned,
    use_promo,
)
from payments import check_payment, create_payment_for_tariff, notify_admins_about_payment
from vpn import build_download_name

router = Router()


class UserStates(StatesGroup):
    waiting_for_promo = State()


def support_url() -> str:
    return f"https://t.me/{settings.support_username.lstrip('@')}"


def main_menu(user_id: int) -> InlineKeyboardMarkup:
    role = get_role(user_id)
    rows = [
        [InlineKeyboardButton(text="Купить VPN", callback_data="buy_menu")],
        [InlineKeyboardButton(text="Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="Промокод", callback_data="promo")],
        [InlineKeyboardButton(text="Поддержка", url=support_url())],
    ]
    if role >= ROLE_ADMIN:
        rows.insert(3, [InlineKeyboardButton(text="Админка", callback_data="open_admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_main_markup(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="back_main")],
            [InlineKeyboardButton(text="Поддержка", url=support_url())],
        ]
    )


def tariff_menu() -> InlineKeyboardMarkup:
    rows = []
    for code, tariff in TARIFFS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{tariff['title']} - {tariff['price']} RUB",
                    callback_data=f"buy:{code}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_actions(payment_id: str) -> InlineKeyboardMarkup:
    payment = check_payment(payment_id)
    rows = [
        [InlineKeyboardButton(text="Открыть оплату", url=payment["payment_url"])],
        [InlineKeyboardButton(text="Проверить оплату", callback_data=f"payment:{payment_id}")],
        [InlineKeyboardButton(text="Назад", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _guard_user(message_or_callback: Message | CallbackQuery, referred_by: int | None = None) -> bool:
    user = message_or_callback.from_user
    add_user(user.id, user.username, referred_by=referred_by)
    if is_banned(user.id):
        text = "Ваш аккаунт заблокирован. Напишите в поддержку, если это ошибка."
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer(text, show_alert=True)
        else:
            await message_or_callback.answer(text)
        return False
    return True


def profile_text(user_id: int) -> str:
    user = get_user(user_id)
    vpn_key = get_vpn_key(user_id)
    subscription = user["subscription_until"] or "не активна"
    access_url = vpn_key["config_text"] if vpn_key else "еще не выдана"
    balance = get_balance(user_id)
    return (
        "Ваш профиль\n\n"
        f"ID: {user['user_id']}\n"
        f"Username: @{user['username'] or 'unknown'}\n"
        f"Баланс: {balance} RUB\n"
        f"Подписка до: {subscription}\n"
        f"VPN ссылка: {access_url}"
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    referred_by: int | None = None
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referred_by = int(args[1][4:])
            if referred_by == message.from_user.id:
                referred_by = None
        except ValueError:
            referred_by = None

    if not await _guard_user(message, referred_by=referred_by):
        return

    await message.answer(
        "Добро пожаловать в VPN-бот.\n\n"
        "Здесь можно оплатить тариф, дождаться подтверждения админа и получить ссылку для подключения.",
        reply_markup=main_menu(message.from_user.id),
    )


@router.message(Command("pay"))
async def pay_cmd(message: Message) -> None:
    if not await _guard_user(message):
        return
    await message.answer(
        "Выберите тариф.\n\nПосле оплаты администратор подтвердит перевод, и бот выдаст доступ автоматически.",
        reply_markup=tariff_menu(),
    )


@router.message(Command("gift"))
async def gift_cmd(message: Message, state: FSMContext) -> None:
    if not await _guard_user(message):
        return
    await state.set_state(UserStates.waiting_for_promo)
    await message.answer(
        "Отправьте промокод одним сообщением.",
        reply_markup=back_to_main_markup(message.from_user.id),
    )


@router.message(Command("ref"))
async def ref_cmd(message: Message) -> None:
    if not await _guard_user(message):
        return

    user_id = message.from_user.id
    bot_info = await message.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    stats = get_referral_stats(user_id)

    await message.answer(
        "Реферальная программа\n\n"
        "За каждого друга, который оплатит первую подписку, начисляется бонус.\n\n"
        f"Ваша ссылка:\n{link}\n\n"
        f"Приглашено: {stats['total']}\n"
        f"С бонусом: {stats['rewarded']}",
        reply_markup=back_to_main_markup(user_id),
    )


@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_user(callback):
        return
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(
        "Главное меню",
        reply_markup=main_menu(callback.from_user.id),
    )


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery) -> None:
    if not await _guard_user(callback):
        return
    await callback.answer()
    await callback.message.edit_text(
        profile_text(callback.from_user.id),
        reply_markup=back_to_main_markup(callback.from_user.id),
    )


@router.callback_query(F.data == "buy_menu")
async def buy_menu(callback: CallbackQuery) -> None:
    if not await _guard_user(callback):
        return
    await callback.answer()
    await callback.message.edit_text(
        "Выберите тариф.\n\n"
        "После оплаты администратор проверит перевод вручную, а бот автоматически выдаст доступ после подтверждения.",
        reply_markup=tariff_menu(),
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery) -> None:
    if not await _guard_user(callback):
        return

    tariff_code = callback.data.split(":", maxsplit=1)[1]
    if tariff_code not in TARIFFS:
        await callback.answer("Неизвестный тариф", show_alert=True)
        return

    tariff = TARIFFS[tariff_code]
    payment = create_payment_for_tariff(callback.from_user.id, tariff_code)
    admins_notified = await notify_admins_about_payment(callback.bot, payment["id"])
    await callback.answer()
    await callback.message.edit_text(
        "Счет создан.\n\n"
        f"Счет: {payment['invoice_code']}\n"
        f"Тариф: {tariff['title']}\n"
        f"Сумма: {tariff['price']}₽\n"
        "Статус: Ожидает оплату\n\n"
        "Открой оплату по кнопке ниже, переведи деньги и затем нажми «Проверить оплату».\n"
        "Администратор увидит этот счет и подтвердит его вручную."
        + (
            "\n\nВнимание: уведомление администраторам пока не доставлено. "
            "Проверь, что в базе есть хотя бы один админ или owner."
            if admins_notified == 0
            else ""
        ),
        reply_markup=payment_actions(payment["id"]),
    )


@router.callback_query(F.data.startswith("payment:"))
async def payment_status(callback: CallbackQuery) -> None:
    if not await _guard_user(callback):
        return

    payment_id = callback.data.split(":", maxsplit=1)[1]
    payment = check_payment(payment_id)
    if not payment:
        await callback.answer("Платеж не найден", show_alert=True)
        return

    if payment["status"] == "failed":
        await callback.answer("Оплата отклонена", show_alert=True)
        await callback.message.edit_text(
            "Оплата по этому счету отклонена администратором.\n\n"
            f"Счет: {payment.get('invoice_code') or payment_id}\n"
            "Если ты уже оплатил счет, напиши в поддержку и приложи чек.",
            reply_markup=payment_actions(payment_id),
        )
        return

    if payment["status"] != "paid":
        await callback.answer("Платеж еще на проверке", show_alert=True)
        await callback.message.edit_text(
            "Платеж еще не подтвержден.\n\n"
            f"Счет: {payment.get('invoice_code') or payment_id}\n"
            "После оплаты администратор проверит перевод вручную.\n"
            "Если ты уже оплатил, просто подожди ответа админа и нажми кнопку позже.",
            reply_markup=payment_actions(payment_id),
        )
        return

    vpn_key = get_vpn_key(callback.from_user.id)
    link_label = "Subscription Link" if vpn_key and vpn_key["config_text"].startswith(("http://", "https://")) else "VPN ссылка"
    await callback.answer("Оплата подтверждена")
    await callback.message.edit_text(
        "Оплата подтверждена.\n\n"
        f"Счет: {payment.get('invoice_code') or payment_id}\n"
        f"Подписка активна до: {get_user(callback.from_user.id)['subscription_until']}\n"
        f"{link_label}: {vpn_key['config_text'] if vpn_key else 'создается'}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Получить ссылку", callback_data="download_config")],
                [InlineKeyboardButton(text="Главное меню", callback_data="back_main")],
            ]
        ),
    )


@router.callback_query(F.data == "download_config")
async def download_config(callback: CallbackQuery) -> None:
    if not await _guard_user(callback):
        return

    vpn_key = get_vpn_key(callback.from_user.id)
    if not vpn_key:
        await callback.answer("Ссылка еще не подготовлена", show_alert=True)
        return

    await callback.answer()
    config_bytes = vpn_key["config_text"].encode("utf-8")
    file = BufferedInputFile(config_bytes, filename=build_download_name(callback.from_user.id))
    if vpn_key["config_text"].startswith(("http://", "https://")):
        await callback.message.answer(
            "Вот ваш Subscription Link:\n\n"
            f"{vpn_key['config_text']}"
        )
        return

    await callback.message.answer(
        "Вот ваша VPN-ссылка:\n\n"
        f"{vpn_key['config_text']}"
    )
    await callback.message.answer_document(
        file,
        caption="Резервная копия ссылки в текстовом файле.",
    )


@router.callback_query(F.data == "promo")
async def promo(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _guard_user(callback):
        return

    await callback.answer()
    await state.set_state(UserStates.waiting_for_promo)
    await callback.message.edit_text(
        "Отправьте промокод одним сообщением.",
        reply_markup=back_to_main_markup(callback.from_user.id),
    )


@router.message(UserStates.waiting_for_promo)
async def promo_handler(message: Message, state: FSMContext) -> None:
    if not await _guard_user(message):
        return

    result = use_promo(message.from_user.id, message.text or "")
    await state.clear()
    if result is None:
        await message.answer(
            "Такой промокод не найден или уже использован.",
            reply_markup=main_menu(message.from_user.id),
        )
        return

    await message.answer(
        f"Промокод активирован. На баланс начислено {result} RUB.",
        reply_markup=main_menu(message.from_user.id),
    )
