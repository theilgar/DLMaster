"""🎭 .clone & .revert — Profil klonlama və əvvəlki vəziyyətinə qaytarma modulu.

  .clone  — reply edilən istifadəçinin profilini (ad, soyad, bio və profil şəkli) klonlayır
  .revert — profili klonlanmadan əvvəlki orijinal vəziyyətinə qaytarır
"""
import asyncio
from html import escape

from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPhoto

from core.userbot_api import User, logger, safe_edit

DONE_VISIBLE = 4

# Orijinal profil məlumatlarını yaddaşda saxlamaq üçün struktur
_BACKUP = {}


def register(ub):
    @ub.command("clone", pattern=r"^\.clone$",
                help=("istifadəçinin profilini klonla",
                      "Mesaja reply edib <code>.clone</code> yazdıqda hədəf şəxsin "
                      "<b>adı</b>, <b>soyadı</b>, <b>bioqrafiyası</b> və <b>profil şəkli</b> "
                      "sizin hesabınıza kopyalanır.\n\n"
                      "Əvvəlki vəziyyətə qayıtmaq üçün: <code>.revert</code>"))
    async def on_clone(event):
        client = event.client

        if not event.is_reply:
            await safe_edit(event, "ℹ️ Klonlamaq istədiyin istifadəçinin mesajına <b>reply</b> et.")
            return

        reply_msg = await event.get_reply_message()
        user = await reply_msg.get_sender()

        if not isinstance(user, User):
            await safe_edit(event, "❌ Yalnız istifadəçi profilləri klonlana bilər (kanal və ya qrup yox).")
            return

        if getattr(user, "is_self", False):
            await safe_edit(event, "ℹ️ Öz hesabınızı klonlaya bilməzsiniz.")
            return

        await safe_edit(event, "🔄 <i>İstifadəçi məlumatları toplanır...</i>")

        try:
            # Əgər ehtiyat nüsxə yoxdursa, cari profil məlumatlarımızı saxlayırıq
            if "original" not in _BACKUP:
                me = await client.get_me()
                full_me = await client(GetFullUserRequest(me.id))
                _BACKUP["original"] = {
                    "first_name": me.first_name or "",
                    "last_name": me.last_name or "",
                    "about": getattr(full_me.full_user, "about", "") or "",
                }
                _BACKUP["photos"] = []

            # 1. Hədəf istifadəçinin məlumatlarını əldə et
            full_user_data = await client(GetFullUserRequest(user.id))
            bio = getattr(full_user_data.full_user, "about", "") or ""

            first_name = user.first_name or ""
            last_name = user.last_name or ""

            # 2. Ad, soyad və bioqrafiyanı dəyiş
            await client(UpdateProfileRequest(
                first_name=first_name,
                last_name=last_name,
                about=bio
            ))

            # 3. Profil şəklini yüklə və tətbiq et
            await safe_edit(event, "🖼 <i>Profil şəkli yüklənir...</i>")
            photo_bytes = await client.download_profile_photo(user, file=bytes)

            if photo_bytes:
                # Xətanın qarşısını almaq üçün fayl adı və .jpg uzantısı qeyd olunur
                uploaded_photo = await client.upload_file(photo_bytes, file_name="avatar.jpg")
                res = await client(UploadProfilePhotoRequest(fallback=False, file=uploaded_photo))
                photo_obj = getattr(res, "photo", None)
                if photo_obj:
                    _BACKUP.setdefault("photos", []).append(
                        InputPhoto(
                            id=photo_obj.id,
                            access_hash=photo_obj.access_hash,
                            file_reference=photo_obj.file_reference
                        )
                    )

            target_name = escape(first_name + (" " + last_name if last_name else ""))
            await safe_edit(
                event,
                f"✅ <b>{target_name}</b> hesabı uğurla klonlandı!\n"
                "<i>Geri qaytarmaq üçün: <code>.revert</code></i>"
            )

            await asyncio.sleep(DONE_VISIBLE)
            try:
                await event.delete()
            except Exception:
                pass

        except Exception as e:
            logger.info(f".clone xətası: {e}")
            await safe_edit(event, f"❌ <b>.clone xətası:</b> <code>{escape(str(e))[:200]}</code>")

    @ub.command("revert", pattern=r"^\.revert$",
                help=("klonlanmış profili geri qaytar",
                      "Klonlanmış profili əvvəlki orijinal halına (ad, soyad, bio və avatar) qaytarır."))
    async def on_revert(event):
        client = event.client

        if "original" not in _BACKUP:
            await safe_edit(
                event,
                "ℹ️ Saxlanılmış orijinal profil məlumatı tapılmadı "
                "(əvvəlcə <code>.clone</code> istifadə edilməlidir)."
            )
            return

        await safe_edit(event, "🔄 <i>Orijinal profil bərpa olunur...</i>")

        try:
            orig = _BACKUP["original"]

            # 1. Ad, soyad və bioqrafiyanı əvvəlki vəziyyətinə qaytar
            await client(UpdateProfileRequest(
                first_name=orig["first_name"],
                last_name=orig["last_name"],
                about=orig["about"]
            ))

            # 2. Klonlama zamanı əlavə edilmiş şəkli sil (əvvəlki avatar ön plana qayıdır)
            uploaded_photos = _BACKUP.get("photos", [])
            if uploaded_photos:
                try:
                    await client(DeletePhotosRequest(id=uploaded_photos))
                except Exception as pe:
                    logger.info(f".revert şəkil silinmədi: {pe}")

            # Yaddaşı sıfırla
            _BACKUP.clear()

            await safe_edit(event, "✅ Profiliniz uğurla əvvəlki vəziyyətinə qaytarıldı!")

            await asyncio.sleep(DONE_VISIBLE)
            try:
                await event.delete()
            except Exception:
                pass

        except Exception as e:
            logger.info(f".revert xətası: {e}")
            await safe_edit(event, f"❌ <b>.revert xətası:</b> <code>{escape(str(e))[:200]}</code>")
