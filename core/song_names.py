"""
🏷 Mahnı adlarının təmizlənməsi və müqayisəsi — core/song_names.py

  clean_title("Eminem - Lose Yourself (Official Video) [HD] 12.05.2024")  → "Eminem - Lose Yourself"
  split_title(...)          → ("Eminem", "Lose Yourself")   (artist yoxdursa "YouTube")
  clean_track / clean_artist — ayrıca sahələr üçün (song.link / Spotify adları da: "- Remastered 2011")
  meta_matches(artist, track, yt_title) — song.link adı YouTube adına həqiqətən uyğundurmu
  tokens / query_score / same_song — depo axtarışı və dublikatlar üçün
"""
import re
import unicodedata
from html import unescape

# Mötərizə içində bunlardan biri varsa, mötərizə tamamilə atılır
_BRACKET_NOISE = re.compile(
    r"\b(official|offical|oficial|video|audio|lyrics?|visuali[sz]er|mv|m/v|hd|hq|4k|8k|uhd|"
    r"klip|clip|rəsmi|resmi|premiere|premyera|премьера|клип|клипа|exclusive|eksklüziv|ekskluziv|yeni|new|"
    r"trend|tiktok|hit|full|mp3|m4a|\d{2,3}\s*kbps|kbps|bass\s*boost(?:ed)?|prod|produced|"
    r"remaster(?:ed)?|explicit|dir|directed|shot\s+by|subtitles?|altyaz[ıi]|"
    r"tekst|текст|sözləri|sozleri)\b"
    r"|^\s*(?:19|20)\d\d\s*$"                                 # (2023)
    r"|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"                       # (12.05.2024)
    r"|^\s*\d+\s*$|https?://|www\.|@", re.I)
_BRACKETS = re.compile(r"\s*[\(\[【{«]([^\)\]】}»]*)[\)\]】}»]")
_URL = re.compile(r"(?:https?://|www\.)\S+|\bt\.me/\S+", re.I)
_MENTION = re.compile(r"(?<![^\W_])[@#][\w.]+")
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2500-\u25FF\u2660-\u266F"
    "\uFE0F\u200B-\u200F\u2060\u00A9\u00AE\u2122]+")
_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}[./](?:19|20)?\d\d\b|\b(?:19|20)\d\d[./-]\d{1,2}[./-]\d{1,2}\b")
_YEAR_PHRASE = re.compile(
    r"\b(?:en\s+yeni|yeni\s+mahn[ıi]|new\s+song|yeni|new|hit|trend|tiktok)\s*(?:19|20)\d\d\b"
    r"(?:\s+(?:yeni|new|klip|clip))?"
    r"|\b(?:19|20)\d\d\s*(?:yeni|new|hit|trend|klip|clip|mahn[ıi]s[ıi]?)\b(?:\s+(?:klip|clip))?", re.I)
_TRAILING = re.compile(
    r"(?:\s*[-|/:]\s*|\s+)(?:official|offical|rəsmi|resmi)?\s*(?:music\s+|lyrics?\s+|lyric\s+)?"
    r"(?:video|audio|klip|clip|visuali[sz]er|lyrics?)(?:\s+clip)?\s*$"
    r"|(?:\s*[-|/:]\s*|\s+)(?:official|rəsmi|resmi|premiere|premyera|премьера|hd|hq|4k|mp3|"
    r"\d{2,3}\s*kbps|yeni|new|trend|tiktok)\s*$", re.I)
_REMASTER = re.compile(
    r"\s+-\s+(?:(?:19|20)\d\d\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?(?:\s+(?:19|20)\d\d)?(?:\s+version)?\s*$"
    r"|\s+-\s+(?:single|album|mono|stereo)\s+version\s*$", re.I)
_TRAILING_YEAR = re.compile(r"(?:\s*[-|/,]\s*|\s+)(?:19|20)\d\d\s*$")
_SEP = re.compile(r"\s+(?:\||//|•|·|‖)\s+|\s*\|\s*")

_CHANNEL_SUFFIX = re.compile(
    r"(?:\s*-\s*topic|vevo|\s+official(?:\s+(?:channel|music|tv|youtube|page))?|\s+music|\s+tv|"
    r"\s+rəsmi(?:\s+kanal[ıi]?)?|\s+resmi)\s*$", re.I)

_STOP = {"feat", "ft", "featuring", "prod", "with", "x", "vs", "the", "and", "a", "de", "ve", "və", "и"}


def _norm_dashes(s: str) -> str:
    s = re.sub(r"\s*[–—―‒−]\s*", " - ", s)
    return re.sub(r"(?<=\S)\s+-\s*(?=\S)|(?<=\S)\s*-\s+(?=\S)", " - ", s)


