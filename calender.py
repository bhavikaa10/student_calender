"""
student_calendar.py – context‑aware auto‑label (v3)
=================================================
Universal extractor that copes with:
• month‑day dates **without a year** (e.g. "Oct. 18")
• month‑day ranges (first date kept)
• “Week N” lines that inherit a meaningful title from nearby lines

Works for both STA238 (Winter 2025) and MAT235 (2024‑25 Fall/Winter) syllabi
without any manual rule‑tables.

Pipeline
--------
1. **PDF → text** with PyMuPDF.
2. Iterate text line‑by‑line; maintain a sliding window of the *previous 3* lines.
3. On each line emit:
   • *Absolute‑date* events ⇢ uses `parse_date()` which fills missing years and
     resolves cross‑year semesters.
   • *Week‑N* events ⇢ maps to `semester_start + (N‑1)` weeks (+ weekday offset)
     and back‑scans if the line is just "Week N".
4. De‑duplicate → DataFrame → FullCalendar grid; export `.ics` & PDF list.
"""
from __future__ import annotations

# ---------------- stdlib / third‑party -----------------
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

# -------------------- regexes -------------------------
# Month names w/ optional trailing period
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?'?"
# e.g. Oct 18   Oct. 18, 2024   Oct 18‑20
ABS_DATE_RE = re.compile(
    rf"\b(?P<month>{MONTH})\s*(?P<day>\d{{1,2}})(?:\s*[–-]\s*\d{{1,2}})?(?:,?\s*(?P<year>\d{{4}}))?\b",
    re.I,")
# dd/mm/yy or dd-mm-yy fallback
NUM_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
WEEK_RE     = re.compile(r"\bweek\s*(\d{1,2})\b", re.I)

# -------------- title heuristics ----------------------
TITLE_RULES = [
    (re.compile(r"scavenger", re.I),           lambda m: "Syllabus Scavenger Hunt"),
    (re.compile(r"activity\s*(\d+)", re.I),  lambda m: f"Tutorial Activity {m.group(1)}"),
    (re.compile(r"quiz\s*(\d+)", re.I),      lambda m: f"Tutorial Quiz {m.group(1)}"),
    (re.compile(r"mid.?term", re.I),           lambda m: "Mid‑term Test"),
    (re.compile(r"final", re.I),               lambda m: "Final Exam"),
]

BACKSCAN    = 3      # how many previous lines to look at for context
WEEKDAY_OFFSET = 0   # 0=Mon, 3=Thu tutorials, etc.

# ---------------- helper functions --------------------

def iter_lines(text:str)->Iterable[str]:
    for ln in text.splitlines():
        ln = ln.strip()
        if ln:
            yield ln

def smart_title(line:str)->str:
    """Return a concise, descriptive title for a raw syllabus line."""
    for rx, fn in TITLE_RULES:
        m = rx.search(line)
        if m:
            return fn(m)
    clean = re.sub(r"^[•*\-\s]+", "", line).strip()
    return (clean[:77] + "…") if len(clean) > 80 else clean

# ---------------- date parsing -----------------------

def parse_date_fragment(match:re.Match, default_year:int)->date|None:
    """Convert a MONTH‑DAY[‑DAY][, YEAR] match to a date object."""
    month_txt = match.group("month")
    day       = int(match.group("day"))
    year_txt  = match.group("year")
    year      = int(year_txt) if year_txt else default_year
    try:
        d = dt.datetime.strptime(f"{month_txt[:3]} {day} {year}", "%b %d %Y").date()
        return d
    except ValueError:
        return None

def parse_date(line:str, sem_start:date)->list[date]:
    """Return 0‑N date objects detected in the line."""
    found: list[date] = []
    # textual dates
    for m in ABS_DATE_RE.finditer(line):
        d = parse_date_fragment(m, sem_start.year)
        if d:
            # if syllabus crosses New Year, bump Jan‑Apr to next year
            if d < sem_start and sem_start.month >= 8:
                d = d.replace(year=d.year + 1)
            found.append(d)
    # numeric fallback
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
    seen   : set[Tuple[date,str]] = set()
    window : list[str] = []

    for line in iter_lines(text):
        # absolute dates ------------------------------------------------------
        for d in parse_date(line, sem_start):
            title = smart_title(line)
            if re.fullmatch(r"\w+\.?,?\s+\d{1,2}(?:,?\s+\d{4})?", title, re.I):
                # bare date → inherit from window
                for prev in reversed(window):
                    cand = smart_title(prev)
                    if not re.fullmatch(r"Week\s*\d+", cand, re.I):
                        title = cand; break
            if (d,title) not in seen:
                events.append((d,title)); seen.add((d,title))

        # week references -----------------------------------------------------
        for wk_m in WEEK_RE.finditer(line):
            wk = int(wk_m.group(1))
            d  = sem_start + timedelta(weeks=wk-1, days=weekday_offset)
            title = smart_title(line)
            if re.fullmatch(r"Week\s*\d+", title, re.I):
                for prev in reversed(window):
                    cand = smart_title(prev)
                    if not re.fullmatch(r"Week\s*\d+", cand, re.I):
                        title = cand; break
            if (d,title) not in seen:
                events.append((d,title)); seen.add((d,title))

        # slide window --------------------------------------------------------
        window.append(line)
        if len(window) > BACKSCAN:
            window.pop(0)

    return events

# ------------------- export helpers ------------------

def create_ics(events:List[Tuple[date,str]])->bytes:
    cal = Calendar()
    for d, t in events:
        ev = Event(); ev.name = t; ev.begin = d.isoformat(); cal.events.add(ev)
    return cal.serialize().encode()


def pdf_bytes(events:List[Tuple[date,str]])->io.BytesIO:
    pdf = FPDF(); pdf.add_page(); pdf.set_font("Helvetica", size=12)
    pdf.cell(0,10,"Course Calendar", ln=True, align="C"); pdf.ln(4)
    for d,t in events:
        line = f"{d.isoformat()} – {t}".encode("latin-1","replace").decode("latin-1")
        pdf.multi_cell(0,8,line)
    return io.BytesIO(pdf.output(dest="S").encode("latin-1"))

# ---------------------- UI ----------------------------

st.set_page_config(page_title="Student Calendar", layout="centered")
st.title("📘 Student Calendar")
file = st.file_uploader("Upload syllabus PDF", type="pdf")
col1,col2=st.columns(2)
with col1: sem_start = st.date_input("Semester start", value=dt.date.today())
with col2: sem_end   = st.date_input("Semester end",   value=dt.date.today()+dt.timedelta(days=120))

if file:
    doc  = fitz.open(stream=file.read(), filetype="pdf")
    text = "\n".join(p.get_text() for p in doc)

    events = [ (d,t) for d,t in extract_events(text, sem_start, WEEKDAY_OFFSET)
               if sem_start <= d <= sem_end ]
    if not events:
        st.warning("No events detected in that range"); st.stop()

    df = (pd.DataFrame({"Date":[d.isoformat() for d,_ in events],
                        "Event":[t for _,t in events]})
          .drop_duplicates().sort_values("Date"))

    with st.expander("Show table", False):
        st.dataframe(df, height=240)

    fc_events=[{"title":r.Event[:40]+("…" if len(r.Event)>40 else ""),
                "start":r.Date,
                "description":r.Event} for r in df.itertuples()]

    calendar(fc_events,
        {
            "initialView":"dayGridMonth",
            "height":"auto",
            "eventClick":"function(e){alert(e.event.extendedProps.description)}",
            "headerToolbar": {"left":"today prev,next","center":"title","right":"dayGridMonth,timeGridWeek,listMonth"},
        }, key="fc")

    st.markdown("---")
    colA,colB=st.columns(2)
    with colA:
        st.download_button("📆 Download .ics", create_ics(events),
                           file_name="course_calendar.ics", mime="text/calendar")
    with colB:
        st.download_button("🖨️ Download PDF list", pdf_bytes(events),
                           file_name="course_calendar.pdf", mime="application/pdf")
