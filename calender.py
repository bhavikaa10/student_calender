"""
syllabus_calendar_app.py – auto‑label edition 2
==============================================
Adds *context back‑scan* so bare "Week N" rows inherit a meaningful title from
nearby lines (e.g., the preceding "Activity 1" bullet). No manual rule‑setting
needed.

Pipeline
--------
1. PDF → text via PyMuPDF.
2. Walk the text line‑by‑line keeping a sliding window of the *previous 3
   lines*.
3. For each line:
   • emit absolute‑date events;  
   • emit Week‑N events. If the line’s smart_title is merely "Week N",
     back‑scan the window for the most recent non‑Week line and use that as the
     title (so you get "Tutorial Activity 1" instead of just "Week 2").
4. Render in FullCalendar; export .ics / PDF.
"""
from __future__ import annotations

# -------------------- stdlib / third‑party --------------------
import re, io, datetime as dt
from datetime import date, timedelta
from typing import List, Tuple

import fitz                     # PyMuPDF
import pandas as pd
from dateutil import parser as dtparse
from ics import Calendar, Event
from fpdf import FPDF
import streamlit as st
from streamlit_calendar import calendar

# -------------------- heuristics -------------------------------
TITLE_RULES = [
    (re.compile(r"scavenger", re.I),              lambda m: "Syllabus Scavenger Hunt"),
    (re.compile(r"activity\s*(\d+)", re.I),     lambda m: f"Tutorial Activity {m.group(1)}"),
    (re.compile(r"quiz\s*(\d+)", re.I),         lambda m: f"Tutorial Quiz {m.group(1)}"),
    (re.compile(r"midterm", re.I),                lambda m: "Mid‑term Test"),
    (re.compile(r"final", re.I),                  lambda m: "Final Exam"),
]

ABS_DATE_RE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+\s+\d{1,2},\s*\d{4})\b", re.I)
WEEK_RE     = re.compile(r"\bweek\s*(\d{1,2})\b", re.I)
WEEKDAY_OFFSET = 0   # 0=Mon, 3=Thu, etc.

# -------------------- helpers ----------------------------------

def iter_lines(text:str):
    for line in text.splitlines():
        if line.strip():
            yield line.strip()

def smart_title(line:str)->str:
    for rx, fn in TITLE_RULES:
        m = rx.search(line)
        if m:
            return fn(m)
    clean = re.sub(r"^[•*\-\s]+", "", line).strip()
    return (clean[:77] + "…") if len(clean) > 80 else clean

# -------------------- unified extraction -----------------------

def extract_events(text:str, sem_start:date, weekday_offset:int=0)->List[Tuple[date,str]]:
    events:List[Tuple[date,str]] = []
    seen_abs:set[date] = set()
    window:list[str] = []  # last 3 lines

    for line in iter_lines(text):
        # absolute dates
        for m in ABS_DATE_RE.finditer(line):
            d = dtparse.parse(m.group(0), fuzzy=True).date()
            if d not in seen_abs:
                seen_abs.add(d)
                events.append((d, smart_title(line)))

        # week references
        for m in WEEK_RE.finditer(line):
            wk = int(m.group(1))
            d  = sem_start + timedelta(weeks=wk-1, days=weekday_offset)
            title = smart_title(line)
            if re.fullmatch(r"Week\s*\d+", title, re.I):
                # back‑scan last 3 lines for descriptive title
                for prev in reversed(window):
                    cand = smart_title(prev)
                    if not re.fullmatch(r"Week\s*\d+", cand, re.I):
                        title = cand
                        break
            events.append((d, title))

        # slide window
        window.append(line)
        if len(window) > 3:
            window.pop(0)

    return events

# -------------------- export helpers ---------------------------

def create_ics(events:List[Tuple[date,str]])->bytes:
    cal = Calendar()
    for d, t in events:
        ev = Event(); ev.name = t; ev.begin = d.strftime("%Y-%m-%d"); cal.events.add(ev)
    return cal.serialize().encode()

def pdf_bytes(events:List[Tuple[date,str]])->io.BytesIO:
    pdf = FPDF(); pdf.add_page(); pdf.set_font("Helvetica", size=12)
    pdf.cell(0,10,"Course Calendar",ln=True,align="C"); pdf.ln(4)
    for d,t in events:
        line=f"{d.isoformat()} – {t}".encode("latin-1","replace").decode("latin-1")
        pdf.multi_cell(0,8,line)
    return io.BytesIO(pdf.output(dest="S").encode("latin-1"))

# -------------------- Streamlit UI -----------------------------

st.title("📘 Student Calendar")
file = st.file_uploader("Upload syllabus PDF", type="pdf")
col1,col2=st.columns(2)
with col1:  sem_start = st.date_input("Semester start", value=dt.date(2025,1,6))
with col2:  sem_end   = st.date_input("Semester end",   value=dt.date(2025,4,4))

if file:
    doc = fitz.open(stream=file.read(), filetype="pdf")
    text = "\n".join(p.get_text() for p in doc)

    evts = [ (d,t) for d,t in extract_events(text, sem_start, WEEKDAY_OFFSET) if sem_start<=d<=sem_end ]
    if not evts:
        st.warning("No events detected in that range"); st.stop()

    df = pd.DataFrame({"Date":[d.isoformat() for d,_ in evts],"Event":[t for _,t in evts]}).drop_duplicates().sort_values("Date")

    with st.expander("Show table", False):
        st.dataframe(df, height=240)

    fc_events=[{"title":row.Event,"start":row.Date,"description":row.Event} for row in df.itertuples()]
    _=calendar(fc_events,{"initialView":"dayGridMonth","height":"auto","eventClick":"function(i){alert(i.event.extendedProps.description);} "},key="fc")

    st.markdown("---")
    colA,colB=st.columns(2)
    with colA: st.download_button("📆 .ics", create_ics(evts), file_name="course_calendar.ics", mime="text/calendar")
    with colB: st.download_button("🖨 PDF list", pdf_bytes(evts), file_name="course_calendar.pdf", mime="application/pdf")
