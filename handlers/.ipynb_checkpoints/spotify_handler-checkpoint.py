import spotipy
import mutagen
from spotipy.oauth2 import SpotifyClientCredentials
from handlers.utilities import format_duration
import logging

# Loqlama konfiqurasiyası
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class SpotifyManager:
    def __init__(self, client_id: str, client_secret: str):
        try:
            self.sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret
            ))
            logger.info("Spotify API ilə əlaqə quruldu.")
        except Exception as e:
            logger.error(f"Spotify API ilə əlaqə xətası: {str(e)}")
            raise RuntimeError(f"Spotify API ilə əlaqə xətası: {str(e)}")

    def get_playlist_data(self, playlist_id: str) -> dict:
        """Spotify playlist məlumatlarını alır"""
        try:
            logger.info(f"Playlist məlumatları əldə edilir: {playlist_id}")
            playlist = self.sp.playlist(playlist_id)
            return {
                "name": playlist.get('name', 'Naməlum Playlist'),
                "tracks": self._process_tracks(playlist.get('tracks', {})),
                "total": playlist.get('tracks', {}).get('total', 0),
                "duration": self._format_duration(playlist.get('tracks', {}))
            }
        except Exception as e:
            logger.error(f"Spotify API xətası: {str(e)}")
            raise RuntimeError(f"Spotify API xətası: {str(e)}")

    def get_track_data(self, track_id: str) -> dict:
        """Spotify-dan verilən track ID üzrə məlumatları alır"""
        try:
            logger.info(f"Track məlumatları əldə edilir: {track_id}")
            track = self.sp.track(track_id)
            return {
                "name": track.get('name', ''),
                "artists": [{"name": a.get('name', '')} for a in track.get('artists', [])],
                "duration_ms": track.get('duration_ms', 0) or 0,
                "album": {"name": track.get('album', {}).get('name', '')}
            }
        except Exception as e:
            logger.error(f"Spotify track məlumatı xətası: {str(e)}")
            raise RuntimeError(f"Spotify track məlumatı xətası: {str(e)}")

    def _process_tracks(self, tracks: dict) -> list:
        """Playlist daxilindəki track-ləri işləyir"""
        return [{
            "name": item.get('track', {}).get('name', ''),
            "artists": [{"name": a.get('name', '')} for a in item.get('track', {}).get('artists', [])],
            "duration_ms": item.get('track', {}).get('duration_ms', 0) or 0,
            "album": {"name": item.get('track', {}).get('album', {}).get('name', '')}
        } for item in tracks.get('items', []) if item.get('track')]

    def _format_duration(self, tracks: dict) -> str:
        """Playlist-in ümumi müddətini hesablayır"""
        total_ms = sum(
            item.get('track', {}).get('duration_ms', 0) or 0
            for item in tracks.get('items', [])
        )
        return format_duration(total_ms)

    def apply_metadata(self, file_path: str, track: dict):
        """Yüklənən mahnıya metadata əlavə edir"""
        try:
            audio = mutagen.File(file_path)
            audio['title'] = track.get('name', '')[:64]
            audio['artist'] = ", ".join([a.get('name', '') for a in track.get('artists', [])][:3])[:64]
            audio['album'] = track.get('album', {}).get('name', '')[:64]
            audio.save()
            logger.info(f"Metadata uğurla əlavə edildi: {file_path}")
        except Exception as e:
            logger.error(f"Metadata xətası: {str(e)}")