"""
student_calendar.py – v6.4 (UofT Academic Calendar Integration)
=======================================================
• Added semester detection from syllabus
• Added UofT academic calendar integration
• Added holiday and break detection
"""

from __future__ import annotations
import io, re, datetime as dt
from datetime import date, timedelta
from typing import List, Tuple, Iterable
import requests
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
import pandas as pd
from dateutil import parser as dtparse
from ics import Calendar, Event
from fpdf import FPDF
import streamlit as st
from streamlit_calendar import calendar
import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

# ░░ Semester Detection ░░
SEMESTER_PATTERNS = [
    (re.compile(r"(?:fall|autumn)\s*(?:semester|term|session)", re.I), "F"),
    (re.compile(r"(?:winter)\s*(?:semester|term|session)", re.I), "S"),
    (re.compile(r"(?:summer)\s*(?:semester|term|session)", re.I), "Y"),
]

def detect_semester(text: str) -> str:
    """Detect semester from syllabus text."""
    for pattern, semester in SEMESTER_PATTERNS:
        if pattern.search(text):
            return semester
    return None

# ░░ UofT Academic Calendar Integration ░░
UOFT_CALENDAR_URL = "https://www.artsci.utoronto.ca/current/dates-deadlines/academic-dates"

def fetch_uoft_calendar() -> List[Tuple[date, str]]:
    """Fetch and parse UofT academic calendar."""
    try:
        response = requests.get(UOFT_CALENDAR_URL)
        soup = BeautifulSoup(response.text, 'html.parser')
        events = []
        
        # Find all tables in the page
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cols = row.find_all('td')
                if len(cols) >= 2:
                    date_str = cols[0].get_text().strip()
                    event_desc = cols[1].get_text().strip()
                    try:
                        event_date = dtparse.parse(date_str).date()
                        events.append((event_date, event_desc))
                    except:
                        continue
        return events
    except Exception as e:
        st.error(f"Error fetching UofT calendar: {str(e)}")
        return []

# ░░ PDF normalisation ░░
NBSP = "\u00A0"; EN_DASH = "–"
MONTH_BRK = re.compile(r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.)\s*\n\s*(\d{1,2})", re.I)

# ░░ Regexes ░░
MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
ABS_RE = re.compile(rf"""
    \b(?:
        (?P<month>{MONTH})\s*(?P<day>\d{{1,2}})(?:[–-]\d{{1,2}})?(?:,?\s*(?P<year>\d{{4}}))?  # Jan 15, 2024
        |(?P<day2>\d{{1,2}})\s+(?P<month2>{MONTH})(?:,?\s*(?P<year2>\d{{4}}))?              # 15 Jan 2024
        |(?P<month3>{MONTH})\s*(?P<day3>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(?P<year3>\d{{4}}))?  # January 15th, 2024
    )\b
""", re.I | re.X)
NUM_RE = re.compile(r"""
    \b(?:
        \d{1,2}[/-]\d{1,2}[/-]\d{2,4}  # 01/15/2024 or 1-15-24
        |\d{4}[/-]\d{1,2}[/-]\d{1,2}   # 2024/01/15
    )\b
""", re.X)
WEEK_RE = re.compile(r"\b(?:week|wk)\s*(\d{1,2})\b", re.I)

# ░░ Title heuristics ░░
TITLE_RULES = [
    (re.compile(r"TT\d+", re.I),            lambda m: f"Term Test {m.group(0)[2:]}"),
    (re.compile(r"quiz\s*(\d+)", re.I),    lambda m: f"Tutorial Quiz {m.group(1)}"),
    (re.compile(r"activity\s*(\d+)", re.I), lambda m: f"Tutorial Activity {m.group(1)}"),
    (re.compile(r"PCA\s*(\d+)", re.I),     lambda m: f"PCA {m.group(1)} due"),
    (re.compile(r"scavenger", re.I),         lambda _: "Syllabus Scavenger Hunt"),
    (re.compile(r"assignment\s*(\d+)", re.I), lambda m: f"Assignment {m.group(1)} due"),
    (re.compile(r"project\s*(\d+)", re.I),   lambda m: f"Project {m.group(1)} due"),
    (re.compile(r"midterm", re.I),           lambda _: "Midterm Exam"),
    (re.compile(r"final\s*exam", re.I),      lambda _: "Final Exam"),
]
BACKSCAN_LINES = 3
LOOKAHEAD_LINES = 4
WEEKDAY_OFFSET = 0  # 0 = Monday, adjust if you want week events on another day

