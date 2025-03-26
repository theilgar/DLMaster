# 🎵 DLLMasterBot - Telegram Musiqi Botu

Telegram üçün güclü musiqi botu! YouTube və Spotify playlistlərini idarə edin, fərdi mahnılar axtarın və qruplar üçün inteqrasiya edin.

## 🌟 Xüsusiyyətlər
- 🎶 **YouTube və Spotify playlist dəstəyi** (Spotify üçün YouTube vasitəsilə)
- 🔎 `/music` - Mahnı axtarışı və endirmə  
- 📂 `/my_songs` - Yükləmə tarixçəsinə baxış  
- 👥 **Qruplarda istifadə** (admin hüquqları tələb olunur)  

---

## 🖥 Quraşdırma (Debian/Ubuntu)
Aşağıdakı addımları izləyərək botu quraşdıra bilərsiniz.

### 📦 1. Əsas Tələblər
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv ffmpeg

🐍 2. Python Virtual Mühiti

python3 -m venv bot-env
source bot-env/bin/activate
pip3 install wheel
pip3 install -r requirements.txt

⚙️ 3. Konfiqurasiya

Botun işləməsi üçün .env faylını yaradın və aşağıdakı kodu daxil edin:

SPOTIFY_CLIENT_ID = "your_client_id"
SPOTIFY_CLIENT_SECRET = "your_client_secret"
TELEGRAM_BOT_TOKEN = "your_bot_token"



⸻

🚀 Botu İşə Salmaq

Aşağıdakı əmrləri icra edərək botu başladın:

source bot-env/bin/activate
python3 app.py
