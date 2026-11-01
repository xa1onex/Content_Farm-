"""Steady phrase captions: one ASS event per phrase, no word-by-word relayout."""
import math
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT_FILE = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_SIZE = 60
MAX_WIDTH = 880
MAX_PAUSE = .65


def format_ass_time(seconds):
    cs = max(0, round(float(seconds) * 100))
    return f'{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}'


def escape_ass(text):
    return str(text).replace('\\', '／').replace('{', '(').replace('}', ')').replace('\n', ' ')


@lru_cache(maxsize=64)
def _font(size):
    return ImageFont.truetype(FONT_FILE, size)


def _width(text, size=FONT_SIZE):
    return _font(size).getlength(text)


def _label(word):
    return escape_ass(word['word']).upper()


def _fit_label(label, size):
    # Exceptionally long individual words get explicit breaks. ASS never decides
    # wrapping during playback, and ordinary phrases stay on one line.
    lines, line = [], ''
    for char in label:
        if line and _width(line + char, size) > MAX_WIDTH:
            lines.append(line)
            line = ''
        line += char
    lines.append(line)
    return r'\N'.join(lines)


def generate_tiktok_ass(words, ass_path, duration=60, subtitle_y=1400):
    clean = []
    for word in words:
        text = ' '.join(str(word.get('word') or '').split())
        try:
            start, end = float(word['start']), float(word['end'])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or not all(map(math.isfinite, (start, end))):
            continue
        start, end = max(0, start), min(duration, end)
        if end > start:
            clean.append({'word': text, 'start': start, 'end': end})
    clean.sort(key=lambda word: word['start'])

    phrases, phrase = [], []
    for word in clean:
        candidate = ' '.join(_label(w) for w in [*phrase, word])
        if phrase and (len(phrase) >= 3 or
                       word['start'] - phrase[-1]['end'] > MAX_PAUSE or
                       _width(candidate) > MAX_WIDTH):
            phrases.append(phrase)
            phrase = []
        phrase.append(word)
        if (word['word'].endswith(('.', '!', '?', '…')) and
                word['end'] - phrase[0]['start'] >= .45):
            phrases.append(phrase)
            phrase = []
    if phrase:
        phrases.append(phrase)
    merged = []
    for phrase in phrases:
        if merged and round(phrase[0]['start'] * 100) == round(merged[-1][0]['start'] * 100):
            merged[-1].extend(phrase)
        else:
            merged.append(phrase)
    phrases = merged

    content = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,60,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,100,100,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    for index, phrase in enumerate(phrases):
        start = round(phrase[0]['start'] * 100) / 100
        spoken_end = max(word['end'] for word in phrase)
        finish = min(duration, max(start + .55, spoken_end + .20))
        if index + 1 < len(phrases):
            following = phrases[index + 1][0]['start']
            # Join ordinary speech continuously; clear the screen during a real
            # pause. Adjacent captions share an exact centisecond boundary.
            finish = following if following - spoken_end <= MAX_PAUSE else min(finish, following)
        finish = min(round(finish * 100) / 100, math.floor(duration * 100) / 100)
        if finish <= start:
            continue

        label = ' '.join(_label(word) for word in phrase)
        size = max(24, min(FONT_SIZE, int(FONT_SIZE * MAX_WIDTH / max(1, _width(label)))))
        while size > 24 and _width(label, size) > MAX_WIDTH:
            size -= 1
        parts = []
        # Built-in karaoke changes only glyph colour. Text and its bounding box
        # stay in a single event for the entire phrase, including short gaps.
        elapsed_cs = 0
        line = ''
        for position, word in enumerate(phrase):
            next_start = phrase[position + 1]['start'] if position + 1 < len(phrase) else finish
            end_cs = max(elapsed_cs, round((next_start - start) * 100))
            display = _fit_label(_label(word), size)
            first_line = display.split(r'\N', 1)[0]
            separator = '' if not parts else ' '
            if line and _width(line + ' ' + first_line, size) > MAX_WIDTH:
                separator = r'\N'
                line = ''
            parts.append(separator + r'{\k' + str(end_cs - elapsed_cs) + '}' + display)
            line = display.rsplit(r'\N', 1)[-1] if r'\N' in display else (line + ' ' + display).strip()
            elapsed_cs = end_cs
        anchor = round(subtitle_y + 34)
        content += (f'Dialogue: 0,{format_ass_time(start)},{format_ass_time(finish)},'
                    f'Default,,0,0,0,,{{\\an2\\pos(540,{anchor})\\fs{size}}}' + ''.join(parts) + '\n')
    Path(ass_path).write_text(content, encoding='utf-8')
