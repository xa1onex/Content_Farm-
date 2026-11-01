"""Strict parsing: never pass arbitrary URLs or command text to a downloader."""
import re
from urllib.parse import parse_qs, urlparse

URL_PATTERN = re.compile(r'https?://[^\s<>]+')
VIDEO_ID = re.compile(r'^[\w-]{11}$')
DRIVE_ID = re.compile(r'^[\w-]{10,200}$')

def parse_source(text):
    urls = URL_PATTERN.findall(text or '')
    if len(urls) != 1:
        raise ValueError('Отправь одну ссылку на видео YouTube или файл Google Диска.')
    parsed = urlparse(urls[0].rstrip('.,)'))
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('Некорректная ссылка.')
    host = (parsed.hostname or '').lower()
    query = parse_qs(parsed.query)
    if host == 'youtu.be':
        video = parsed.path.strip('/')
    elif host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
        parts = parsed.path.strip('/').split('/')
        video = query.get('v', [''])[0] if parsed.path == '/watch' else (
            parts[1] if len(parts) == 2 and parts[0] in {'shorts', 'live', 'embed'} else '')
    elif host in {'drive.google.com', 'docs.google.com'}:
        match = re.fullmatch(r'/file/d/([\w-]+)(?:/[^/]*)?/?', parsed.path)
        file_id = match.group(1) if match else query.get('id', [''])[0]
        if not DRIVE_ID.fullmatch(file_id):
            raise ValueError('Нужна ссылка на видеофайл Диска, а не на папку.')
        return 'drive', file_id
    else:
        raise ValueError('Поддерживаются ссылки YouTube и Google Диска.')
    if not VIDEO_ID.fullmatch(video):
        raise ValueError('В ссылке не найден корректный ID видео YouTube.')
    return 'youtube', 'https://www.youtube.com/watch?v=' + video
