import logging
import random

logger = logging.getLogger(__name__)

def get_cookies_from_browser(browser: str = "firefox") -> tuple:
    """
    Brauzerdən cookies alır və yt-dlp üçün uyğun formatda qaytarır.
    """
    try:
        cookies = (browser,)  # Brauzer adını qaytarır
        logger.info(f"{browser} brauzerindən cookies istifadə edilir.")
        return cookies
    except Exception as e:
        logger.error(f"Cookies alınarkən xəta baş verdi: {str(e)}")
        raise RuntimeError(f"Cookies alınarkən xəta: {str(e)}")


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
]

def get_random_user_agent():
    try:
        user_agent = random.choice(USER_AGENTS)
        logger.info(f"Seçilmiş User-Agent: {user_agent}")
        return user_agent
    except Exception as e:
        logger.error(f"Useragentlər alınarkən xəta baş verdi: {str(e)}")
        raise RuntimeError(f"Useragentlər alınarkən xəta: {str(e)}")