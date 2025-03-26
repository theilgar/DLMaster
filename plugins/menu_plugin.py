from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
import inspect
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Pagination variables
current_page = 0
users_per_page = 10

def setup(context):
    dp = context.dp
    sudo_users = context.sudo_users
    creator_id = context.creator_id
    db = context.db

    async def check_user_permission(user_id: int) -> bool:
        """
        İstifadəçinin sudo və ya creator olub-olmadığını yoxlayır.
        """
        return user_id in sudo_users or user_id == creator_id

    @dp.message(Command("menu"))
    async def list_commands(message: types.Message):
        """
        /menu komandasını yalnız sudo və creator istifadə edə bilər.
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

        # Log qeydi
        db.add_log(
            level="INFO",
            message=f"User {message.from_user.username or message.from_user.id} menyunu açdı",
            user_id=user_id,
            group_id=message.chat.id if message.chat.type != "private" else None,
            command="/menu"
        )

        await message.answer("\n".join(response),
                            reply_markup=builder.as_markup(resize_keyboard=True),
                            parse_mode="HTML")

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
        response = [f"📂 <b>Qovluq:</b> <code>{current_dir}</code>", "<pre>"]
        for file in files:
            file_path = os.path.join(current_dir, file)
            if os.path.isdir(file_path):
                response.append(f"📁 {file}/")
            else:
                size = os.path.getsize(file_path)
                response.append(f"📄 {file} ({size/1024:.1f} KB)")
        response.append("</pre>")

        # Düymələri yaradırıq
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔙 Geri", callback_data="back_to_command")
        keyboard.button(text="❌ Bağla", callback_data="close_window")
        keyboard.adjust(2)

        # Log qeydi
        db.add_log(
            level="INFO",
            message=f"User {callback.from_user.username or user_id} fayl sisteminə baxdı",
            user_id=user_id,
            command="dir_cmd"
        )

        # Köhnə mesajı yenisi ilə əvəz edirik
        await callback.message.edit_text("\n".join(response), 
                                       reply_markup=keyboard.as_markup(),
                                       parse_mode="HTML")
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

        # Log qeydi
        db.add_log(
            level="INFO",
            message=f"User {callback.from_user.username or user_id} menyunu bağladı",
            user_id=user_id,
            command="close_window"
        )

        # Pəncərəni bağlamaq üçün mesajı silirik
        await callback.message.delete()
        await callback.answer("✅ Pəncərə bağlandı.")

    @dp.callback_query(F.data == "show_stats")
    async def show_stats_callback(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # İstifadəçi və mahnı statistikalarını əldə et
        users = db.get_all_users()
        songs = db.get_all_songs()
        logs = db.get_logs()
        
        # Ümumi statistikalar
        total_users = len(users)
        total_songs = len(songs)
        total_messages = sum(user['message_count'] for user in users)
        total_commands = len([log for log in logs if log.get('command')])
        
        # Top 5 mahnılar
        song_counter = {}
        for song in songs:
            key = f"{song['song_title']} - {song['artist']}"
            song_counter[key] = song_counter.get(key, 0) + 1
        top_songs = sorted(song_counter.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Top 5 aktiv istifadəçilər
        top_users = sorted(users, key=lambda x: x['message_count'], reverse=True)[:5]
        
        stats_message = (
            "<b>📊 Bot Statistikaları:</b>\n\n"
            f"👥 <b>Ümumi istifadəçilər:</b> {total_users}\n"
            f"🎵 <b>Ümumi yüklənən mahnılar:</b> {total_songs}\n"
            f"✉️ <b>Ümumi mesajlar:</b> {total_messages}\n"
            f"⚡ <b>Ümumi əmrlər:</b> {total_commands}\n\n"
            "<b>🏆 Ən aktiv istifadəçilər:</b>\n"
        )
        
        for i, user in enumerate(top_users, 1):
            stats_message += f"{i}. {user['username']} - {user['message_count']} mesaj\n"
        
        stats_message += "\n<b>🎧 Ən çox yüklənən mahnılar:</b>\n"
        for i, (song, count) in enumerate(top_songs, 1):
            stats_message += f"{i}. {song} - {count} dəfə\n"
        
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📊 Detallı Statistikalar", callback_data="detailed_stats")
        keyboard.button(text="🎵 Mahnı Tarixçəsi", callback_data="song_history")
        keyboard.button(text="🔙 Geri", callback_data="back_to_command")
        keyboard.button(text="❌ Bağla", callback_data="close_window")
        keyboard.adjust(1, 2, 1)
        
        # Log qeydi
        db.add_log(
            level="INFO",
            message=f"User {callback.from_user.username or user_id} statistikaları görüntülədi",
            user_id=user_id,
            command="show_stats"
        )
        
        await callback.message.edit_text(stats_message, parse_mode="HTML", reply_markup=keyboard.as_markup())
        await callback.answer()

    @dp.callback_query(F.data == "detailed_stats")
    async def show_detailed_stats(callback: types.CallbackQuery):
        users = db.get_all_users()
        stats_message = get_stats_message(users)
        keyboard = get_pagination_keyboard(users)

        await callback.message.edit_text(stats_message, parse_mode="HTML", reply_markup=keyboard.as_markup())
        await callback.answer()

    @dp.callback_query(F.data == "song_history")
    async def show_song_history(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        songs = db.get_all_songs()
        
        if not songs:
            await callback.answer("❌ Heç bir mahnı yüklənməyib.", show_alert=True)
            return
        
        response = ["<b>🎵 Son yüklənən mahnılar:</b>\n"]
        for song in songs[:10]:  # Son 10 mahnını göstər
            response.append(
                f"🎧 <b>{song['song_title']}</b> - {song['artist']}\n"
                f"👤 İstifadəçi: {song.get('username', 'ID: '+str(song['user_id']))}\n"
                f"⏳ Tarix: {song['download_date']}\n"
                f"🔗 Mənbə: {song['source']}\n"
            )
        
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📊 Statistikalar", callback_data="show_stats")
        keyboard.button(text="🔙 Geri", callback_data="back_to_command")
        keyboard.button(text="❌ Bağla", callback_data="close_window")
        keyboard.adjust(1, 2)
        
        # Log qeydi
        db.add_log(
            level="INFO",
            message=f"User {callback.from_user.username or user_id} mahnı tarixçəsini görüntülədi",
            user_id=user_id,
            command="song_history"
        )
        
        await callback.message.edit_text("\n".join(response), parse_mode="HTML", reply_markup=keyboard.as_markup())
        await callback.answer()

    def get_stats_message(users):
        global current_page
        total_pages = (len(users) + users_per_page - 1) // users_per_page

        stats_message = (
            "<b>📊 Detallı İstifadəçi Statistikaları:</b>\n\n"
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

        stats_message += f"\n📄 Səhifə {current_page + 1}/{total_pages}"
        return stats_message

    def get_pagination_keyboard(users):
        global current_page
        total_pages = (len(users) + users_per_page - 1) // users_per_page

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="⬅️ Geri", callback_data="prev_page")
        keyboard.button(text=f"{current_page + 1}/{total_pages}", callback_data="current_page")
        keyboard.button(text="İrəli ➡️", callback_data="next_page")
        keyboard.button(text="🔙 Ümumi Statistikalar", callback_data="show_stats")
        keyboard.button(text="❌ Bağla", callback_data="close_window")
        keyboard.adjust(3, 2)

        return keyboard

    @dp.callback_query(F.data == "prev_page")
    async def handle_prev_page(callback: types.CallbackQuery):
        global current_page
        if current_page > 0:
            current_page -= 1
            await update_stats_message(callback)
        else:
            await callback.answer("❌ Artıq birinci səhifədəsiniz")

    @dp.callback_query(F.data == "next_page")
    async def handle_next_page(callback: types.CallbackQuery):
        global current_page
        users = db.get_all_users()
        total_pages = (len(users) + users_per_page - 1) // users_per_page
        if current_page < total_pages - 1:
            current_page += 1
            await update_stats_message(callback)
        else:
            await callback.answer("❌ Artıq son səhifədəsiniz")

    async def update_stats_message(callback: types.CallbackQuery):
        users = db.get_all_users()
        stats_message = get_stats_message(users)
        keyboard = get_pagination_keyboard(users)
        await callback.message.edit_text(stats_message, parse_mode="HTML", reply_markup=keyboard.as_markup())