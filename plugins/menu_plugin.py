from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
import inspect
import os
import logging
from handlers.plugin_handlers import get_all_commands

logger = logging.getLogger(__name__)

# Pagination variables
current_page = 0
users_per_page = 10

def setup(context):
    dp = context.dp
    sudo_users = context.sudo_users
    creator_id = context.creator_id

    async def check_user_permission(user_id: int) -> bool:
        """
        İstifadəçinin sudo və ya creator olub-olmadığını yoxlayır.
        """
        return user_id in sudo_users or user_id == creator_id

    @dp.message(Command("menu"))
    async def list_commands(message: types.Message):
        """
        /command komandasını yalnız sudo və creator istifadə edə bilər.
        """
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

        # İstifadəçiyə göstəriləcək mesajı hazırlayırıq
        response = [
            f"🔌 **Aktiv Pluginlər ({len(plugins)}):**",
            *[f"• {plugin.replace('_', ' ').title()}" for plugin in sorted(plugins)],
            f"\n👨💻 **Sudo istifadəçiləri:** {len(sudo_users)}",
            f"🆔 **Sizin ID:** {user_id}"
        ]

        # Düymələr yaradırıq
        builder = InlineKeyboardBuilder()
        builder.button(text="📂 Dir", callback_data="dir_cmd")
        builder.button(text="🔄 Alive", callback_data="alive_cmd")
        builder.button(text="🚀 Speedtest", callback_data="speedtest_cmd")
        builder.button(text="📜 Komandalar", callback_data="show_commands")
        builder.button(text="📊 Stats", callback_data="show_stats")
        builder.button(text="❌ Close", callback_data="close_window")
        builder.adjust(2)

        await message.answer("\n".join(response),
            reply_markup=builder.as_markup(resize_keyboard=True))

    @dp.callback_query(F.data == "show_commands")
    async def show_commands_callback(callback: types.CallbackQuery):
        """
        Komandaları göstərən callback. Yalnız sudo və creator istifadə edə bilər.
        """
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # Bütün komandaları toplayırıq
        commands = get_all_commands(dp)
        logger.info(f"Tapılan komandalar: {commands}")

        # Komandaları formatlaşdırırıq
        if commands:
            commands_text = "📜 **Mövcud Komandalar:**\n"
            commands_text += "\n".join([f"• /{cmd}" for cmd in commands])
        else:
            commands_text = "❌ Heç bir komanda tapılmadı."

        # Pop-up mətn olaraq göstəririk
        await callback.answer(commands_text, show_alert=True)

    @dp.callback_query(F.data == "dir_cmd")
    async def dir_cmd_callback(callback: types.CallbackQuery):
        """
        Dir komandasını icra edən callback. Yalnız sudo və creator istifadə edə bilər.
        """
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # Qovluqdakı faylları və qovluqları göstəririk
        current_dir = os.getcwd()
        files = os.listdir(current_dir)

        # Faylları və qovluqları formatlaşdırırıq
        response = [f"📂 **Qovluq:** `{current_dir}`", "```"]
        for file in files:
            file_path = os.path.join(current_dir, file)
            if os.path.isdir(file_path):
                response.append(f"📁 {file}/")
            else:
                response.append(f"📄 {file}")
        response.append("```")

        # Düymələri yaradırıq
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔙 Command menyusu", callback_data="back_to_command")
        keyboard.button(text="❌ Close", callback_data="close_window")
        keyboard.adjust(2)

        # Köhnə mesajı yenisi ilə əvəz edirik
        await callback.message.edit_text("\n".join(response), reply_markup=keyboard.as_markup())
        await callback.answer()

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
            f"🔌 **Aktiv Pluginlər ({len(plugins)}):**",
            *[f"• {plugin.replace('_', ' ').title()}" for plugin in sorted(plugins)],
            f"\n👨💻 **Sudo istifadəçiləri:** {len(sudo_users)}",
            f"🆔 **Sizin ID:** {user_id}"
        ]

        # Düymələr yaradırıq
        builder = InlineKeyboardBuilder()
        builder.button(text="📂 Dir", callback_data="dir_cmd")
        builder.button(text="🔄 Alive", callback_data="alive_cmd")
        builder.button(text="🚀 Speedtest", callback_data="speedtest_cmd")
        builder.button(text="📜 Komandalar", callback_data="show_commands")
        builder.button(text="📊 Stats", callback_data="show_stats")
        builder.button(text="❌ Close", callback_data="close_window")
        builder.adjust(2)

        # Köhnə mesajı yenisi ilə əvəz edirik
        await callback.message.edit_text("\n".join(response), reply_markup=builder.as_markup())
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

    @dp.callback_query(F.data == "show_stats")
    async def show_stats_callback(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in sudo_users and user_id != creator_id:
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        users = context.db.get_all_users()
        stats_message = get_stats_message(users)
        keyboard = get_pagination_keyboard(users)

        logger.debug(f"Stats message: {stats_message}")  # Debugging line
        await callback.message.edit_text(stats_message, parse_mode="HTML", reply_markup=keyboard.as_markup())
        await callback.answer()

    def get_stats_message(users):
        global current_page
        total_pages = (len(users) + users_per_page - 1) // users_per_page

        stats_message = (
            "<b>📊 Bot Statistikaları:</b>\n\n"
            f"• <b>İstifadəçi Sayısı:</b> {len(users)}\n\n"
            "<b>İstifadəçi Komanda Statistikaları:</b>\n"
        )

        start_index = current_page * users_per_page
        end_index = start_index + users_per_page
        for user in users[start_index:end_index]:
            stats_message += (
                f"👤 <b>{user['username']}</b> (ID: {user['user_id']}):\n"
                f"  • Mesaj Sayısı: {user['message_count']}\n"
                f"  • Mahnı Yükləmə Sayısı: {user['song_download_count']}\n\n"
            )

        return stats_message

    def get_pagination_keyboard(users):
        global current_page
        total_pages = (len(users) + users_per_page - 1) // users_per_page

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="⬅️ Geri", callback_data="prev_page")
        keyboard.button(text=f"Səhifə {current_page + 1}/{total_pages}", callback_data="current_page")
        keyboard.button(text="İrəli ➡️", callback_data="next_page")
        keyboard.button(text="-10", callback_data="prev_10_pages")
        keyboard.button(text="+10", callback_data="next_10_pages")
        keyboard.button(text="🔙 Command menyusu", callback_data="back_to_command")
        keyboard.button(text="❌ Close", callback_data="close_window")
        keyboard.adjust(3, 2)

        return keyboard

    @dp.callback_query(F.data == "prev_page")
    async def handle_prev_page(callback: types.CallbackQuery):
        global current_page
        if current_page > 0:
            current_page -= 1
            await update_stats_message(callback)
        else:
            await callback.answer("Səhifə bitdi")

    @dp.callback_query(F.data == "next_page")
    async def handle_next_page(callback: types.CallbackQuery):
        global current_page
        users = context.db.get_all_users()
        total_pages = (len(users) + users_per_page - 1) // users_per_page
        if current_page < total_pages - 1:
            current_page += 1
            await update_stats_message(callback)
        else:
            await callback.answer("Səhifə bitdi")

    @dp.callback_query(F.data == "prev_10_pages")
    async def handle_prev_10_pages(callback: types.CallbackQuery):
        global current_page
        if current_page >= 10:
            current_page -= 10
            await update_stats_message(callback)
        else:
            await callback.answer("Səhifə bitdi")

    @dp.callback_query(F.data == "next_10_pages")
    async def handle_next_10_pages(callback: types.CallbackQuery):
        global current_page
        users = context.db.get_all_users()
        total_pages = (len(users) + users_per_page - 1) // users_per_page
        if current_page + 10 < total_pages:
            current_page += 10
            await update_stats_message(callback)
        else:
            await callback.answer("Səhifə bitdi")

    async def update_stats_message(callback: types.CallbackQuery):
        users = context.db.get_all_users()
        stats_message = get_stats_message(users)
        keyboard = get_pagination_keyboard(users)
        await callback.message.edit_text(stats_message, parse_mode="HTML", reply_markup=keyboard.as_markup())