# plugins/fx_plugin.py
import logging
from aiogram import types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import F

logger = logging.getLogger(__name__)

class FXPlugin:
    def __init__(self, context):
        self.context = context
        if not hasattr(context, 'user_effects'):
            context.user_effects = {}

    async def setup(self):
        dp = self.context.dp
        dp.message.register(self.fx_command_handler, Command("fx"))
        dp.callback_query.register(self.bass_handler, F.data.startswith("bass_"))
        dp.callback_query.register(self.tempo_handler, F.data.startswith("tempo_"))
        dp.callback_query.register(self.main_menu_handler, F.data == "main_menu")
        logger.info("✅ FX plugin loaded (DB-free version)")

    async def fx_command_handler(self, message: types.Message):
        """Handle /fx command with main menu"""
        user_id = message.from_user.id
        if user_id not in self.context.user_effects:
            self.context.user_effects[user_id] = {
                'bass': {'min_hz': 20, 'max_hz': 60, 'db_gain': 0},
                'tempo': {'value': 1.0}
            }

        builder = InlineKeyboardBuilder()
        builder.button(text="🎛 Bass Settings", callback_data="bass_menu")
        builder.button(text="⏱ Tempo Settings", callback_data="tempo_menu")
        builder.adjust(2)
        
        await message.answer(
            "**🔧 FX Control Panel**\n"
            "Current Status (Session-only):\n"
            f"Bass: {self.context.user_effects[user_id]['bass']['min_hz']}-{self.context.user_effects[user_id]['bass']['max_hz']}Hz "
            f"({self.context.user_effects[user_id]['bass']['db_gain']}dB)\n"
            f"Tempo: {self.context.user_effects[user_id]['tempo']['value']}x", 
            reply_markup=builder.as_markup()
        )

    async def bass_handler(self, callback: types.CallbackQuery):
        """Handle bass adjustment menu"""
        user_id = callback.from_user.id
        data = callback.data.split('_')
        
        if user_id not in self.context.user_effects:
            self.context.user_effects[user_id] = {
                'bass': {'min_hz': 20, 'max_hz': 60, 'db_gain': 0},
                'tempo': {'value': 1.0}
            }
        
        user_settings = self.context.user_effects[user_id]['bass']
        
        if data[1] == "menu":
            builder = InlineKeyboardBuilder()
            builder.button(text=f"- MIN ({user_settings['min_hz']}Hz)", callback_data="bass_min_down")
            builder.button(text=f"+ MIN ({user_settings['min_hz']}Hz)", callback_data="bass_min_up")
            builder.button(text=f"- MAX ({user_settings['max_hz']}Hz)", callback_data="bass_max_down")
            builder.button(text=f"+ MAX ({user_settings['max_hz']}Hz)", callback_data="bass_max_up")
            builder.button(text=f"- GAIN ({user_settings['db_gain']}dB)", callback_data="bass_db_down")
            builder.button(text=f"+ GAIN ({user_settings['db_gain']}dB)", callback_data="bass_db_up")
            builder.button(text="🔙 Back", callback_data="main_menu")
            builder.adjust(2, 2, 2, 1)
            
            await callback.message.edit_text(
                f"**🔊 Bass Control**\n"
                f"Frequency Range: {user_settings['min_hz']}-{user_settings['max_hz']}Hz\n"
                f"Boost/Cut: {user_settings['db_gain']}dB",
                reply_markup=builder.as_markup()
            )
        else:
            param, action = data[1], data[2]
            step = 5 if param in ['min', 'max'] else 1
            
            if param == 'min':
                new_val = user_settings['min_hz'] + (step if action == 'up' else -step)
                new_val = max(20, min(new_val, user_settings['max_hz'] - 5))
                user_settings['min_hz'] = new_val
            elif param == 'max':
                new_val = user_settings['max_hz'] + (step if action == 'up' else -step)
                new_val = min(60, max(new_val, user_settings['min_hz'] + 5))
                user_settings['max_hz'] = new_val
            else:
                new_val = user_settings['db_gain'] + (step if action == 'up' else -step)
                new_val = max(-20, min(new_val, 20))
                user_settings['db_gain'] = new_val
            
            builder = InlineKeyboardBuilder()
            builder.button(text=f"- MIN ({user_settings['min_hz']}Hz)", callback_data="bass_min_down")
            builder.button(text=f"+ MIN ({user_settings['min_hz']}Hz)", callback_data="bass_min_up")
            builder.button(text=f"- MAX ({user_settings['max_hz']}Hz)", callback_data="bass_max_down")
            builder.button(text=f"+ MAX ({user_settings['max_hz']}Hz)", callback_data="bass_max_up")
            builder.button(text=f"- GAIN ({user_settings['db_gain']}dB)", callback_data="bass_db_down")
            builder.button(text=f"+ GAIN ({user_settings['db_gain']}dB)", callback_data="bass_db_up")
            builder.button(text="🔙 Back", callback_data="main_menu")
            builder.adjust(2, 2, 2, 1)
            
            await callback.message.edit_text(
                f"**🔊 Bass Control**\n"
                f"Frequency Range: {user_settings['min_hz']}-{user_settings['max_hz']}Hz\n"
                f"Boost/Cut: {user_settings['db_gain']}dB",
                reply_markup=builder.as_markup()
            )
        
        await callback.answer()

    async def tempo_handler(self, callback: types.CallbackQuery):
        """Handle tempo adjustment menu"""
        user_id = callback.from_user.id
        data = callback.data.split('_')
        
        if user_id not in self.context.user_effects:
            self.context.user_effects[user_id] = {
                'bass': {'min_hz': 20, 'max_hz': 60, 'db_gain': 0},
                'tempo': {'value': 1.0}
            }
        
        user_settings = self.context.user_effects[user_id]['tempo']
        
        if data[1] == "menu":
            builder = InlineKeyboardBuilder()
            builder.button(text="- TEMPO", callback_data="tempo_down")
            builder.button(text="+ TEMPO", callback_data="tempo_up")
            builder.button(text="🔙 Back", callback_data="main_menu")
            builder.adjust(2, 1)
            
            await callback.message.edit_text(
                f"**⏱ Tempo Control**\n"
                f"Current Value: {user_settings['value']}x",
                reply_markup=builder.as_markup()
            )
        else:
            step = 0.1
            new_val = user_settings['value'] + (step if data[1] == 'up' else -step)
            new_val = max(0.5, min(new_val, 2.0))
            user_settings['value'] = round(new_val, 1)
            
            builder = InlineKeyboardBuilder()
            builder.button(text="- TEMPO", callback_data="tempo_down")
            builder.button(text="+ TEMPO", callback_data="tempo_up")
            builder.button(text="🔙 Back", callback_data="main_menu")
            builder.adjust(2, 1)
            
            await callback.message.edit_text(
                f"**⏱ Tempo Control**\n"
                f"Current Value: {user_settings['value']}x",
                reply_markup=builder.as_markup()
            )
        
        await callback.answer()

    async def main_menu_handler(self, callback: types.CallbackQuery):
        """Return to main FX menu"""
        user_id = callback.from_user.id
        if user_id not in self.context.user_effects:
            self.context.user_effects[user_id] = {
                'bass': {'min_hz': 20, 'max_hz': 60, 'db_gain': 0},
                'tempo': {'value': 1.0}
            }
        
        settings = self.context.user_effects[user_id]
        
        builder = InlineKeyboardBuilder()
        builder.button(text="🎛 Bass Settings", callback_data="bass_menu")
        builder.button(text="⏱ Tempo Settings", callback_data="tempo_menu")
        builder.adjust(2)
        
        await callback.message.edit_text(
            "**🔧 FX Control Panel**\n"
            "Current Status (Session-only):\n"
            f"Bass: {settings['bass']['min_hz']}-{settings['bass']['max_hz']}Hz "
            f"({settings['bass']['db_gain']}dB)\n"
            f"Tempo: {settings['tempo']['value']}x",
            reply_markup=builder.as_markup()
        )

async def setup(context):
    plugin = FXPlugin(context)
    await plugin.setup()