def _drop_brackets(s: str) -> str:
    return _BRACKETS.sub(lambda m: "" if (not m.group(1).strip() or _BRACKET_NOISE.search(m.group(1)))
                         else m.group(0), s)


def _base_clean(s: str) -> str:
    s = unescape(str(s or "")).replace("\u00a0", " ")
    s = _drop_brackets(s)
    s = _URL.sub(" ", s)
    s = _MENTION.sub(" ", s)
    s = _EMOJI.sub(" ", s)
    s = _DATE.sub(" ", s)
    s = _YEAR_PHRASE.sub(" ", s)
    s = _drop_brackets(s)                      # tarix silinəndən sonra boş qalan mötərizələr
    if _SEP.search(s):
        parts = [p for p in _SEP.split(s) if p.strip()]
        s = next((p for p in parts if " - " in _norm_dashes(p)), parts[0] if parts else s)
    s = _norm_dashes(s)
    for _ in range(3):
        s = _TRAILING.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" -–|.,:;*•·'\"`~")


def clean_track(track: str) -> str:
    """Mahnı adı (artistsiz): tarix, il, (Official Video), "- Remastered 2011" və s. atılır."""
    t = _base_clean(track)
    t = _REMASTER.sub("", t)
    stripped = _TRAILING_YEAR.sub("", t).strip(" -")
    if stripped and re.search(r"[^\W\d_]", stripped):     # "1999" kimi adlar qalsın
        t = stripped
    return t.strip(" -") or (str(track or "").strip())


def clean_artist(artist: str) -> str:
    a = _base_clean(artist)
    for _ in range(2):
        a = _CHANNEL_SUFFIX.sub("", a).strip()
    return a.strip(" -,") or (str(artist or "").strip())


clean_channel = clean_artist


def clean_title(title: str) -> str:
    """Tam YouTube adı ("Artist - Ad ...") → təmiz "Artist - Ad"."""
    t = _base_clean(title)
    if not t:
        return str(title or "").strip()
    parts = re.split(r"\s+-\s+", t, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return f"{parts[0].strip()} - {clean_track(parts[1])}"
    return clean_track(t)


def split_title(title: str):
    """YouTube adını təmizləyib (artist, ad) kimi ayırır; artist yoxdursa "YouTube"."""
    t = clean_title(title)
    parts = re.split(r"\s+-\s+", t, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return "YouTube", t


# ───────────── müqayisə ─────────────
def fold(s: str) -> str:
    s = (s or "").casefold().replace("ə", "e").replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", fold(s)) if w not in _STOP and (len(w) >= 2 or w.isdigit())}


def track_tokens(track: str) -> frozenset:
    return frozenset(tokens(clean_track(track)))


def artist_tokens(artist: str) -> frozenset:
    return frozenset(tokens(clean_artist(artist)))


def _tok_hit(q: str, pool: set) -> bool:
    return q in pool or (len(q) >= 3 and any(p.startswith(q) for p in pool))


def query_score(query: str, artist: str, track: str) -> float:
    """Axtarış sorğusu depo mahnısına nə qədər uyğundur (0..1). Sözün əvvəli də sayılır: "lose yours"."""
    q = tokens(query)
    if not q:
        return 0.0
    ta, tt = tokens(artist), tokens(track)
    pool = ta | tt
    hit = sum(1 for w in q if _tok_hit(w, pool))
    score = hit / len(q)
    if tt and not any(_tok_hit(w, tt) for w in q):
        score *= 0.5                                  # yalnız artist uyğun gəlir — ifaçının başqa mahnısı
    extra = len(tt - q) / max(1, len(tt))             # sorğuda olmayan çox söz → bir az aşağı
    return score - 0.15 * extra


def meta_matches(artist: str, track: str, yt_title: str) -> bool:
    """song.link / Spotify adı YouTube adı ilə həqiqətən eyni mahnıdırmı."""
    yt = tokens(clean_title(yt_title))
    if not yt:
        return True                                    # müqayisə etmək olmur
    tt, ta = tokens(clean_track(track)), tokens(clean_artist(artist))
    if not tt:
        return False
    t_ratio = len(tt & yt) / len(tt)
    a_hit = bool(ta & yt)
    return t_ratio >= 0.75 or (t_ratio >= 0.5 and a_hit)


def same_song(a_artist, a_track, b_artist, b_track) -> bool:
    ta, tb = track_tokens(a_track), track_tokens(b_track)
    if not ta or ta != tb:
        return False
    aa, ab = artist_tokens(a_artist), artist_tokens(b_artist)
    return bool(aa and ab and (aa & ab))
