from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
import inspect
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Bütün pluginlərdə:
def setup(context):
    dp = context.dp
    db = context.db  # Əlavə edilir
    sudo_users = db.get_sudo_users()  # Köhnə context.sudo_users əvəzinə
    creator_id = context.creator_id

    async def check_user_permission(user_id: int) -> bool:
        return user_id in sudo_users or user_id == creator_id

    @dp.message(Command("menu"))
    async def list_commands(message: types.Message):
        user_id = message.from_user.id
        if not await check_user_permission(user_id):
            return

        # Aktiv pluginləri toplayırıq
        plugins = set()
        for handler in dp.message.handlers:
            try:
                module = inspect.getmodule(handler.callback)
                if module and 'plugins' in module.__file__:
                    plugins.add(module.__name__.split('.')[-1])
            except:
                continue

        # Qrup ID-ni əlavə edirik
        group_info = ""
        if message.chat.type != "private":
            group_info = f"\n👥 <b>Qrup ID:</b> <code>{message.chat.id}</code>"

        # İstifadəçiyə göstəriləcək mesajı hazırlayırıq
        response = [
            f"🔌 <b>Aktiv Pluginlər ({len(plugins)}):</b>",
            *[f"• {plugin.replace('_', ' ').title()}" for plugin in sorted(plugins)],
            f"\n👨💻 <b>Sudo istifadəçiləri:</b> {len(sudo_users)}",
            f"🆔 <b>Sizin ID:</b> <code>{user_id}</code>",
            group_info
        ]

        # Düymələr yaradırıq
        builder = InlineKeyboardBuilder()
        builder.button(text="📂 Fayl Sistemi", callback_data="dir_cmd")
        builder.button(text="🔄 Sistem Statusu", callback_data="alive_cmd")
        builder.button(text="🚀 Şəbəkə Testi", callback_data="speedtest_cmd")
        builder.button(text="📊 Statistikalar", callback_data="show_stats")
        builder.button(text="🎵 Mahnı Tarixçəsi", callback_data="song_history")
        builder.button(text="❌ Bağla", callback_data="close_window")
        builder.adjust(2, 2, 2, 1)


        await message.answer("\n".join(response),
                            reply_markup=builder.as_markup(resize_keyboard=True),
                            parse_mode="HTML")

    @dp.callback_query(F.data == "back_to_command")
    async def back_to_command(callback: types.CallbackQuery):
        """
        Command menyusuna qayıtmaq üçün callback. Yalnız sudo və creator istifadə edə bilər.
        """
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # Aktiv pluginləri toplayırıq
        plugins = set()
        for handler in dp.message.handlers:
            try:
                module = inspect.getmodule(handler.callback)
                if module and 'plugins' in module.__file__:
                    plugins.add(module.__name__.split('.')[-1])
            except:
                continue

        # İstifadəçiyə göstəriləcək mesajı hazırlayırıq
        response = [
            f"🔌 <b>Aktiv Pluginlər ({len(plugins)}):</b>",
            *[f"• {plugin.replace('_', ' ').title()}" for plugin in sorted(plugins)],
            f"\n👨💻 <b>Sudo istifadəçiləri:</b> {len(sudo_users)}",
            f"🆔 <b>Sizin ID:</b> <code>{user_id}</code>"
        ]

        # Düymələr yaradırıq
        builder = InlineKeyboardBuilder()
        builder.button(text="📂 Fayl Sistemi", callback_data="dir_cmd")
        builder.button(text="🔄 Sistem Statusu", callback_data="alive_cmd")
        builder.button(text="🚀 Şəbəkə Testi", callback_data="speedtest_cmd")
        builder.button(text="📊 Statistikalar", callback_data="show_stats")
        builder.button(text="🎵 Mahnı Tarixçəsi", callback_data="song_history")
        builder.button(text="❌ Bağla", callback_data="close_window")
        builder.adjust(2, 2, 2, 1)

        # Köhnə mesajı yenisi ilə əvəz edirik
        await callback.message.edit_text("\n".join(response), 
                                       reply_markup=builder.as_markup(),
                                       parse_mode="HTML")
        await callback.answer()

    
    @dp.callback_query(F.data == "close_window")
    async def close_window(callback: types.CallbackQuery):
        """
        Pəncərəni bağlamaq üçün callback. Yalnız sudo və creator istifadə edə bilər.
        """
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return
        

        # Pəncərəni bağlamaq üçün mesajı silirik
        await callback.message.delete()
        await callback.answer("✅ Pəncərə bağlandı.")