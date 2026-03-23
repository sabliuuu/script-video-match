import streamlit as st
import whisper
import pdfplumber
import subprocess
import tempfile
import pathlib
import re
import html as html_lib
from difflib import SequenceMatcher
from datetime import date
import os
import uuid
import imageio_ffmpeg

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Script x Video Match",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
.main { padding-top: 1rem; }
.stButton > button {
    background: #0f3460; color: white; border: none;
    padding: 0.6rem 2rem; font-size: 16px; font-weight: 600;
    border-radius: 8px; width: 100%; cursor: pointer;
}
.stButton > button:hover { background: #1a4a80; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def read_script(path):
    ext = pathlib.Path(path).suffix.lower()
    if ext == '.pdf':
        text = ''
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or '') + '\n'
        return text
    elif ext in ('.docx', '.doc'):
        from docx import Document
        doc = Document(path)
        return '\n'.join(p.text for p in doc.paragraphs)
    return open(path, encoding='utf-8', errors='ignore').read()

def extract_key_phrases(text):
    phrases = []
    phrases += re.findall(r'"([^"]{10,150})"', text)
    phrases += re.findall(r'[\u201c\u2018]([^\u201d\u2019]{10,150})[\u201d\u2019]', text)
    for m in re.finditer(
        r'(?:VO|V\.O|Text|Script|Caption|Voiceover|Narration|Dialogue|Line)[^:]*:\s*(.+)',
        text, re.IGNORECASE
    ):
        line = m.group(1).strip().strip('"').strip('\u201c\u201d\u2018\u2019')
        if len(line) > 8:
            phrases.append(line)
    if len(phrases) < 3:
        for sentence in re.split(r'[\n.!?]', text):
            sentence = sentence.strip()
            if 8 <= len(sentence.split()) <= 40:
                phrases.append(sentence)
    seen, out = set(), []
    for p in phrases:
        k = re.sub(r'\s+', ' ', p.lower().strip())
        if k and k not in seen:
            seen.add(k)
            out.append(p.strip())
    return out[:40]

def norm(t):
    return re.sub(r'[^\w\s]', '', t.lower()).strip()

def match_phrase(phrase, transcript):
    pn = norm(phrase)
    tn = norm(transcript)
    words = [w for w in pn.split() if len(w) > 3]
    if not words:
        return 'missing', ''
    wfound = sum(1 for w in words if w in tn)
    wratio = wfound / len(words)
    best_score, best_snip = 0.0, ''
    wt = transcript.split()
    win = max(1, len(phrase.split()))
    for i in range(max(1, len(wt) - win + 1)):
        chunk = ' '.join(wt[i:i + win + 5])
        sc = SequenceMatcher(None, norm(phrase), norm(chunk)).ratio()
        if sc > best_score:
            best_score, best_snip = sc, chunk
    if best_score >= 0.75 or wratio >= 0.85:
        return 'found', best_snip
    elif best_score >= 0.50 or wratio >= 0.55:
        return 'partial', best_snip
    return 'missing', best_snip

def find_timestamp(snippet, segments):
    """Return 'M:SS' of the segment most likely containing this snippet."""
    if not segments or not snippet:
        return ''
    sn = norm(snippet[:80])
    best_score, best_t = 0.0, None
    for seg in segments:
        sc = SequenceMatcher(None, sn, norm(seg.get('text', ''))).ratio()
        if sc > best_score:
            best_score, best_t = sc, seg.get('start', 0)
    if best_score > 0.25 and best_t is not None:
        m, s = int(best_t) // 60, int(best_t) % 60
        return f"{m}:{s:02d}"
    return ''

def translate_text(text, source, target):
    """Translate text using deep-translator (Google backend, no API key)."""
    try:
        from deep_translator import GoogleTranslator
        MAX = 4500
        if not text or not text.strip():
            return ''
        if len(text) <= MAX:
            return GoogleTranslator(source=source, target=target).translate(text) or ''
        # Chunk long transcripts
        chunks = [text[i:i + MAX] for i in range(0, len(text), MAX)]
        return ' '.join(
            GoogleTranslator(source=source, target=target).translate(c) or ''
            for c in chunks
        )
    except Exception as e:
        return f'(Translation unavailable: {e})'

