from aiogram import Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardRemove

async def setup_report_plugin(dp: Dispatcher, creator_id: int):
    """
    Report plugin-ini quraşdırır. Bu plugin istifadəçilərə problemləri adminə bildirmək imkanı verir.
    """
    @dp.message(Command("report"))
    async def report_command(message: types.Message, command: Command):
        bot = message.bot  # Bot obyektini alırıq
        report_args = " ".join(command.args) if command.args else ""

        if message.reply_to_message:
            original_text = message.reply_to_message.text or "<mətn yoxdur>"
            report_body = report_args if report_args else "(Əlavə məlumat yoxdur)"
            report_text = (
                f"📨 Yeni Report:\n\n"
                f"👤 İstifadəçi: @{message.from_user.username} (ID: {message.from_user.id})\n"
                f"📝 Əlavə Mesaj: {report_body}\n\n"
                f"🔗 Reply edilən mesaj:\n{original_text}"
            )
        else:
            if report_args:
                report_text = (
                    f"📨 Yeni Report:\n\n"
                    f"👤 İstifadəçi: @{message.from_user.username} (ID: {message.from_user.id})\n"
                    f"📝 Mesaj: {report_args}"
                )
            else:
                await message.reply(
                    "Zəhmət olmasa, problem haqqında məlumat verin. Ya bir mesaja reply edin, ya da report mətni əlavə edin.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
        
        try:
            await bot.send_message(creator_id, report_text)
            await message.reply("✅ Report adminə göndərildi. Təşəkkürlər!")
        except Exception as e:
            await message.reply(f"❌ Report göndərilərkən xəta baş verdi: {e}")

async def setup(context):
    """
    Plugin-in əsas quraşdırma funksiyası. Bu funksiya avtomatik olaraq çağırılır.
    """
    creator_id = context.creator_id
    dp = context.dp
    await setup_report_plugin(dp, creator_id)