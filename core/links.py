"""
Musiqi linklərinin tanınması — core/links.py

parse_link(mətn) → dict | None
  kind:
    yt_video        — YouTube / YouTube Music / Shorts video        (id)
    yt_video_list   — siyahının içindən açılmış video (watch?v=..&list=..)   (id, list)
    yt_list         — YouTube playlist, YT Music albomu (OLAK5uy_), Mix / Radio (RD...)   (list)
    sp_track / sp_album / sp_playlist / sp_artist      (id)
    sp_jam          — Spotify Jam (canlı sessiya)
    sp_short        — spotify.link / spotify.app.link qısa linki (url — açılmalıdır)
"""
import re
from urllib.parse import urlparse, parse_qs

_URL_RE = re.compile(r"https?://[^\s<>\"']+|spotify:[a-z]+:[A-Za-z0-9]+", re.I)
_SP_PATH = re.compile(
    r"^/(?:intl-[a-z]{2}(?:-[a-z]+)?/)?(?:embed/)?(track|album|playlist|artist|socialsession|jam)/([A-Za-z0-9]+)", re.I
)
_YT_ID = re.compile(r"^[\w-]{11}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
_SP_HOSTS = {"open.spotify.com", "play.spotify.com"}
_SP_SHORT = {"spotify.link", "spotify.app.link"}


def find_urls(text: str) -> list:
    return _URL_RE.findall(text or "")


def parse_url(url: str):
    url = url.rstrip(").,!?»")
    if url.lower().startswith("spotify:"):
        _, kind, sid = url.split(":", 2)
        kind = kind.lower()
        return {"kind": f"sp_{kind}", "id": sid, "url": url} if kind in ("track", "album", "playlist", "artist") else None

    try:
        u = urlparse(url)
    except ValueError:
        return None
    host = (u.netloc or "").lower().split(":")[0]
    qs = parse_qs(u.query)

    if host in _SP_SHORT:
        return {"kind": "sp_short", "url": url}
    if host in _SP_HOSTS:
        m = _SP_PATH.match(u.path)
        if not m:
            return None
        kind, sid = m.group(1).lower(), m.group(2)
        if kind in ("socialsession", "jam"):
            return {"kind": "sp_jam", "id": sid, "url": url}
        return {"kind": f"sp_{kind}", "id": sid, "url": url}

    if host in _YT_HOSTS:
        vid = None
        if host.endswith("youtu.be"):
            vid = u.path.strip("/").split("/")[0]
        elif u.path == "/watch":
            vid = (qs.get("v") or [None])[0]
        else:
            m = re.match(r"^/(?:shorts|embed|live|v)/([\w-]{11})", u.path)
            vid = m.group(1) if m else None
        lst = (qs.get("list") or [None])[0]
        if vid and not _YT_ID.match(vid):
            vid = None
        if lst and lst.upper() in ("WL", "LL"):        # "Sonra izlə" / "Bəyəndiklərim" — şəxsi siyahılar
            lst = None
        if vid and lst:
            return {"kind": "yt_video_list", "id": vid, "list": lst, "url": url}
        if vid:
            return {"kind": "yt_video", "id": vid, "url": url}
        if lst and u.path in ("/playlist", "/watch", "/browse") or (lst and host == "music.youtube.com"):
            return {"kind": "yt_list", "list": lst, "url": url}
    return None


def parse_link(text: str):
    """Mətndəki ilk tanınan musiqi linki."""
    for url in find_urls(text):
        parsed = parse_url(url)
        if parsed:
            return parsed
    return None


def is_music_link(text: str) -> bool:
    return parse_link(text) is not None


def yt_list_url(list_id: str, video_id: str = None) -> str:
    """Siyahını yt-dlp-nin başa düşəcəyi formada: Mix/Radio üçün watch?v=..&list=RD.."""
    if list_id.startswith("RD") and video_id:
        return f"https://www.youtube.com/watch?v={video_id}&list={list_id}"
    return f"https://www.youtube.com/playlist?list={list_id}"


def list_kind_label(list_id: str) -> str:
    if list_id.startswith("OLAK5uy_"):
        return "💿 YouTube Music albomu"
    if list_id.startswith("RDCLAK"):
        return "📻 YouTube Music radio"
    if list_id.startswith("RD"):
        return "🔀 YouTube Mix / Radio"
    return "📃 YouTube playlist"