# ░░ Helpers ░░

def iter_lines(txt: str) -> Iterable[str]:
    for ln in txt.splitlines():
        ln = ln.strip()
        if ln:
            yield ln

def smart_title(line: str) -> str:
    for rx, fn in TITLE_RULES:
        m = rx.search(line)
        if m:
            return fn(m)
    clean = re.sub(r"^[•*\-\s]+", "", line).strip()
    if not clean:
        return "Event"
    return (clean[:77] + "…") if len(clean) > 80 else clean

# ░░ Date parsing ░░

def _abs_date(m: re.Match, year: int, start: date) -> date | None:
    # Try different capture groups
    for i in range(1, 4):
        month = m.groupdict().get(f'month{i}') or m.groupdict().get('month')
        day = m.groupdict().get(f'day{i}') or m.groupdict().get('day')
        yr = m.groupdict().get(f'year{i}') or m.groupdict().get('year')
        
        if month and day:
            month = month[:3]  # Normalize month name
            day = int(day)
            yr = int(yr) if yr else year
            
            try:
                d = dt.datetime.strptime(f"{month} {day} {yr}", "%b %d %Y").date()
                # rollover for Fall terms where year isn't specified
                if d < start and start.month >= 8 and not yr:
                    d = d.replace(year=d.year + 1)
                return d
            except Exception:
                continue
    return None

def parse_dates(line: str, start: date) -> list[date]:
    out = []
    # Try absolute dates
    for m in ABS_RE.finditer(line):
        d = _abs_date(m, start.year, start)
        if d:
            out.append(d)
    
    # Try numerical dates
    for m in NUM_RE.finditer(line):
        try:
            # Try different date formats
            for fmt in ["%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%m-%d-%Y", "%Y-%m-%d"]:
                try:
                    d = dt.datetime.strptime(m.group(0), fmt).date()
                    # Handle 2-digit years
                    if d.year < 100:
                        d = d.replace(year=2000 + d.year)
                    out.append(d)
                    break
                except ValueError:
                    continue
        except Exception:
            pass
    return out

# ░░ Event extraction with bidirectional context ░░

def extract_events(text: str, start: date, offset: int = 0) -> List[Tuple[date, str]]:
    evts, seen, window = [], set(), []
    lines = list(iter_lines(text))
    
    # Detect semester
    semester = detect_semester(text)
    if semester:
        st.info(f"Detected {semester} semester")
        
        # Fetch UofT calendar events
        uoft_events = fetch_uoft_calendar()
        for d, t in uoft_events:
            if start <= d <= end:
                evts.append((d, f"UofT: {t}"))
                seen.add((d, t))

    for idx, line in enumerate(lines):
        buffer = lines[idx + 1: idx + 1 + LOOKAHEAD_LINES]

        def contextual_title(raw: str) -> str:
            title = smart_title(raw)
            # If title still looks like a bare date / week label, peek around
            if WEEK_RE.fullmatch(title) or re.fullmatch(rf"{MONTH}\s*\d{{1,2}}(?:,\s*\d{{4}})?", title, re.I):
                for prev in reversed(window):
                    cand = smart_title(prev)
                    if not WEEK_RE.fullmatch(cand):
                        return cand
                for nxt in buffer:
                    cand = smart_title(nxt)
                    if not WEEK_RE.fullmatch(cand):
                        return cand
            return title or "Event"

        # absolute dates
        for d in parse_dates(line, start):
            t = contextual_title(line)
            key = (d, t)
            if key not in seen:
                evts.append(key)
                seen.add(key)
        # week‑based dates
        for m in WEEK_RE.finditer(line):
            d = start + timedelta(weeks=int(m.group(1)) - 1, days=offset)
            t = contextual_title(line)
            key = (d, t)
            if key not in seen:
                evts.append(key)
                seen.add(key)
        window.append(line)
        if len(window) > BACKSCAN_LINES:
            window.pop(0)
    return evts

