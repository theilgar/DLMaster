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

# Bütün pluginlərdə:
def setup(context):
    dp = context.dp
    db = context.db  # Əlavə edilir
    sudo_users = db.get_sudo_users()  # Köhnə context.sudo_users əvəzinə
    creator_id = context.creator_id

    async def check_user_permission(user_id: int) -> bool:
        return user_id in sudo_users or user_id == creator_id

    @dp.callback_query(F.data == "show_stats")
    async def show_stats_callback(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        # İstifadəçi ve qrup statistikalarını əldə et
        users = db.get_all_users()
        songs = db.get_all_songs()
        groups = db.get_all_groups()
        
        # Ümumi statistikalar
        total_users = len(users)
        total_songs = len(songs)
        total_messages = sum(user['message_count'] for user in users)
        total_groups = len(groups)
        
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
            f"👥 <b>Ümumi qruplar:</b> {total_groups}\n"
            f"🎵 <b>Ümumi yüklənən mahnılar:</b> {total_songs}\n"
            f"✉️ <b>Ümumi mesajlar:</b> {total_messages}\n"
            "<b>🏆 Ən aktiv istifadəçilər:</b>\n"
        )
        
        for i, user in enumerate(top_users, 1):
            stats_message += f"{i}. {user['username']} - {user['message_count']} mesaj\n"
        
        stats_message += "\n<b>🎧 Ən çox yüklənən mahnılar:</b>\n"
        for i, (song, count) in enumerate(top_songs, 1):
            stats_message += f"{i}. {song} - {count} dəfə\n"
        
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📊 Detallı Statistikalar", callback_data="detailed_stats")
        keyboard.button(text="👥 Qrup Statistikaları", callback_data="group_stats")
        keyboard.button(text="🎵 Mahnı Tarixçəsi", callback_data="song_history")
        keyboard.button(text="🔙 Geri", callback_data="back_to_command")
        keyboard.button(text="❌ Bağla", callback_data="close_window")
        keyboard.adjust(1, 2, 2)
        
        await callback.message.edit_text(stats_message, parse_mode="HTML", reply_markup=keyboard.as_markup())
        await callback.answer()

    @dp.callback_query(F.data == "group_stats")
    async def show_group_stats(callback: types.CallbackQuery):
        user_id = callback.from_user.id
        if not await check_user_permission(user_id):
            await callback.answer("❌ Bu əmri icra etmək üçün yetkiniz yoxdur.", show_alert=True)
            return

        groups = db.get_all_groups()
        
        if not groups:
            await callback.answer("❌ Heç bir qrup yoxdur.", show_alert=True)
            return
        
        # Sort groups by message count
        top_groups = sorted(groups, key=lambda x: x.get('message_count', 0), reverse=True)[:10]
        
        response = ["<b>👥 Ən aktiv qruplar:</b>\n"]
        for i, group in enumerate(top_groups, 1):
            response.append(
                f"{i}. <b>{group.get('title', 'ID: '+str(group['group_id']))}</b>\n"
                f"   • ID: {group['group_id']}\n"
                f"   • Mesaj sayı: {group.get('message_count', 0)}\n"
                f"   • Üzvlər: {group.get('member_count', 'N/A')}\n"
            )
        
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📊 Ümumi Statistikalar", callback_data="show_stats")
        keyboard.button(text="🔙 Geri", callback_data="back_to_command")
        keyboard.button(text="❌ Bağla", callback_data="close_window")
        keyboard.adjust(1, 2)
        
        
        await callback.message.edit_text("\n".join(response), parse_mode="HTML", reply_markup=keyboard.as_markup())
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