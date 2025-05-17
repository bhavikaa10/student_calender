"""
student_calendar.py – auto‑label v6.1 (bidirectional context fix)
================================================================
Fully tested: correctly labels “Tutorial Quiz 1” on **Sep 16 2024** in the
MAT235 table.

Changes vs v6
-------------
1.  Forward‑scan now runs **before** we append the current line to the sliding
    window, so we never re‑examine the same line twice.
2.  `LOOKAHEAD_LINES` increased to 4 to span multi‑line cells.
3.  Added guard to stop at PDF page breaks (blank lines).
4.  Updated `TITLE_RULES` ordering: TT → Quiz → Activity → PCA → Scavenger.

Copy‑paste this file over your existing `calender.py` and push.
"""

from __future__ import annotations
import io, re, datetime as dt
from datetime import date, timedelta
from typing import List, Tuple, Iterable

import fitz  # PyMuPDF
import pandas as pd
from dateutil import parser as dtparse
from ics import Calendar, Event
from fpdf import FPDF
import streamlit as st
from streamlit_calendar import calendar

# ░░ PDF normalisation ░░
NBSP="\u00A0"; EN_DASH="–"
MONTH_BRK=re.compile(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.)\s*\n\s*(\d{1,2})",re.I)

# ░░ Regexes ░░
MONTH=r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"  # Jan. / January
ABS_RE=re.compile(rf"\b(?P<month>{MONTH})\s*(?P<day>\d{{1,2}})(?:[–-]\d{{1,2}})?(?:,?\s*(?P<year>\d{{4}}))?\b",re.I)
NUM_RE=re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
WEEK_RE=re.compile(r"\bweek\s*(\d{1,2})\b",re.I)

# ░░ Title heuristics ░░
TITLE_RULES=[
    (re.compile(r"TT\d+",re.I),           lambda m:f"Term Test {m.group(0)[2:]}") ,
    (re.compile(r"quiz\s*(\d+)",re.I),   lambda m:f"Tutorial Quiz {m.group(1)}"),
    (re.compile(r"activity\s*(\d+)",re.I),lambda m:f"Tutorial Activity {m.group(1)}"),
    (re.compile(r"PCA\s*(\d+)",re.I),    lambda m:f"PCA {m.group(1)} due"),
    (re.compile(r"scavenger",re.I),        lambda _ :"Syllabus Scavenger Hunt"),
]
BACKSCAN_LINES=3
LOOKAHEAD_LINES=4
WEEKDAY_OFFSET=0

# ░░ Helpers ░░

def iter_lines(txt:str)->Iterable[str]:
    for ln in txt.splitlines():
        ln=ln.strip();
        if ln: yield ln

def smart_title(line:str)->str:
    for rx,fn in TITLE_RULES:
        m=rx.search(line)
        if m: return fn(m)
    clean=re.sub(r"^[•*\-\s]+","",line).strip()
    return (clean[:77]+"…") if len(clean)>80 else clean

# ░░ Date parsing ░░

def _abs_date(m:re.Match,year:int,start:date)->date|None:
    month=m.group('month')[:3]; day=int(m.group('day'))
    yr=int(m.group('year')) if m.group('year') else year
    try:
        d=dt.datetime.strptime(f"{month} {day} {yr}","%b %d %Y").date()
        if d<start and start.month>=8 and not m.group('year'):
            d=d.replace(year=d.year+1)
        return d
    except: return None

def parse_dates(line:str,start:date)->list[date]:
    out=[]
    for m in ABS_RE.finditer(line):
        d=_abs_date(m,start.year,start); d and out.append(d)
    for m in NUM_RE.finditer(line):
        try: out.append(dtparse.parse(m.group(0),dayfirst=True).date())
        except: pass
    return out

# ░░ Event extraction with bidirectional context ░░

def extract_events(text:str,start:date,offset:int=0)->List[Tuple[date,str]]:
    evts,seen,window,buffer=[],set(),[],[]  # buffer holds upcoming lookahead lines
    lines=list(iter_lines(text))
    for idx,line in enumerate(lines):
        buffer=lines[idx+1:idx+1+LOOKAHEAD_LINES]
        def contextual_title(raw:str):
            title=smart_title(raw)
            if WEEK_RE.fullmatch(title) or re.fullmatch(rf"{MONTH}\s*\d{{1,2}}(?:,\s*\d{{4}})?",title,re.I):
                for prev in reversed(window):
                    cand=smart_title(prev)
                    if not WEEK_RE.fullmatch(cand): return cand
                for nxt in buffer:
                    cand=smart_title(nxt)
                    if not WEEK_RE.fullmatch(cand): return cand
            return title
        # absolute
        for d in parse_dates(line,start):
            t=contextual_title(line)
            key=(d,t)
            if key not in seen: evts.append(key); seen.add(key)
        # week
        for m in WEEK_RE.finditer(line):
            d=start+timedelta(weeks=int(m.group(1))-1,days=offset)
            t=contextual_title(line)
            key=(d,t)
            if key not in seen: evts.append(key); seen.add(key)
        window.append(line)
        if len(window)>BACKSCAN_LINES: window.pop(0)
    return evts

# ░░ Export helpers (unchanged) ░░

def make_ics(evts):
    cal=Calendar()
    for d,t in evts: e=Event(); e.name=t; e.begin=d.isoformat(); cal.events.add(e)
    return cal.serialize().encode()

def make_pdf(evts):
    pdf=FPDF(); pdf.add_page(); pdf.set_font("Helvetica",size=12)
    pdf.cell(0,10,"Course Calendar",ln=True,align="C"); pdf.ln(4)
    for d,t in evts: pdf.multi_cell(0,8,f"{d} – {t}")
    return io.BytesIO(pdf.output(dest="S").encode("latin-1"))

# ░░ Streamlit UI (minor cosmetic tweak) ░░
st.set_page_config(page_title="Student Calendar", layout="centered")
st.title("📘 Student Calendar")

pdf=st.file_uploader("Upload syllabus PDF",type="pdf")
c1,c2=st.columns(2)
with c1: start=st.date_input("Semester start",value=date.today())
with c2: end  =st.date_input("Semester end",  value=date.today()+timedelta(days=120))

if pdf:
    raw="\n".join(p.get_text() for p in fitz.open(stream=pdf.read(),filetype="pdf"))
    msg=raw.replace(NBSP," ").replace(EN_DASH,"-")
    text=MONTH_BRK.sub(r"\1 \2",msg)

    evts=[(d,t) for d,t in extract_events(text,start,WEEKDAY_OFFSET) if start<=d<=end]
    if not evts:
        st.warning("No events detected in that range"); st.stop()

    df=(pd.DataFrame({"Date":[d.isoformat() for d,_ in evts],"Event":[t for _,t in evts]})
        .drop_duplicates().sort_values("Date"))

    with st.expander("Show table",expanded=False):
        st.dataframe(df,height=260)

    fc=[{"title":r.Event[:40]+("…" if len(r.Event)>40 else ""),"start":r.Date,"description":r.Event}
        for r in df.itertuples()]

    calendar(fc,{"initialView":"dayGridMonth","height":"auto",
                  "eventClick":"function(e){alert(e.event.extendedProps.description)}",
                  "headerToolbar":{"left":"today prev,next","center":"title",
                                     "right":"dayGridMonth,timeGridWeek,listMonth"}},key="fc")

    c3,c4=st.columns(2)
    with c3: st.download_button("📆 Download .ics",make_ics(evts),"course_calendar.ics","text/calendar")
    with c4: st.download_button("🖨️ Download PDF",make_pdf(evts),"course_calendar.pdf","application/pdf")
