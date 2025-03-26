from aiogram import types
from aiogram.filters import Command
import logging

def setup(context):
    dp = context.dp
    sudo_users = context.sudo_users
    save_sudo = context.save_sudo_users
    creator_id = context.creator_id

    async def extract_user_id(message: types.Message) -> int:
        # Əgər reply edilibsə, reply edilən istifadəçinin ID-sini götür
        if message.reply_to_message:
            return message.reply_to_message.from_user.id
        
        # Əgər mesajın textində ID varsa, onu götür
        command_parts = message.text.split()
        if len(command_parts) > 1:
            try:
                return int(command_parts[1])
            except ValueError:
                pass
        
        return None

    @dp.message(Command("sudo"))
    async def grant_sudo(message: types.Message):
        if message.from_user.id != creator_id:
            await message.reply("❌ Only admins can use this command!")
            return

        target_user_id = await extract_user_id(message)
        if target_user_id is None:
            await message.reply("⚠️ Please reply to a user or provide a user ID!")
            return

        if target_user_id in sudo_users:
            await message.reply("ℹ️ User already has sudo privileges!")
            return

        sudo_users.append(target_user_id)
        save_sudo()
        logging.info(f"Granted sudo to user ID: {target_user_id}")
        await message.reply(f"✅ Granted sudo privileges to user ID: {target_user_id}")

    @dp.message(Command("unsudo"))
    async def revoke_sudo(message: types.Message):
        if message.from_user.id != creator_id:
            await message.reply("❌ Only admins can use this command!")
            return

        target_user_id = await extract_user_id(message)
        if target_user_id is None:
            await message.reply("⚠️ Please reply to a user or provide a user ID!")
            return

        if target_user_id not in sudo_users:
            await message.reply("ℹ️ User doesn't have sudo privileges!")
            return

        sudo_users.remove(target_user_id)
        save_sudo()
        logging.info(f"Revoked sudo from user ID: {target_user_id}")
        await message.reply(f"✅ Revoked sudo privileges from user ID: {target_user_id}")

    @dp.message(Command("sudolist"))
    async def list_sudo(message: types.Message):
        if message.from_user.id != creator_id:
            await message.reply("❌ Only admins can use this command!")
            return

        if not sudo_users:
            await message.reply("ℹ️ No sudo users!")
            return

        response = "🔐 <b>Sudo Users:</b>\n" + "\n".join(
            f"• <code>{user_id}</code>" for user_id in sudo_users
        )
        await message.reply(response, parse_mode="HTML")