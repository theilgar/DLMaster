from aiogram import types
from aiogram.filters import Command

def setup(context):
    dp = context.dp
    sudo_users = context.sudo_users
    save_sudo = context.save_sudo_users
    creator_id = context.creator_id

    @dp.message(Command("sudo"))
    async def grant_sudo(message: types.Message):
        if message.from_user.id != creator_id:
            await message.reply("❌ Bu əmr yalnız adminlər üçündür!")
            return

        if not message.reply_to_message:
            await message.reply("⚠️ İstifadəçiyə reply edin!")
            return

        target_user = message.reply_to_message.from_user
        if target_user.id in sudo_users:
            await message.reply("ℹ️ Bu istifadəçi artıq sudo istifadəçisidir!")
            return

        sudo_users.append(target_user.id)
        save_sudo()
        await message.reply(f"✅ Sudo yetkisi verildi: {target_user.full_name}")

    @dp.message(Command("unsudo"))
    async def revoke_sudo(message: types.Message):
        if message.from_user.id != creator_id:
            await message.reply("❌ Bu əmr yalnız adminlər üçündür!")
            return

        if not message.reply_to_message:
            await message.reply("⚠️ İstifadəçiyə reply edin!")
            return

        target_user = message.reply_to_message.from_user
        if target_user.id not in sudo_users:
            await message.reply("ℹ️ Bu istifadəçidə sudo yetkisi yoxdur!")
            return

        sudo_users.remove(target_user.id)
        save_sudo()
        await message.reply(f"✅ Sudo yetkisi silindi: {target_user.full_name}")

    @dp.message(Command("sudolist"))
    async def list_sudo(message: types.Message):
        if message.from_user.id != creator_id:
            await message.reply("❌ Bu əmr yalnız adminlər üçündür!")
            return

        if not sudo_users:
            await message.reply("ℹ️ Sudo istifadəçisi yoxdur!")
            return

        response = "🔐 Sudo İstifadəçiləri:\n" + "\n".join(
            f"• {user_id}" for user_id in sudo_users
        )
        await message.reply(response)