# ░░ Export helpers ░░

def _latin1(txt: str) -> str:
    """Return a version that is guaranteed Latin‑1 encodable (replace unknowns)."""
    return txt.encode('latin-1', 'replace').decode('latin-1')


def make_ics(evts: list[tuple[date, str]]):
    cal = Calendar()
    for d, t in evts:
        e = Event()
        e.name = t or "Event"
        e.begin = d.isoformat()
        cal.events.add(e)
    return cal.serialize().encode()


def make_pdf(evts: list[tuple[date, str]]):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, _latin1("Course Calendar"), ln=True, align="C")
    pdf.ln(4)
    for d, t in evts:
        txt = f"{d} – {t or 'Event'}"
        pdf.multi_cell(0, 8, _latin1(txt))
    # Return BytesIO so Streamlit can send it directly
    return io.BytesIO(pdf.output(dest="S").encode('latin-1', 'replace'))

# ░░ Streamlit UI ░░
st.set_page_config(page_title="Student Calendar", layout="centered")
st.title("📘 Student Calendar")

pdf_file = st.file_uploader("Upload syllabus PDF", type="pdf")
c1, c2 = st.columns(2)
with c1:
    start = st.date_input("Semester start", value=date.today())
with c2:
    end = st.date_input("Semester end", value=date.today() + timedelta(days=120))

if pdf_file:
    raw_text = "\n".join(p.get_text() for p in fitz.open(stream=pdf_file.read(), filetype="pdf"))
    cleaned = MONTH_BRK.sub(r"\1 \2", raw_text.replace(NBSP, " ").replace(EN_DASH, "-"))

    evts = [(d, t) for d, t in extract_events(cleaned, start, WEEKDAY_OFFSET) if start <= d <= end]
    if not evts:
        st.warning("No events detected in that range")
        st.stop()

    # Add UofT calendar events
    uoft_events = fetch_uoft_calendar()
    for d, t in uoft_events:
        if start <= d <= end:
            evts.append((d, f"UofT: {t}"))

    df = (
        pd.DataFrame({"Date": [d.isoformat() for d, _ in evts], "Event": [t for _, t in evts]})
        .drop_duplicates()
        .sort_values("Date")
    )

    with st.expander("Show table", expanded=False):
        st.dataframe(df, height=260)

    fc_events = [
        {
            "title": str(r.Event)[:40] + ("…" if len(str(r.Event)) > 40 else ""),
            "start": str(r.Date),      # ISO string mandatory
            "allDay": True,            # ensure it shows in month view
            "description": str(r.Event),
        }
        for r in df.itertuples()
    ]

    calendar(
        fc_events,
        {
            "initialView": "dayGridMonth",
            "height": "auto",
            "eventClick": "function(e){alert(e.event.extendedProps.description)}",
            "headerToolbar": {
                "left": "today prev,next",
                "center": "title",
                "right": "dayGridMonth,timeGridWeek,listMonth",
            },
        },
        key="fc",
    )

    c3, c4 = st.columns(2)
    with c3:
        st.download_button("📆 Download .ics", make_ics(evts), "course_calendar.ics", "text/calendar")
    with c4:
        st.download_button("🖨️ Download PDF", make_pdf(evts), "course_calendar.pdf", "application/pdf")
