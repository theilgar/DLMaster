"""
🧠 yt-dlp işçi prosesi — core/ytdl_worker.py

Botun əsas prosesi bir event loop + GIL-dir: yt-dlp-nin ağır Python işi (səhifə / JSON parse, format seçimi,
cookies deşifrəsi) thread-də işləsə də GIL-i tutur və bot "donur" (mesajlara gec cavab, düymələr ilişir).
Bu modul ayrıca Python prosesi kimi işləyir (`python -m core.ytdl_worker`) — hər proses öz nüvəsində,
öz GIL-i ilə. core/cpu_pool.py bu proseslərdən hovuz qurur.

Protokol (stdin / stdout, 4 bayt uzunluq + pickle):
  → (task_id, func_name, args)
  ← (task_id, "ok", result) | (task_id, "cancel", mesaj) | (task_id, "err", mesaj)
stdout yalnız protokol üçündür — yt-dlp-nin bütün çapı stderr-ə (bot konsoluna) gedir.

Bu fayldakı funksiyalar "🧵 Thread" rejimində əsas prosesdə də birbaşa çağırılır.
"""
import os
import pickle
import signal
import struct
import sys

_BIG_KEYS = ("formats", "thumbnails", "automatic_captions", "subtitles", "heatmap", "requested_formats",
             "fragments", "http_headers", "_format_sort_fields", "chapters")


def _slim(info):
    """Prosesdən qaytarılan info — böyük siyahılar atılır (pickle / RAM)."""
    if not isinstance(info, dict):
        return info
    out = {k: v for k, v in info.items() if k not in _BIG_KEYS}
    if isinstance(out.get("entries"), list):
        out["entries"] = [_slim(e) for e in out["entries"] if e]
    if isinstance(out.get("requested_downloads"), list):
        out["requested_downloads"] = [{"filepath": d.get("filepath")} for d in out["requested_downloads"]
                                      if isinstance(d, dict)]
    return out


# ───────────────────────── tapşırıqlar ─────────────────────────
def ping():
    return os.getpid()


def ytdl_extract(url: str, opts: dict):
    """extract_info(download=False) — axtarış, playlist, Mix."""
    from yt_dlp import YoutubeDL
    with YoutubeDL(dict(opts)) as ydl:
        info = ydl.extract_info(url, download=False)
        return _slim(ydl.sanitize_info(info))


def ytdl_download(url: str, opts: dict, cancel_path: str = None):
    """Yükləmə. cancel_path faylı yarananda (⏹ Dayandır) növbəti hissədə kəsilir."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadCancelled

    def hook(_):
        if cancel_path and os.path.exists(cancel_path):
            raise DownloadCancelled("İstifadəçi yükləməni dayandırdı")

    opts = dict(opts)
    opts["progress_hooks"] = [hook]
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloads = info.get("requested_downloads") or []
        path = downloads[0]["filepath"] if downloads else ydl.prepare_filename(info)
        return {"path": path, "info": _slim(ydl.sanitize_info(info))}


_ytm = None


def ytm_search(query: str, limit: int):
    """ytmusicapi axtarışı (obyekt hər prosesdə bir dəfə yaradılır)."""
    global _ytm
    if _ytm is None:
        from ytmusicapi import YTMusic
        _ytm = YTMusic()
    return _ytm.search(query, filter="songs", limit=limit)


TASKS = {"ping": ping, "ytdl_extract": ytdl_extract, "ytdl_download": ytdl_download, "ytm_search": ytm_search}


# ───────────────────────── proses döngüsü ─────────────────────────
def _read(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf


def main():
    nice = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else 0
    if nice > 0:
        try:
            os.nice(nice)                    # botun əsas prosesi həmişə üstün olsun
        except OSError:
            pass
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # Ctrl+C-ni əsas bot idarə edir
    proto_in = sys.stdin.buffer
    proto_out = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # yt-dlp print()-ləri protokolu pozmasın
    sys.stdout = sys.stderr

    def send(obj):
        data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        proto_out.write(struct.pack(">I", len(data)) + data)

    send((0, "ok", "ready"))
    while True:
        try:
            size = struct.unpack(">I", _read(proto_in, 4))[0]
            task_id, name, args = pickle.loads(_read(proto_in, size))
        except EOFError:
            return
        try:
            result = TASKS[name](*args)
            try:
                send((task_id, "ok", result))
            except Exception as e:           # nəticə pickle olunmadı
                send((task_id, "err", f"nəticə ötürülmədi: {type(e).__name__}: {e}"))
        except Exception as e:
            cancelled = type(e).__name__ == "DownloadCancelled"
            send((task_id, "cancel" if cancelled else "err", str(e) or type(e).__name__))


if __name__ == "__main__":
    main()
