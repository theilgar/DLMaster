import logging

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