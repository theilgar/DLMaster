from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
import inspect
import logging

logger = logging.getLogger(__name__)
router = Router()

async def check_user_permission(message: types.Message) -> bool:
    context = message.bot.data.get('app_context')
    user_id = message.from_user.id
    return user_id in context.sudo_users or user_id == context.creator_id

async def setup(context):
    # Router-i əsas router-ə əlavə et
    context.main_router.include_router(router)
    
    # Handler-ləri qeydiyyata al
    router.message.register(list_commands, Command("menu"))
    router.callback_query.register(back_to_command, F.data == "back_to_command")
    router.callback_query.register(close_window, F.data == "close_window")
    
    logger.info("✅ Menu plugin yükləndi")

@router.message(Command("menu"))
async def list_commands(message: types.Message):
    if not await check_user_permission(message):
        return

    context = message.bot.data.get('app_context')
    
    # Aktiv pluginləri topla
    plugins = set()
    for handler in context.main_router.message.handlers:
        try:
            module = inspect.getmodule(handler.callback)
            if module and 'plugins' in module.__file__:
                plugins.add(module.__name__.split('.')[-1])
        except:
            continue

    # Qrup məlumatları
    group_info = ""
    if message.chat.type != "private":
        group_info = f"\n👥 <b>Qrup ID:</b> <code>{message.chat.id}</code>"

    # Mesaj kontenti
    response = [
        f"🔌 <b>Aktiv Pluginlər ({len(plugins)}):</b>",
        *[f"• {plugin.replace('_', ' ').title()}" for plugin in sorted(plugins)],
        f"\n👨💻 <b>Sudo istifadəçiləri:</b> {len(context.sudo_users)}",
        f"🆔 <b>Sizin ID:</b> <code>{message.from_user.id}</code>",
        group_info
    ]

    # İnline düymələr
    builder = InlineKeyboardBuilder()
    buttons = [
        ("📂 Fayl Sistemi", "dir_cmd"),
        ("🔄 Sistem Statusu", "alive_cmd"),
        ("🚀 Şəbəkə Testi", "speedtest_cmd"),
        ("📊 Statistikalar", "show_stats"),
        ("🎵 Mahnı Tarixçəsi", "song_history"),
        ("❌ Bağla", "close_window")
    ]
    
    for text, data in buttons:
        builder.button(text=text, callback_data=data)
    
    builder.adjust(2, 2, 2, 1)
    
    await message.answer(
        "\n".join(response),
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "back_to_command")
async def back_to_command(callback: types.CallbackQuery):
    if not await check_user_permission(callback.message):
        await callback.answer("❌ Yetkiniz yoxdur!", show_alert=True)
        return

    context = callback.bot.data.get('app_context')
    
    # Aktiv pluginləri yenilə
    plugins = set()
    for handler in context.main_router.message.handlers:
        try:
            module = inspect.getmodule(handler.callback)
            if module and 'plugins' in module.__file__:
                plugins.add(module.__name__.split('.')[-1])
        except:
            continue

    # Yenilənmiş mesaj
    response = [
        f"🔌 <b>Aktiv Pluginlər ({len(plugins)}):</b>",
        *[f"• {plugin.replace('_', ' ').title()}" for plugin in sorted(plugins)],
        f"\n👨💻 <b>Sudo istifadəçiləri:</b> {len(context.sudo_users)}",
        f"🆔 <b>Sizin ID:</b> <code>{callback.from_user.id}</code>"
    ]

    # Düymələri yenidən qur
    builder = InlineKeyboardBuilder()
    buttons = [
        ("📂 Fayl Sistemi", "dir_cmd"),
        ("🔄 Sistem Statusu", "alive_cmd"),
        ("🚀 Şəbəkə Testi", "speedtest_cmd"),
        ("📊 Statistikalar", "show_stats"),
        ("🎵 Mahnı Tarixçəsi", "song_history"),
        ("❌ Bağla", "close_window")
    ]
    
    for text, data in buttons:
        builder.button(text=text, callback_data=data)
    
    builder.adjust(2, 2, 2, 1)
    
    await callback.message.edit_text(
        "\n".join(response),
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "close_window")
async def close_window(callback: types.CallbackQuery):
    if not await check_user_permission(callback.message):
        await callback.answer("❌ Yetkiniz yoxdur!", show_alert=True)
        return

    await callback.message.delete()
    await callback.answer("✅ Pəncərə bağlandı.")