def transcribe(video_path, model_size):
    import wave
    import numpy as np

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    audio_path = os.path.join(tempfile.gettempdir(), f'audio_{uuid.uuid4().hex}.wav')
    r = subprocess.run(
        [ffmpeg_exe, '-i', video_path, '-vn', '-acodec', 'pcm_s16le',
         '-ar', '16000', '-ac', '1', audio_path, '-y'],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return None, 'unknown', [], None, None, f"Audio extraction failed: {r.stderr[-300:]}"

    try:
        with wave.open(audio_path, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            audio_np = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as e:
        return None, 'unknown', [], None, None, f"WAV read failed: {e}"
    finally:
        try:
            os.remove(audio_path)
        except Exception:
            pass

    wmodel = whisper.load_model(model_size)
    result = wmodel.transcribe(audio_np, task='transcribe')
    orig_text = result['text'].strip()
    orig_lang = result.get('language', 'unknown')
    segments  = result.get('segments', [])

    # English translation
    if orig_lang == 'en':
        en_text = orig_text
    else:
        en_text = translate_text(orig_text, source=orig_lang, target='en')

    # Chinese translation (translate from English if available, else original)
    if orig_lang in ('zh', 'zh-cn', 'zh-tw', 'zh-hans', 'zh-hant'):
        zh_text = orig_text
    else:
        src_text = en_text if (en_text and not en_text.startswith('(Translation')) else orig_text
        src_lang = 'en' if src_text == en_text else orig_lang
        zh_text = translate_text(src_text, source=src_lang, target='zh-CN')

    return orig_text, orig_lang, segments, en_text, zh_text, None

# ══════════════════════════════════════════════════════════════════════
# REPORT BUILDER (key phrase table + score, no transcript section)
# ══════════════════════════════════════════════════════════════════════

def build_html_report(video_name, script_name, brand, influencer,
                      detected_lang, results,
                      found_n, partial_n, missing_n, score, model_size):
    today = date.today().strftime('%Y-%m-%d')
    sc_col = '#1a8a4a' if score >= 80 else ('#f0b429' if score >= 60 else '#c0392b')

    def pill(status):
        c = {
            'found':   ('#e8f8ef', '#1a8a4a', 'Found'),
            'partial': ('#fff4e0', '#b07d00', 'Partial'),
            'missing': ('#fdecea', '#c0392b', 'Missing'),
        }
        bg, fg, lbl = c.get(status, ('#f0f0f0', '#666', status))
        return (f'<span style="background:{bg};color:{fg};padding:3px 10px;'
                f'border-radius:12px;font-size:12px;font-weight:600;'
                f'border:1px solid {fg}">{lbl}</span>')

    rows = ''
    for r in results:
        p  = html_lib.escape(r['phrase'])
        s  = html_lib.escape(r['snippet']) if r['snippet'] else '<em style="color:#aaa">—</em>'
        ts = r.get('timestamp', '')
        ts_html = (f'<span style="background:#f0f4ff;color:#0f3460;padding:2px 7px;'
                   f'border-radius:10px;font-size:11px;font-weight:600">{ts}</span>'
                   if ts else '<em style="color:#ccc">—</em>')
        rows += (
            '<tr>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #eef0f4;font-size:13px">{p}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #eef0f4;text-align:center">{pill(r["status"])}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #eef0f4;text-align:center">{ts_html}</td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #eef0f4;font-size:12px;color:#555;font-style:italic">{s}</td>'
            '</tr>'
        )

    missing_items = [r for r in results if r['status'] == 'missing']
    if missing_items:
        act = '<ul style="margin:0;padding-left:20px">'
        for r in missing_items:
            act += f'<li style="margin-bottom:6px;font-size:13px">{html_lib.escape(r["phrase"])}</li>'
        act += '</ul>'
    else:
        act = '<p style="color:#1a8a4a;font-size:13px;margin:0">All key phrases found!</p>'

    css = (
        'body{font-family:-apple-system,Segoe UI,Arial,sans-serif;background:#f4f6f9;margin:0;padding:20px}'
        '.card{background:white;border-radius:10px;padding:24px;margin-bottom:18px;box-shadow:0 1px 4px rgba(0,0,0,.08)}'
        'h2{margin:0 0 6px;font-size:15px;color:#0f3460}'
        'table{width:100%;border-collapse:collapse}'
        'th{background:#1a1a2e;color:white;padding:10px 12px;text-align:left;font-size:13px}'
        'tr:nth-child(even) td{background:#f9fafc}'
    )

    parts = [
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><style>{css}</style></head><body>',
        # Header
        '<div style="background:linear-gradient(135deg,#1a1a2e,#0f3460);color:white;padding:24px 28px;border-radius:10px;margin-bottom:18px">',
        '<div style="font-size:20px;font-weight:700">Script x Video Match Report</div>',
        '<div style="font-size:12px;color:#a0b0cc;margin-top:3px">Influencer Script Compliance Report</div>',
        f'<div style="margin-top:12px;font-size:12px;color:#ccd8ee">',
        f'Video: <b>{html_lib.escape(video_name)}</b> &nbsp;|&nbsp; ',
        f'Script: <b>{html_lib.escape(script_name)}</b> &nbsp;|&nbsp; ',
        f'Influencer: <b>{html_lib.escape(influencer)}</b> &nbsp;|&nbsp; ',
        f'Language: <b>{detected_lang}</b> &nbsp;|&nbsp; Date: <b>{today}</b>',
        '</div></div>',
        # Score card
        '<div class="card"><div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">',
        f'<div style="text-align:center"><div style="font-size:48px;font-weight:800;color:{sc_col};line-height:1">{score}%</div>',
        '<div style="font-size:12px;color:#888">Match Score</div></div>',
        '<div style="display:flex;gap:12px;flex-wrap:wrap">',
        f'<div style="background:#e8f8ef;border-top:3px solid #1a8a4a;padding:12px 18px;border-radius:8px;text-align:center"><div style="font-size:26px;font-weight:800;color:#1a8a4a">{found_n}</div><div style="font-size:11px;color:#888">Found</div></div>',
        f'<div style="background:#fff4e0;border-top:3px solid #f0b429;padding:12px 18px;border-radius:8px;text-align:center"><div style="font-size:26px;font-weight:800;color:#b07d00">{partial_n}</div><div style="font-size:11px;color:#888">Partial</div></div>',
        f'<div style="background:#fdecea;border-top:3px solid #c0392b;padding:12px 18px;border-radius:8px;text-align:center"><div style="font-size:26px;font-weight:800;color:#c0392b">{missing_n}</div><div style="font-size:11px;color:#888">Missing</div></div>',
        '</div></div></div>',
        # Key phrase table
        '<div class="card"><h2>Key Phrase Matching</h2>',
        f'<p style="font-size:12px;color:#888;margin:4px 0 14px">Whisper {model_size} &nbsp;·&nbsp; keyword + similarity matching</p>',
        '<table><thead><tr>',
        '<th style="width:38%">Script Key Phrase</th>',
        '<th style="width:12%">Status</th>',
        '<th style="width:10%">Timestamp</th>',
        '<th>Best Match in Transcript</th>',
        f'</tr></thead><tbody>{rows}</tbody></table></div>',
        # Missing follow-up
        '<div class="card" style="border-left:4px solid #c0392b">',
        '<h2 style="color:#c0392b">Missing — Follow Up Required</h2>',
        f'<p style="font-size:12px;color:#888;margin:4px 0 12px">Not detected in audio. Verify with influencer.</p>{act}</div>',
        f'<div style="text-align:center;font-size:11px;color:#aaa;margin-top:8px">{html_lib.escape(brand)} x {html_lib.escape(influencer)} | {today}</div>',
        '</body></html>',
    ]
    return ''.join(parts)

# ══════════════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════════════

if 'reports' not in st.session_state:
    st.session_state.reports = {}
if 'selected' not in st.session_state:
    st.session_state.selected = None

# ══════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════

st.markdown("## Script x Video Match Analyzer")
st.markdown("Upload videos and scripts — the system transcribes the audio and checks whether key messages were delivered.")

st.markdown("""
<div style="background:#fff8e1;border-left:4px solid #f0b429;padding:12px 18px;
border-radius:6px;margin:12px 0;font-size:13px;color:#7a5c00">
<b>Note — Audio Only:</b> This tool detects spoken words via audio transcription (Whisper).
It <b>cannot read on-screen text, captions, or subtitles</b>.
If an influencer's video uses text overlays without a voiceover, those messages will not be detected
and will appear as <b>Missing</b> in the report. Please review those cases manually.
</div>
""", unsafe_allow_html=True)

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════
# STEP 1: UPLOAD — two big columns
# ══════════════════════════════════════════════════════════════════════

st.markdown("### Step 1 — Upload Files")
col_v, col_s = st.columns(2)

with col_v:
    st.markdown("#### Videos")
    st.caption("Supports MP4 / MOV / AVI / MKV, up to 500MB per file. Select multiple at once.")
    video_files = st.file_uploader(
        "Upload videos", type=['mp4', 'mov', 'avi', 'mkv', 'wmv', 'm4v'],
        accept_multiple_files=True, key="videos", label_visibility="collapsed",
    )
    if video_files:
        for i, vf in enumerate(video_files):
            st.markdown(
                f'<div style="background:#e8eeff;padding:8px 12px;border-radius:6px;'
                f'margin-bottom:4px;font-size:13px;border-left:3px solid #0f3460">'
                f'<b>{i+1}.</b> {vf.name}</div>', unsafe_allow_html=True)

with col_s:
    st.markdown("#### Scripts (PDF / DOCX / TXT)")
    st.caption("Paired with videos by upload order — 1st script goes with 1st video, and so on.")
    script_files = st.file_uploader(
        "Upload scripts", type=['pdf', 'docx', 'doc', 'txt'],
        accept_multiple_files=True, key="scripts", label_visibility="collapsed",
    )
    if script_files:
        for i, sf in enumerate(script_files):
            st.markdown(
                f'<div style="background:#e8f8ef;padding:8px 12px;border-radius:6px;'
                f'margin-bottom:4px;font-size:13px;border-left:3px solid #1a8a4a">'
                f'<b>{i+1}.</b> {sf.name}</div>', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════
# STEP 2: PAIRING + INFLUENCER NAMES
# ══════════════════════════════════════════════════════════════════════

if video_files and script_files:
    st.markdown("---")
    st.markdown("### Step 2 — Confirm Pairings & Add Influencer Names")
    st.caption("Auto-paired by upload order. Select \"— No script\" for videos you only want transcribed without matching.")

    NO_SCRIPT = "— No script (transcribe only) —"
    script_names = [s.name for s in script_files]
    script_options = [NO_SCRIPT] + script_names
    pairs = []

    for i, vf in enumerate(video_files):
        # Videos within the script count get paired in order; extras default to No script
        default_idx = (i + 1) if i < len(script_names) else 0
        c1, c2, c3, c4 = st.columns([3, 0.4, 3, 2])
        with c1:
            st.markdown(
                f'<div style="background:#e8eeff;padding:9px 12px;border-radius:6px;'
                f'font-size:13px;font-weight:500">📹 {vf.name}</div>', unsafe_allow_html=True)
        with c2:
            st.markdown('<div style="text-align:center;padding:8px 0;font-size:18px;color:#888">→</div>',
                        unsafe_allow_html=True)
        with c3:
            selected_script = st.selectbox(
                "script", options=script_options, index=default_idx,
                key=f"pair_{i}", label_visibility="collapsed")
        with c4:
            influencer_name = st.text_input(
                "inf", placeholder="Influencer name",
                key=f"inf_{i}", label_visibility="collapsed")
        pairs.append((vf, selected_script, influencer_name or vf.name))

    # ── STEP 3: SETTINGS ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Step 3 — Settings")
    c1, c2 = st.columns(2)
    with c1:
        brand = st.text_input("Brand Name", value="Brand", placeholder="e.g. KOMFYMED")
    with c2:
        speed_label = st.selectbox(
            "Transcription Quality",
            ["Fast (English only)", "Standard (SEA languages)", "Accurate (best quality)"],
            index=1,
        )
    model_map = {
        "Fast (English only)": "base",
        "Standard (SEA languages)": "small",
        "Accurate (best quality)": "medium",
    }
    model_size = model_map[speed_label]

    st.markdown("")
    start_btn = st.button("Start Analysis", type="primary")

    # ── STEP 4: ANALYSIS ──────────────────────────────────────────────
    if start_btn:
        st.markdown("---")
        st.markdown("### Analyzing...")
        tmp_dir = tempfile.mkdtemp()
        script_map = {}
        for sf in script_files:
            sp = os.path.join(tmp_dir, sf.name)
            with open(sp, 'wb') as f:
                f.write(sf.getvalue())
            script_map[sf.name] = sp

        overall_progress = st.progress(0)
        st.session_state.reports = {}
        st.session_state.selected = None

        for i, (vf, script_name, influencer) in enumerate(pairs):
            overall_progress.progress(i / len(pairs), text=f"Processing {i+1}/{len(pairs)}: {vf.name}")
            with st.status(f"Analyzing: {vf.name}", expanded=True) as status:
                st.write("Saving video...")
                vp = os.path.join(tmp_dir, vf.name)
                with open(vp, 'wb') as f:
                    f.write(vf.getvalue())

                st.write(f"Transcribing audio (Whisper {model_size})... 1-3 min")
                orig_text, detected_lang, segments, en_text, zh_text, err = transcribe(vp, model_size)

                if err:
                    st.error(f"Error: {err}")
                    status.update(label=f"Error: {vf.name}", state="error")
                    continue
                st.write(f"Done. Language: {detected_lang}")

                key = f"{i}_{pathlib.Path(vf.name).stem}"

                if script_name == NO_SCRIPT:
                    # Transcribe-only — no script matching
                    st.write("No script selected — skipping match analysis.")
                    st.session_state.reports[key] = {
                        'html': None,
                        'orig_text': orig_text,
                        'en_text': en_text or '',
                        'zh_text': zh_text or '',
                        'segments': segments,
                        'detected_lang': detected_lang,
                        'score': None,
                        'influencer': influencer,
                        'video': vf.name,
                    }
                    status.update(label=f"Done: {vf.name} — transcript only ({detected_lang})", state="complete")
                else:
                    # Full match analysis
                    st.write(f"Reading script: {script_name}...")
                    script_text = read_script(script_map[script_name])
                    key_phrases = extract_key_phrases(script_text)
                    st.write(f"Found {len(key_phrases)} key phrases. Matching...")

                    results = []
                    found_n = partial_n = missing_n = 0
                    for phrase in key_phrases:
                        s, snip = match_phrase(phrase, orig_text)
                        ts = find_timestamp(snip, segments) if s != 'missing' else ''
                        results.append({'phrase': phrase, 'status': s, 'snippet': snip, 'timestamp': ts})
                        if s == 'found':     found_n += 1
                        elif s == 'partial': partial_n += 1
                        else:                missing_n += 1

                    total = len(results)
                    score = int(((found_n + partial_n * 0.5) / total) * 100) if total else 0

                    report_html = build_html_report(
                        vf.name, script_name, brand, influencer,
                        detected_lang, results, found_n, partial_n, missing_n, score, model_size
                    )

                    st.session_state.reports[key] = {
                        'html': report_html,
                        'orig_text': orig_text,
                        'en_text': en_text or '',
                        'zh_text': zh_text or '',
                        'segments': segments,
                        'detected_lang': detected_lang,
                        'score': score,
                        'influencer': influencer,
                        'video': vf.name,
                    }
                    score_color = "green" if score >= 80 else ("orange" if score >= 60 else "red")
                    status.update(label=f"Done: {vf.name} — :{score_color}[{score}%]", state="complete")

                if st.session_state.selected is None:
                    st.session_state.selected = key

        overall_progress.progress(1.0, text="All done!")
        st.success(f"Analysis complete! {len(pairs)} video(s) processed.")

    # ══════════════════════════════════════════════════════════════════
    # STEP 5: RESULTS — left list + right report
    # ══════════════════════════════════════════════════════════════════
    if st.session_state.reports:
        st.markdown("---")
        st.markdown("### Reports")

        report_keys = list(st.session_state.reports.keys())

        # Ensure selected key is valid
        if st.session_state.selected not in st.session_state.reports:
            st.session_state.selected = report_keys[0]

        list_col, report_col = st.columns([1, 3])

        # ── LEFT: clickable list ──────────────────────────────────────
        with list_col:
            st.markdown("**Select to View**")
            for key in report_keys:
                d = st.session_state.reports[key]
                sc = d['score']
                if sc is None:
                    icon = "🎬"
                    label = f"{icon} {d['influencer']}\nTranscript only"
                else:
                    icon = "🟢" if sc >= 80 else ("🟡" if sc >= 60 else "🔴")
                    label = f"{icon} {d['influencer']}\n{sc}% match"
                if st.button(label, key=f"sel_{key}", use_container_width=True):
                    st.session_state.selected = key
                    st.rerun()

        # ── RIGHT: selected report ────────────────────────────────────
        with report_col:
            data = st.session_state.reports[st.session_state.selected]

            # If video had a script — show score + match table
            if data['html'] is not None:
                st.components.v1.html(data['html'], height=700, scrolling=True)
            else:
                st.markdown(f"#### {data['video']}")
                st.info("Transcript only — no script was matched for this video.")

            # Transcript — 3 language tabs
            orig_label = f"Original ({data['detected_lang']})"
            st.markdown("#### Full Transcript")
            tab_orig, tab_en, tab_zh = st.tabs([orig_label, "English", "Chinese (中文)"])

            def render_transcript(text, segments):
                if not text:
                    st.caption("(No content)")
                    return
                if segments:
                    for seg in segments:
                        t = int(seg.get('start', 0))
                        ts = f"[{t//60}:{t%60:02d}]"
                        st.markdown(
                            f'<span style="color:#888;font-size:11px">{ts}</span> '
                            f'{html_lib.escape(seg.get("text","").strip())}',
                            unsafe_allow_html=True
                        )
                else:
                    st.write(text)

            with tab_orig:
                render_transcript(data['orig_text'], data['segments'])

            with tab_en:
                if data['en_text']:
                    st.write(data['en_text'])
                else:
                    st.caption("Translation unavailable.")

            with tab_zh:
                if data['zh_text']:
                    st.write(data['zh_text'])
                else:
                    st.caption("Translation unavailable.")

elif video_files and not script_files:
    # ── TRANSCRIBE ONLY MODE ──────────────────────────────────────────
    st.markdown("---")
    st.info("No script uploaded — running in **Transcribe Only** mode. The audio will be transcribed and translated, with no script matching.")

    st.markdown("### Settings")
    c1, c2 = st.columns(2)
    with c1:
        speed_label_t = st.selectbox(
            "Transcription Quality",
            ["Fast (English only)", "Standard (SEA languages)", "Accurate (best quality)"],
            index=1, key="tonly_speed",
        )
    with c2:
        st.markdown("")  # spacer
    model_map_t = {
        "Fast (English only)": "base",
        "Standard (SEA languages)": "small",
        "Accurate (best quality)": "medium",
    }
    model_size_t = model_map_t[speed_label_t]

    transcribe_btn = st.button("Transcribe Videos", type="primary", key="transcribe_only_btn")

    if 'tonly_results' not in st.session_state:
        st.session_state.tonly_results = {}
    if 'tonly_selected' not in st.session_state:
        st.session_state.tonly_selected = None

    if transcribe_btn:
        st.markdown("---")
        tmp_dir = tempfile.mkdtemp()
        overall_progress = st.progress(0)
        st.session_state.tonly_results = {}
        st.session_state.tonly_selected = None

        for i, vf in enumerate(video_files):
            overall_progress.progress(i / len(video_files), text=f"Transcribing {i+1}/{len(video_files)}: {vf.name}")
            with st.status(f"Transcribing: {vf.name}", expanded=True) as status:
                vp = os.path.join(tmp_dir, vf.name)
                with open(vp, 'wb') as f:
                    f.write(vf.getvalue())
                st.write(f"Transcribing audio (Whisper {model_size_t})... 1-3 min")
                orig_text, detected_lang, segments, en_text, zh_text, err = transcribe(vp, model_size_t)
                if err:
                    st.error(f"Error: {err}")
                    status.update(label=f"Error: {vf.name}", state="error")
                    continue
                st.write(f"Done. Language: {detected_lang}")
                key = f"t_{i}_{pathlib.Path(vf.name).stem}"
                st.session_state.tonly_results[key] = {
                    'video': vf.name,
                    'orig_text': orig_text,
                    'en_text': en_text or '',
                    'zh_text': zh_text or '',
                    'segments': segments,
                    'detected_lang': detected_lang,
                }
                if st.session_state.tonly_selected is None:
                    st.session_state.tonly_selected = key
                status.update(label=f"Done: {vf.name} ({detected_lang})", state="complete")

        overall_progress.progress(1.0, text="All done!")
        st.success(f"Transcription complete! {len(video_files)} video(s) processed.")

    if st.session_state.tonly_results:
        st.markdown("---")
        st.markdown("### Transcripts")
        tkeys = list(st.session_state.tonly_results.keys())
        if st.session_state.tonly_selected not in st.session_state.tonly_results:
            st.session_state.tonly_selected = tkeys[0]

        list_col, result_col = st.columns([1, 3])

        with list_col:
            st.markdown("**Select to View**")
            for key in tkeys:
                d = st.session_state.tonly_results[key]
                label = f"🎬 {d['video']}\n{d['detected_lang']}"
                if st.button(label, key=f"tsel_{key}", use_container_width=True):
                    st.session_state.tonly_selected = key
                    st.rerun()

        with result_col:
            data = st.session_state.tonly_results[st.session_state.tonly_selected]
            st.markdown(f"#### {data['video']}")
            st.caption(f"Detected language: `{data['detected_lang']}`")

            tab_orig, tab_en, tab_zh = st.tabs([
                f"Original ({data['detected_lang']})", "English", "Chinese (中文)"
            ])

            def render_t(text, segs):
                if not text:
                    st.caption("(No content)")
                    return
                if segs:
                    for seg in segs:
                        t = int(seg.get('start', 0))
                        ts = f"[{t//60}:{t%60:02d}]"
                        st.markdown(
                            f'<span style="color:#888;font-size:11px">{ts}</span> '
                            f'{html_lib.escape(seg.get("text","").strip())}',
                            unsafe_allow_html=True)
                else:
                    st.write(text)

            with tab_orig:
                render_t(data['orig_text'], data['segments'])
            with tab_en:
                st.write(data['en_text']) if data['en_text'] else st.caption("Translation unavailable.")
            with tab_zh:
                st.write(data['zh_text']) if data['zh_text'] else st.caption("Translation unavailable.")

elif script_files and not video_files:
    st.info("Please also upload video files to continue.")
else:
    st.markdown("""
    <div style="background:white;border-radius:10px;padding:40px;text-align:center;
    color:#888;border:2px dashed #dde2ea;margin-top:24px">
        <div style="font-size:32px;margin-bottom:12px">Upload files to get started</div>
        <div style="font-size:14px">Upload videos on the left, scripts on the right — auto-paired by order.</div>
        <div style="font-size:13px;margin-top:8px;color:#aaa">
        Videos: MP4 / MOV / AVI / MKV (up to 500MB each) &nbsp;|&nbsp; Scripts: PDF / DOCX / TXT
        </div>
    </div>
    """, unsafe_allow_html=True)
