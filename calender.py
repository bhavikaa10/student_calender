"""
student_calendar.py – auto‑label v5 (Assessment‑aware)
=====================================================
Incremental fix so **Quizzes** and **TT1 / TT2 / TT3** rows in MAT235 tables
produce proper calendar events.  Changes:

1.  `TITLE_RULES` now prioritises **Term Tests (TT\d+)** and **Quiz N** before
    Activities or PCA due‑dates.
2.  `smart_title()` returns the *first* matching rule in evaluation order; this
    prevents a "PCA 6 due …" line from overriding a Quiz or TT on the same row.

Everything else (date parsing, week inheritance, PDF normalisation) unchanged.

Tested again on:
  • STA238 Winter 2025 — unchanged output
  • MAT235Y1 2024‑25 — now shows Quiz 1‒10 and TT1/TT2/TT3 entries.
"""
from __future__ import annotations

import io, re, datetime as dt
from datetime import date, timedelta
from typing import List, Tuple, Iterable

import fitz                      # PyMuPDF
import pandas as pd
from dateutil import parser as dtparse
from ics import Calendar, Event
from fpdf import FPDF
import streamlit as st
from streamlit_calendar import calendar

# ---------------- text normalisation -----------------
NBSP = "\u00A0"
EN_DASH = "–"
MONTH_BREAK_RE = re.compile(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.)\s*\n\s*(\d{1,2})", re.I)

# -------------------- regexes ------------------------
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?'?"
ABS_DATE_RE = re.compile(
    rf"\b(?P<month>{MONTH})\s*(?P<day>\d{{1,2}})(?:[–-]\d{{1,2}})?(?:,?\s*(?P<year>\d{{4}}))?\b",
    re.I,
)
NUM_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
WEEK_RE     = re.compile(r"\bweek\s*(\d{1,2})\b", re.I)

# -------------- title heuristics ---------------------
TITLE_RULES = [
    (re.compile(r"TT\s*(\d+)",       re.I), lambda m: f"Term Test {m.group(1)}"),
    (re.compile(r"quiz\s*(\d+)",     re.I), lambda m: f"Tutorial Quiz {m.group(1)}"),
    (re.compile(r"activity\s*(\d+)", re.I), lambda m: f"Tutorial Activity {m.group(1)}"),
    (re.compile(r"scavenger",          re.I), lambda m: "Syllabus Scavenger Hunt"),
    (re.compile(r"mid.?term",          re.I), lambda m: "Mid‑term Test"),
    (re.compile(r"final",              re.I), lambda m: "Final Exam"),
]

BACKSCAN_LINES  = 3  # how many previous lines to inspect
WEEKDAY_OFFSET  = 0  # 0=Mon, 3=Thu, etc.

# ---------------- helper functions -------------------

def iter_lines(text:str)->Iterable[str]:
    for ln in text.splitlines():
        ln = ln.strip()
        if ln:
            yield ln

def smart_title(line:str)->str:
    for rx, fn in TITLE_RULES:
        m = rx.search(line)
        if m:
            return fn(m)
    clean = re.sub(r"^[•*\-\s]+", "", line).strip()
    return (clean[:77] + "…") if len(clean) > 80 else clean

# ---------------- date parsing -----------------------

def parse_date_fragment(match:re.Match, default_year:int)->date|None:
    month_txt = match.group("month")
    day       = int(match.group("day"))
    year_txt  = match.group("year")
    year      = int(year_txt) if year_txt else default_year
    try:
        return dt.datetime.strptime(f"{month_txt[:3]} {day} {year}", "%b %d %Y").date()
    except ValueError:
        return None

def parse_dates(line:str, sem_start:date)->list[date]:
    found: list[date] = []
    # textual dates
    for m in ABS_DATE_RE.finditer(line):
        d = parse_date_fragment(m, sem_start.year)
        if d:
            if d < sem_start and sem_start.month >= 8:
                d = d.replace(year=d.year + 1)
            found.append(d)
    # numeric dates
    for m in NUM_DATE_RE.finditer(line):
        try:
            d = dtparse.parse(m.group(0), dayfirst=True, fuzzy=True).date()
            found.append(d)
        except Exception:
            pass
    return found

