import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class BassProcessor:
    @staticmethod
    def apply_bass(input_path: str, low_start: int, low_end: int, gain_db: int = 10) -> str:
        """
        Bass effektini tətbiq edir və yeni faylı qaytarır.
        """
        output_dir = Path("download") / "bass_effects"
        output_dir.mkdir(exist_ok=True)
        
        filename = Path(input_path).stem
        output_path = str(output_dir / f"{filename}_bass.mp3")

        center_freq = (low_start + low_end) / 2
        bandwidth = low_end - low_start

        try:
            subprocess.run([
                'ffmpeg',
                '-i', input_path,
                '-af',
                f'equalizer=f={center_freq}:width_type=h:width={bandwidth}:gain={gain_db}',
                '-y',
                output_path
            ], check=True)
            
            return output_path
        except subprocess.CalledProcessError as e:
            logger.error(f"Bass effekti xətası: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Ümumi xəta: {str(e)}")
            raise