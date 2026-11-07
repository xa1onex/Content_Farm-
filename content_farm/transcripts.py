"""Exact spoken excerpt from the same word timing boundaries used by subtitles."""
import math,re

def clip_transcript(transcript,start,end):
    start,end=float(start),float(end)
    if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<=start:
        raise ValueError('Invalid clip transcript interval')
    paragraphs=[]
    for segment in transcript.get('segments',[]):
        words=segment.get('words') or []
        if words:
            selected=[]
            for word in words:
                try:begin,finish=float(word['start']),float(word['end'])
                except (KeyError,TypeError,ValueError):continue
                if not all(map(math.isfinite,(begin,finish))) or finish<=begin:continue
                if finish>start and begin<end:
                    label=' '.join(str(word.get('word') or '').split())
                    if label:selected.append(label)
            text=' '.join(selected)
        else:
            # Without word times, include only segments wholly inside the cut.
            # Never substitute a title/summary or leak speech outside the clip.
            try:begin,finish=float(segment['start']),float(segment['end'])
            except (KeyError,TypeError,ValueError):continue
            text=' '.join(str(segment.get('text') or '').split()) if start<=begin<finish<=end else ''
        text=re.sub(r'\s+([,.;:!?…])',r'\1',text).strip()
        if text:paragraphs.append(text)
    return {'text':'\n\n'.join(paragraphs),'language':transcript.get('language'),
            'source':'source_transcript','start_seconds':start,'end_seconds':end,'version':1}
