from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F
from aiogram.fsm.context import FSMContext
import os
import logging
import shutil

logger = logging.getLogger(__name__)

def setup(context):
    dp = context.dp
    creator_id = context.creator_id
    db = context.db

    async def check_user_permission(user_id: int, chat_type: str) -> bool:
        return user_id == creator_id

    async def generate_file_manager(path: str):
        try:
            items = os.listdir(path)
        except Exception as e:
            logger.error(f"Directory error: {e}")
            return None, None

        builder = InlineKeyboardBuilder()
        response = [f"📂 <b>Directory:</b> <code>{path}</code>", "<pre>"]

        # Control buttons (1 row)
        control_buttons = []
        if path != "/" and path != os.getcwd():
            control_buttons.append(types.InlineKeyboardButton(
                text="↩️ Parent", 
                callback_data=f"dir:{os.path.dirname(path)}"
            ))
        control_buttons.append(types.InlineKeyboardButton(
            text="⬆️ Upload", 
            callback_data=f"upload_to:{path}"
        ))
        builder.row(*control_buttons)

        # Separate folders and files
        folders = []
        files = []
        for item in sorted(items):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                folders.append(item)
            else:
                files.append(item)

        # Add folders (2 per row)
        folder_buttons = []
        for folder in folders:
            folder_buttons.append(types.InlineKeyboardButton(
                text=f"📁 {folder[:12]}" if len(folder) > 12 else f"📁 {folder}",
                callback_data=f"dir:{os.path.join(path, folder)}"
            ))
            response.append(f"📁 {folder}/")
        
        for i in range(0, len(folder_buttons), 2):
            row = folder_buttons[i:i+2]
            if len(row) == 2:
                builder.row(row[0], row[1])
            else:
                builder.row(row[0])

        # Add files (4 per row)
        file_buttons = []
        for file in files:
            file_path = os.path.join(path, file)
            size = os.path.getsize(file_path)
            size_str = f"{size/1024:.1f}K" if size < 1024*1024 else f"{size/(1024*1024):.1f}M"
            file_buttons.append(types.InlineKeyboardButton(
                text=f"📄 {file[:10]}" if len(file) > 10 else f"📄 {file}",
                callback_data=f"file:{file_path}"
            ))
            response.append(f"📄 {file} ({size_str})")

        for i in range(0, len(file_buttons), 4):
            row = file_buttons[i:i+4]
            if len(row) == 4:
                builder.row(row[0], row[1], row[2], row[3])
            elif len(row) == 3:
                builder.row(row[0], row[1], row[2])
            elif len(row) == 2:
                builder.row(row[0], row[1])
            else:
                builder.row(row[0])

        # Command buttons (compact)
        builder.row(
            types.InlineKeyboardButton(text="🔄", callback_data=f"dir:{path}"),
            types.InlineKeyboardButton(text="🔙", callback_data="back_to_command"),
            types.InlineKeyboardButton(text="❌", callback_data="close_window")
        )

        response.append("</pre>")
        return "\n".join(response), builder.as_markup()

    @dp.callback_query(F.data == "dir_cmd")
    async def dir_cmd_callback(callback: types.CallbackQuery):
        if not await check_user_permission(callback.from_user.id, callback.message.chat.type):
            await callback.answer()
            return

        initial_path = os.getcwd()
        response, keyboard = await generate_file_manager(initial_path)
        
        if not response:
            await callback.answer("❌ Couldn't open directory!", show_alert=True)
            return

        db.add_log(
            level="INFO",
            message=f"User {callback.from_user.username or callback.from_user.id} accessed file system",
            user_id=callback.from_user.id,
            command="dir_cmd"
        )

        await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
        await callback.answer()

    @dp.callback_query(F.data.startswith("dir:"))
    async def handle_directory(callback: types.CallbackQuery):
        if not await check_user_permission(callback.from_user.id, callback.message.chat.type):
            await callback.answer()
            return

        path = callback.data.split(":", 1)[1]
        response, keyboard = await generate_file_manager(path)
        
        if not response:
            await callback.answer("❌ Directory not found!", show_alert=True)
            return

        await callback.message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
        await callback.answer()

    @dp.callback_query(F.data.startswith("file:"))
    async def handle_file_actions(callback: types.CallbackQuery):
        if not await check_user_permission(callback.from_user.id, callback.message.chat.type):
            await callback.answer()
            return

        file_path = callback.data.split(":", 1)[1]
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        size_str = f"{file_size/1024:.1f}K" if file_size < 1024*1024 else f"{file_size/(1024*1024):.1f}M"
        
        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(text="⬇️", callback_data=f"download:{file_path}"),
            types.InlineKeyboardButton(text="✏️", callback_data=f"rename:{file_path}"),
            types.InlineKeyboardButton(text="🗑️", callback_data=f"delete:{file_path}"),
            types.InlineKeyboardButton(text="🔙", callback_data=f"dir:{os.path.dirname(file_path)}")
        )
        
        await callback.message.edit_text(
            f"📄 <b>File:</b> <code>{file_name}</code>\n"
            f"📦 <b>Size:</b> {size_str}\n"
            f"📂 <b>Path:</b> <code>{os.path.dirname(file_path)}</code>",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        await callback.answer()

    # ... (keep other handlers unchanged from previous versions)

    @dp.callback_query(F.data.startswith("download:"))
    async def download_file(callback: types.CallbackQuery):
        if not await check_user_permission(callback.from_user.id, callback.message.chat.type):
            await callback.answer()
            return

        file_path = callback.data.split(":", 1)[1]
        try:
            with open(file_path, "rb") as file:
                await callback.message.answer_document(
                    document=types.BufferedInputFile(
                        file.read(),
                        filename=os.path.basename(file_path)
                    )
                )
            db.add_log(
                level="INFO",
                message=f"User {callback.from_user.username or callback.from_user.id} downloaded file: {file_path}",
                user_id=callback.from_user.id,
                command="download_file"
            )
            
            await callback.answer("✅ File sent!")
        except Exception as e:
            logger.error(f"Download error: {e}")
            await callback.answer(f"❌ Download failed: {str(e)}", show_alert=True)

    @dp.callback_query(F.data.startswith("delete:"))
    async def delete_item(callback: types.CallbackQuery):
        if not await check_user_permission(callback.from_user.id, callback.message.chat.type):
            await callback.answer()
            return

        path = callback.data.split(":", 1)[1]
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                action = "folder"
            else:
                os.remove(path)
                action = "file"
            
            db.add_log(
                level="WARNING",
                message=f"User {callback.from_user.username or callback.from_user.id} deleted {action}: {path}",
                user_id=callback.from_user.id,
                command="delete_file"
            )
            
            await callback.answer(f"✅ {action.capitalize()} deleted!")
            await handle_directory(callback)
        except Exception as e:
            logger.error(f"Delete error: {e}")
            await callback.answer(f"❌ Error: {str(e)}", show_alert=True)

    @dp.callback_query(F.data.startswith("rename:"))
    async def rename_item(callback: types.CallbackQuery, state: FSMContext):
        if not await check_user_permission(callback.from_user.id, callback.message.chat.type):
            await callback.answer()
            return

        path = callback.data.split(":", 1)[1]
        await state.update_data(
            rename_target=path,
            original_message=callback.message
        )
        
        await callback.message.edit_text(
            f"✏️ <b>Rename:</b>\n"
            f"<code>{path}</code>\n\n"
            "Enter new name:",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith("upload_to:"))
    async def handle_upload_to(callback: types.CallbackQuery, state: FSMContext):
        if not await check_user_permission(callback.from_user.id, callback.message.chat.type):
            await callback.answer()
            return

        folder_path = callback.data.split(":", 1)[1]
        await state.update_data(
            upload_folder=folder_path,
            original_message=callback.message
        )
        
        await callback.message.edit_text(
            f"⬆️ <b>File upload:</b>\n"
            f"Folder: <code>{folder_path}</code>\n\n"
            "Send the file you want to upload:",
            parse_mode="HTML"
        )
        await callback.answer()

    @dp.message(F.text & ~F.text.startswith("/"))
    async def handle_rename(message: types.Message, state: FSMContext):
        if not await check_user_permission(message.from_user.id, message.chat.type):
            return

        data = await state.get_data()
        if "rename_target" not in data:
            return

        old_path = data["rename_target"]
        original_message = data["original_message"]
        new_name = message.text.strip()
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        
        try:
            os.rename(old_path, new_path)
            
            db.add_log(
                level="INFO",
                message=f"User {message.from_user.username or message.from_user.id} renamed file: {old_path} -> {new_path}",
                user_id=message.from_user.id,
                command="rename_file"
            )
            
            await message.answer(f"✅ Renamed to:\n<code>{new_path}</code>", parse_mode="HTML")
            
            # Refresh original file manager
            response, keyboard = await generate_file_manager(os.path.dirname(new_path))
            if response and keyboard:
                await original_message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
            
            await state.clear()
        except Exception as e:
            await message.answer(f"❌ Error: {str(e)}")

    @dp.message(F.document | F.photo | F.video | F.audio | F.voice)
    async def handle_file_upload(message: types.Message, state: FSMContext):
        if not await check_user_permission(message.from_user.id, message.chat.type):
            return

        data = await state.get_data()
        if "upload_folder" not in data:
            return

        target_folder = data["upload_folder"]
        original_message = data["original_message"]
        
        try:
            if message.document:
                file = message.document
                file_name = file.file_name
            elif message.photo:
                file = message.photo[-1]
                file_name = f"photo_{file.file_unique_id}.jpg"
            elif message.video:
                file = message.video
                file_name = file.file_name or f"video_{file.file_unique_id}.mp4"
            elif message.audio:
                file = message.audio
                file_name = file.file_name or f"audio_{file.file_unique_id}.mp3"
            elif message.voice:
                file = message.voice
                file_name = f"voice_{file.file_unique_id}.ogg"
            else:
                return

            file_path = os.path.join(target_folder, file_name)
            await message.bot.download(file, destination=file_path)
            
            db.add_log(
                level="INFO",
                message=f"User {message.from_user.username or message.from_user.id} uploaded file: {file_path}",
                user_id=message.from_user.id,
                command="file_upload"
            )
            
            await message.answer(f"✅ File uploaded:\n<code>{file_path}</code>", parse_mode="HTML")
            
            # Refresh file manager
            response, keyboard = await generate_file_manager(target_folder)
            if response and keyboard:
                await original_message.edit_text(response, parse_mode="HTML", reply_markup=keyboard)
            
            await state.clear()
        except Exception as e:
            await message.answer(f"❌ Upload error: {str(e)}")