# --------------- unified extractor -------------------

def extract_events(text:str, sem_start:date, weekday_offset:int=0)->List[Tuple[date,str]]:
    events: list[Tuple[date,str]] = []
    seen  : set[Tuple[date,str]] = set()
    window: list[str] = []

    for line in iter_lines(text):
        # absolute dates -----------------------------------------------
        for d in parse_dates(line, sem_start):
            title = smart_title(line)
            if re.fullmatch(r"\w+\.?\,?\s+\d{1,2}(?:,?\s+\d{4})?", title, re.I):
                for prev in reversed(window):
                    cand = smart_title(prev)
                    if not WEEK_RE.fullmatch(cand):
                        title = cand; break
            if (d,title) not in seen:
                events.append((d,title)); seen.add((d,title))

        # week references ---------------------------------------------
        for wk_m in WEEK_RE.finditer(line):
            wk = int(wk_m.group(1))
            d  = sem_start + timedelta(weeks=wk-1, days=weekday_offset)
            title = smart_title(line)
            if WEEK_RE.fullmatch(title):
                for prev in reversed(window):
                    cand = smart_title(prev)
                    if not WEEK_RE.fullmatch(cand):
                        title = cand; break
            if (d,title) not in seen:
                events.append((d,title)); seen.add((d,title))

        window.append(line)
        if len(window) > BACKSCAN_LINES:
            window.pop(0)

    return events

# ------------------ exports --------------------------

def make_ics(evts:List[Tuple[date,str]])->bytes:
    cal = Calendar()
    for d,t in evts:
        e = Event(); e.name=t; e.begin=d.isoformat(); cal.events.add(e)
    return cal.serialize().encode()

def make_pdf(evts:List[Tuple[date,str]])->io.BytesIO:
    pdf=FPDF(); pdf.add_page(); pdf.set_font("Helvetica", size=12)
    pdf.cell(0,10,"Course Calendar",ln=True,align="C"); pdf.ln(4)
    for d,t in evts:
        line=f"{d.isoformat()} – {t}".encode("latin-1","replace").decode("latin-1")
        pdf.multi_cell(0,8,line)
    return io.BytesIO(pdf.output(dest="S").encode("latin-1"))

# ---------------------- app --------------------------

st.set_page_config(page_title="Student Calendar", layout="centered")
st.title("📘 Student Calendar")
file = st.file_uploader("Upload syllabus PDF", type="pdf")
col1,col2 = st.columns(2)
with col1: sem_start = st.date_input("Semester start", value=dt.date.today())
with col2: sem_end   = st.date_input("Semester end",   value=dt.date.today()+timedelta(days=120))

if file:
    doc = fitz.open(stream=file.read(), filetype="pdf")
    raw = "\n".join(p.get_text() for p in doc)
    text = raw.replace(NBSP," ").replace(EN_DASH,"-")
    text = MONTH_BREAK_RE.sub(r"\1 \2", text)

    evts=[(d,t) for d,t in extract_events(text, sem_start, WEEKDAY_OFFSET)
          if sem_start<=d<=sem_end]

    if not evts:
        st.warning("No events detected in that range"); st.stop()

    df=(pd.DataFrame({"Date":[d.isoformat() for d,_ in evts],"Event":[t for _,t in evts]})
        .drop_duplicates().sort_values("Date"))

    with st.expander("Show table", False):
        st.dataframe(df, height=240)

    fc=[{"title":r.Event[:40]+("…" if len(r.Event)>40 else ""),
         "start":r.Date,
         "description":r.Event} for r in df.itertuples()]

    calendar(fc,
        {"initialView":"dayGridMonth","height":"auto",
         "eventClick":"function(e){alert(e.event.extendedProps.description)}",
         "headerToolbar":{"left":"today prev,next","center":"title","right":"dayGridMonth,timeGridWeek,listMonth"}},
        key="fc")

    st.markdown("---")
    dl1,dl2 = st.columns(2)
    with dl1:
        st.download_button("📆 Download .ics", make_ics(evts), "course_calendar.ics", "text/calendar")
    with dl2:
        st.download_button("🖨️ Download PDF list", make_pdf(evts), "course_calendar.pdf", "application/pdf")
