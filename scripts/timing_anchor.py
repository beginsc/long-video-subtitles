"""Locate a unique semantic boundary in cleaned text and original aligned words."""
import re,math

def compact(text):return ''.join(c for c in str(text).casefold() if c.isalnum())

def locate_boundary(source,words,source_anchor,word_anchor=None,after_word=0,after_char=0,offset_ms=0):
    """Return a candidate boundary for review; never select the first of many hits.

    Explicit word_anchor is needed for an already documented ASR spelling repair.
    after_* denote the prior boundary, so the new boundary must be later.
    """
    if not isinstance(source,str) or not source_anchor.strip():raise ValueError('Text and nonempty source anchor required')
    if not isinstance(after_word,int) or not isinstance(after_char,int) or after_word<0 or after_char<0:raise ValueError('Previous boundary indices must be nonnegative integers')
    needle=compact(word_anchor or source_anchor)
    if not needle:raise ValueError('Anchor must contain letters or digits')
    tokens=[compact(w.get('word','')) for w in words];hits=[]
    for i in range(after_word+1,len(words)):
        if not tokens[i]:continue
        value=''
        for j in range(i,len(words)):
            value+=tokens[j]
            if value==needle:hits.append(i);break
            if len(value)>=len(needle):break
    if len(hits)!=1:raise ValueError(f'Word anchor has {len(hits)} matches; require a unique longer anchor or explicit prior boundary')
    # Apostrophes are kept inside words; hyphen/space variation is normalized.
    text_tokens=list(re.finditer(r"\w+(?:['’]\w+)*",source));text_hits=[];wanted=compact(source_anchor)
    if not wanted:raise ValueError('Source anchor must contain letters or digits')
    for i,t in enumerate(text_tokens):
        if t.start()<=after_char:continue
        value=''
        for t2 in text_tokens[i:]:
            value+=compact(t2.group())
            if value==wanted:text_hits.append(t.start());break
            if len(value)>=len(wanted):break
    if len(text_hits)!=1:raise ValueError(f'Source anchor has {len(text_hits)} matches; require a unique longer anchor or explicit prior boundary')
    word_index=hits[0];time=words[word_index].get('start')
    if isinstance(time,bool) or not isinstance(time,(int,float)) or not math.isfinite(time) or time<0:raise ValueError('Boundary word lacks a valid aligned start time')
    if isinstance(offset_ms,bool) or not isinstance(offset_ms,(int,float)) or not math.isfinite(offset_ms) or offset_ms<0:raise ValueError('Invalid absolute time offset')
    char=text_hits[0]
    return dict(word_index=word_index,source_char_index=char,start_ms=round(time*1000+offset_ms),source_anchor=source_anchor,word_anchor=word_anchor or source_anchor,explicit_spelling_mapping=word_anchor is not None,source_before=source[:char],source_after=source[char:],review_status='candidate_requires_semantic_review')
