"""
syllabus_calendar_app.py  –  auto‑label edition
================================================
Automatically builds concise event titles from the syllabus text — no more hard‑
coding dictionaries like FIXED_DATE_TITLES or WEEK_TITLE_MAP.  It works by

1. scanning each **line** of the PDF;
2. detecting either a concrete date or a “Week N” reference;
3. extracting a clean, human‑readable title using keyword rules such as
      • "Activity 2"   →  "Tutorial Activity 2"
      • "Quiz 3"       →  "Tutorial Quiz 3"
      • lines that include "scavenger" → "Syllabus Scavenger Hunt";
      • fallback = the entire cleaned line (trimmed to 80 chars).

This file **replaces** the previous version in full.
To run:
    pip install -r requirements.txt
    streamlit run calender.py
"""
from __future__ import annotations

# -------------------------- standard libs & third‑party ----------------------
import re
import io
import datetime as dt
from datetime import timedelta, date
from typing import List, Tuple

import fitz  # PyMuPDF
import pandas as pd
from dateutil import parser as dtparse
from ics import Calendar, Event
from fpdf import FPDF
import streamlit as st
from streamlit_calendar import calendar  # FullCalendar wrapper

# -------------------------- keyword heuristics -------------------------------
# Order matters: first pattern that matches wins
TITLE_RULES = [
    (re.compile(r"scavenger", re.I),               lambda m: "Syllabus Scavenger Hunt"),
    (re.compile(r"activity\s*(\d+)", re.I),       lambda m: f"Tutorial Activity {m.group(1)}"),
    (re.compile(r"quiz\s*(\d+)", re.I),           lambda m: f"Tutorial Quiz {m.group(1)}"),
    (re.compile(r"midterm", re.I),                 lambda m: "Mid‑term Test"),
    (re.compile(r"final", re.I),                   lambda m: "Final Exam"),
]

ABS_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"      # 2/28/25 or 02-28-2025
    r"\w+\s+\d{1,2},\s*\d{4})\b",            # Feb 28, 2025
    re.I,
)
WEEK_RE = re.compile(r"\bweek\s*(\d{1,2})\b", re.I)

WEEKDAY_OFFSET = 0   # 0 = Monday, 1 = Tuesday … set to 3 for Thu tutorials

# -------------------------- helpers ------------------------------------------

def iter_lines(text: str):
    """Yield stripped, non‑empty lines from the PDF text."""
    for line in text.splitlines():
        if line.strip():
            yield line.strip()


def smart_title(line: str) -> str:
    """Return a concise title based on TITLE_RULES or fallback to the line."""
    for regex, fn in TITLE_RULES:
        m = regex.search(line)
        if m:
            return fn(m)
    # fallback – strip leading dashes/bullets and truncate
    clean = re.sub(r"^[•*\-\s]+", "", line).strip()
    return (clean[:77] + "…") if len(clean) > 80 else clean

# -------------------------- extraction logic ---------------------------------

def extract_absolute_dates(text: str) -> List[Tuple[date, str]]:
    events, seen = [], set()
    for line in iter_lines(text):
        for m in ABS_DATE_RE.finditer(line):
            d = dtparse.parse(m.group(0), fuzzy=True).date()
            if d not in seen:
                seen.add(d)
                events.append((d, smart_title(line)))
    return events


def extract_week_events(text: str, sem_start: date, weekday_offset: int = 0) -> List[Tuple[date, str]]:
    events = []
    for line in iter_lines(text):
        for m in WEEK_RE.finditer(line):
            week_num = int(m.group(1))
            d = sem_start + timedelta(weeks=week_num - 1, days=weekday_offset)
            events.append((d, smart_title(line)))
    return events

# -------------------------- exports ------------------------------------------

def create_ics(events: List[Tuple[date, str]]) -> bytes:
    cal = Calendar()
    for d, title in events:
        ev = Event()
        ev.name = title
        ev.begin = d.strftime("%Y-%m-%d")
        cal.events.add(ev)
    return cal.serialize().encode()


def pdf_bytes(events: List[Tuple[date, str]]) -> io.BytesIO:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Course Calendar", ln=True, align="C")
    pdf.ln(4)
    for d, title in events:
        line = f"{d.isoformat()} – {title}"
        safe = line.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 8, safe)
    return io.BytesIO(pdf.output(dest="S").encode("latin-1"))

# -------------------------- Streamlit UI -------------------------------------

st.title("📘 Syllabus → Smart Calendar (auto‑labels)")

syllabus_file = st.file_uploader("📎 Upload syllabus (PDF)", type="pdf")
col1, col2 = st.columns(2)
with col1:
    sem_start = st.date_input("Semester start", value=dt.date.today())
with col2:
    sem_end = st.date_input("Semester end", value=dt.date.today())

if syllabus_file and sem_start and sem_end:
    raw_text = fitz.open(stream=syllabus_file.read(), filetype="pdf")
    raw_text = "\n".join(p.get_text() for p in raw_text)

    abs_events = extract_absolute_dates(raw_text)
    week_events = extract_week_events(raw_text, sem_start, WEEKDAY_OFFSET)

    # keep only dates within term
    all_events = [(d, t) for d, t in abs_events + week_events if sem_start <= d <= sem_end]

    if not all_events:
        st.warning("❌ No events detected in that date range.")
        st.stop()

    # DataFrame for display / export
    df = pd.DataFrame({"Date": [d.isoformat() for d, _ in all_events],
                       "Event Description": [t for _, t in all_events]}).drop_duplicates().sort_values("Date")

    st.subheader("🗓 Interactive calendar")
    with st.expander("Show table", False):
        st.dataframe(df, height=250)

    # FullCalendar payload
    fc_events = [{"title": row["Event Description"],
                  "start": row["Date"],
                  "description": row["Event Description"]} for _, row in df.iterrows()]

    cal_options = {
        "initialView": "dayGridMonth",
        "height": "auto",
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,listMonth",
        },
        "eventClick": """
            function(info){alert(info.event.extendedProps.description);}""",
    }
    _ = calendar(events=fc_events, options=cal_options, key="fc")

    st.markdown("----")
    colA, colB = st.columns(2)
    with colA:
        ics_data = create_ics(all_events)
        st.download_button("📆 Download .ics", ics_data, file_name="course_calendar.ics", mime="text/calendar")
    with colB:
        pdf_buf = pdf_bytes(all_events)
        st.download_button("🖨 Download PDF list", pdf_buf, file_name="course_calendar.pdf", mime="application/pdf")
