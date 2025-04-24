# spotify_handler.py
import spotipy
import mutagen
from spotipy.oauth2 import SpotifyClientCredentials
from core.utilities import format_duration
import logging
import requests
from mutagen.mp4 import MP4, MP4Cover

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
            logger.info("Spotify API connection established.")
        except Exception as e:
            logger.error(f"Spotify API connection error: {str(e)}")
            raise RuntimeError(f"Spotify API connection error: {str(e)}")

    def search_track(self, query: str) -> dict:
        """Search for a track on Spotify and return the first result"""
        try:
            results = self.sp.search(q=query, type='track', limit=1)
            return results['tracks']['items'][0] if results['tracks']['items'] else None
        except Exception as e:
            logger.error(f"Spotify search error: {str(e)}")
            return None

    def get_playlist_data(self, playlist_id: str) -> dict:
        try:
            logger.info(f"Fetching playlist data: {playlist_id}")
            playlist = self.sp.playlist(playlist_id)
            
            tracks = []
            results = self.sp.playlist_items(playlist_id, additional_types=['track'])
            tracks.extend(results['items'])
            
            while results['next']:
                results = self.sp.next(results)
                tracks.extend(results['items'])
            
            return {
                "name": playlist.get('name', 'Unknown Playlist'),
                "tracks": self._process_tracks({"items": tracks}),
                "total": len(tracks),
                "duration": self._format_duration({"items": tracks})
            }
        except Exception as e:
            logger.error(f"Spotify API error: {str(e)}")
            raise RuntimeError(f"Spotify API error: {str(e)}")

    def _process_tracks(self, tracks: dict) -> list:
        return [{
            "name": item.get('track', {}).get('name', ''),
            "artists": [{"name": a.get('name', '')} for a in item.get('track', {}).get('artists', [])],
            "duration_ms": item.get('track', {}).get('duration_ms', 0) or 0,
            "duration_formatted": format_duration((item.get('track', {}).get('duration_ms', 0) or 0) // 1000),
            "album": {
                "name": item.get('track', {}).get('album', {}).get('name', ''),
                "images": item.get('track', {}).get('album', {}).get('images', [])
            }
        } for item in tracks.get('items', []) if item.get('track')]

    def _format_duration(self, tracks: dict) -> str:
        total_ms = sum(
            item.get('track', {}).get('duration_ms', 0) or 0
            for item in tracks.get('items', [])
        )
        total_seconds = total_ms // 1000
        return format_duration(total_seconds)

    def apply_metadata(self, file_path: str, track: dict):
        try:
            audio = mutagen.File(file_path)
            audio['title'] = track.get('name', '')[:64]
            audio['artist'] = ", ".join([a.get('name', '') for a in track.get('artists', [])][:3])[:64]
            audio['album'] = track.get('album', {}).get('name', '')[:64]
            
            # Only use Spotify thumbnails
            album = track.get('album', {})
            if album.get('images'):
                # Get highest resolution image (first in array)
                image_url = album['images'][0]['url'].split('?')[0]  # Remove any URL params
                response = requests.get(image_url)
                if response.status_code == 200:
                    if file_path.lower().endswith('.m4a'):
                        cover = MP4Cover(response.content)
                        audio["covr"] = [cover]
                    else:
                        audio.tags.add(
                            mutagen.id3.APIC(
                                encoding=3,
                                mime='image/jpeg',
                                type=3,
                                data=response.content
                            )
                        )
                    logger.info(f"Added Spotify thumbnail: {image_url}")
            else:
                logger.info("No Spotify thumbnail available - skipping")

            audio.save()
            logger.info(f"Metadata applied: {file_path}")
        except Exception as e:
            logger.error(f"Metadata error: {str(e)}")

    def get_track_data(self, track_id: str) -> dict:
        try:
            logger.info(f"Fetching track data: {track_id}")
            track = self.sp.track(track_id)
            return {
                "name": track.get('name', 'Unknown Track'),
                "artists": [{"name": a.get('name', '')} for a in track.get('artists', [])],
                "duration_ms": track.get('duration_ms', 0) or 0,
                "album": {
                    "name": track.get('album', {}).get('name', ''),
                    "images": track.get('album', {}).get('images', [])
                }
            }
        except Exception as e:
            logger.error(f"Spotify API error: {str(e)}")
            raise RuntimeError(f"Spotify API error: {str(e)}")

    def get_album_data(self, album_id: str) -> dict:
        try:
            logger.info(f"Fetching album data: {album_id}")
            album = self.sp.album(album_id)
        
            tracks = []
            results = self.sp.album_tracks(album_id)
            tracks.extend(results['items'])
        
            while results['next']:
                results = self.sp.next(results)
                tracks.extend(results['items'])
        
            return {
                "name": album.get('name', 'Unknown Album'),
                "artist": album.get('artists', [{}])[0].get('name', 'Unknown Artist'),
                "tracks": self._process_album_tracks({"items": tracks}),
                "total": len(tracks),
                "duration": self._format_duration({"items": tracks}),
                "images": album.get('images', [])
            }
        except Exception as e:
            logger.error(f"Spotify API error: {str(e)}")
            raise RuntimeError(f"Spotify API error: {str(e)}")

    def _process_album_tracks(self, tracks: dict) -> list:
        return [{
            "name": item.get('name', ''),
            "artists": [{"name": a.get('name', '')} for a in item.get('artists', [])],
            "duration_ms": item.get('duration_ms', 0),
            "duration_formatted": format_duration(item.get('duration_ms', 0) // 1000),
            "album": {
                "name": "Album name",  # Placeholder, actual album name comes from parent
                "images": []
            }
        } for item in tracks.get('items', [])]