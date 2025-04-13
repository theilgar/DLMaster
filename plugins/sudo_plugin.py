from aiogram import types
from aiogram.filters import Command
import logging
from typing import List

def setup(context):
    dp = context.dp
    db = context.db  # Verilənlər bazası obyekti
    creator_id = context.creator_id
    
    async def extract_user_id(message: types.Message) -> int:
        """İstifadəçi ID-sini çıxarar:
        - Əgər reply mesaj varsa, onun ID-sini götür
        - Əks halda mesajdakı arqumentdən ID çıxart"""
        
        if message.reply_to_message:
            return message.reply_to_message.from_user.id
        
        command_parts = message.text.split()
        if len(command_parts) > 1:
            try:
                return int(command_parts[1])
            except ValueError:
                await message.reply("⚠️ Yanlış ID formatı!")
                return None
        
        await message.reply("ℹ️ İstifadəçiyə cavab verin və ya ID yazın!")
        return None

    @dp.message(Command("sudo"))
    async def grant_sudo(message: types.Message):
        """İstifadəçiyə sudo hüquqları ver"""
        
        if message.from_user.id != creator_id:
            await message.reply("❌ Bu əmr yalnız bot sahibi üçündür!")
            return

        target_user_id = await extract_user_id(message)
        if not target_user_id:
            return

        if target_user_id in context.sudo_users:
            await message.reply("ℹ️ Bu istifadəçi artıq sudo hüquqlarına malikdir!")
            return

        try:
            # Verilənlər bazasına əlavə et
            db.add_sudo_user(target_user_id)
            context.sudo_users.append(target_user_id)
            
            await message.reply(f"✅ Sudo hüquqları verildi: <code>{target_user_id}</code>", parse_mode="HTML")
            logging.info(f"Yeni sudo istifadəçisi: {target_user_id}")
            
        except Exception as e:
            await message.reply("❌ Xəta baş verdi!")
            logging.error(f"Sudo əlavə edilərkən xəta: {e}")

    @dp.message(Command("unsudo"))
    async def revoke_sudo(message: types.Message):
        """İstifadəçidən sudo hüquqlarını al"""
        
        if message.from_user.id != creator_id:
            await message.reply("❌ Bu əmr yalnız bot sahibi üçündür!")
            return

        target_user_id = await extract_user_id(message)
        if not target_user_id:
            return

        if target_user_id not in context.sudo_users:
            await message.reply("ℹ️ Bu istifadəçinin sudo hüququ yoxdur!")
            return

        try:
            # Verilənlər bazasından sil
            db.remove_sudo_user(target_user_id)
            context.sudo_users.remove(target_user_id)
            
            await message.reply(f"✅ Sudo hüquqları ləğv edildi: <code>{target_user_id}</code>", parse_mode="HTML")
            logging.info(f"Sudo ləğv edildi: {target_user_id}")
            
        except Exception as e:
            await message.reply("❌ Xəta baş verdi!")
            logging.error(f"Sudo silinərkən xəta: {e}")

    @dp.message(Command("sudolist"))
    async def list_sudo(message: types.Message):
        """Sudo istifadəçilərinin siyahısını göstər"""
        
        if message.from_user.id != creator_id:
            await message.reply("❌ Bu əmr yalnız bot sahibi üçündür!")
            return

        if not context.sudo_users:
            await message.reply("ℹ️ Sudo istifadəçisi yoxdur!")
            return

        users_info = []
        for user_id in context.sudo_users:
            user_data = db.get_user_info(user_id)  # Əlavə funksiya lazımdır
            username = user_data.get('username', 'N/A') if user_data else 'N/A'
            users_info.append(f"• ID: <code>{user_id}</code> | 👤: {username}")

        response = "🔐 <b>Sudo İstifadəçiləri:</b>\n" + "\n".join(users_info)
        await message.reply(response, parse_mode="HTML")