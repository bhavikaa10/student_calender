
# ───────────────────────────── Imports ────────────────────────────────────
import streamlit as st              # UI framework
import fitz                         # PDF parsing (PyMuPDF)
from dateutil import parser         # Robust natural‑language date parser
import re                           # Regex for date + "Week N" detection
from ics import Calendar, Event     # Build exportable .ics file
from fpdf import FPDF               # Quick PDF list export
import pandas as pd                 # Tabular storage / sorting
import datetime as dt               # Date arithmetic for Week‑offsets
from streamlit_calendar import calendar  # FullCalendar → Streamlit wrapper

# ───────────────────────── Helper: DataFrame → FullCalendar ──────────────

def df_to_fullcalendar(df: pd.DataFrame) -> list[dict]:
    """Convert a two‑column DataFrame (Date, Event Description) to the small
    JSON schema FullCalendar expects.

    FullCalendar ignores any extra keys, so you can add `url`, `color`, etc.
    later if desired.
    """
    return [
        {
            "title": row["Event Description"][:80],  # 80‑char clip to keep pills tidy
            "start": row["Date"],                   # ISO‑date string "YYYY‑MM‑DD"
        }
        for _, row in df.iterrows()
    ]

# ═══════════════════════════ Text extraction ════════════════════════════

def extract_text_from_pdf(file) -> str:
    """Return *all* text from a PDF file‑like object using PyMuPDF."""
    doc = fitz.open(stream=file.read(), filetype="pdf")
    return "\n".join(page.get_text() for page in doc)

# ══════════════════════════ Date‑parsing helpers ═════════════════════════

ABS_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2}, \d{4})\b",
    re.I,
)  # matches 02/28/25, 2-28-2025, Feb 28, 2025 …

WEEK_RE = re.compile(r"\bweek\s*(\d{1,2})\b", re.I)

def iter_lines(text):
    for line in text.splitlines():
        yield line.strip()

def extract_dates(text):
    events = []
    seen = set()
    for line in iter_lines(text):
        for m in re.finditer(r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+\s+\d{1,2},\s*\d{4})\b', line):
            dt = parser.parse(m.group(0), fuzzy=True).date()
            if dt not in seen:
                seen.add(dt)
                events.append((dt, line))   # ← keep the whole line
    return events


def find_event_context(text: str, keyword: str, window: int = 80) -> str:
    """Return ±*window* chars around *keyword* so we have a title snippet."""
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return "Event"
    start = max(0, idx - window)
    end = idx + len(keyword) + window
    return text[start:end].replace("\n", " ").strip()


# Default: Week‑based events land on **Monday** of that week.
# Set this to 3 if your tutorials are every Thursday, etc.
WEEKDAY_OFFSET = 0  # 0=Mon, 1=Tue, 2=Wed …

def extract_week_based_events(text, semester_start, weekday_offset=0):
    events = []
    for line in iter_lines(text):
        for m in re.finditer(r'\bweek\s*(\d{1,2})\b', line, re.I):
            week = int(m.group(1))
            date = semester_start + datetime.timedelta(weeks=week-1, days=weekday_offset)
            events.append((date, line))
    return events

# ═══════════════════════════ .ics + PDF export ══════════════════════════

def create_ics_file(events: list[tuple[dt.date, str]], full_text: str) -> Calendar:
    """Build and return an `ics.Calendar` object from event tuples."""
    cal = Calendar()
    for date_obj, label in events:
        ev = Event()
        ev.name = find_event_context(full_text, label)
        ev.begin = date_obj.strftime("%Y-%m-%d")  # ICS prefers ISO strings
        cal.events.add(ev)
    return cal


def generate_calendar_pdf(events: list[tuple[dt.date, str]], full_text: str) -> None:
    """Save a very simple PDF list called *calendar.pdf* in the current dir."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 10, "Course Calendar", ln=True, align="C")

    for date_obj, label in events:
        context = find_event_context(full_text, label)
        # FPDF is Latin‑1 only → replace unsupported chars gracefully.
        line = f"{date_obj.strftime('%Y-%m-%d')}: {context}".encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 8, line)

    pdf.output("calendar.pdf")

# ═════════════════════════ Streamlit UI begins ══════════════════════════

st.title("📘 Syllabus → Smart Calendar")

st.write(
    "Upload a course syllabus PDF and get an interactive calendar plus optional\n"
    "downloads (ICS / PDF).  Absolute dates are detected automatically;\n"
    "'Week N' references are mapped from the semester start date you pick."
)

uploaded_file = st.file_uploader("📎 Upload syllabus (PDF)", type="pdf")
semester_start = st.date_input("📅 Semester Start Date")
semester_end = st.date_input("📅 Semester End Date")

if uploaded_file and semester_start and semester_end:
    # 1️⃣ Read PDF text
    raw_text = extract_text_from_pdf(uploaded_file)

    # 2️⃣ Parse dates (absolute + Week‑based)
    abs_dates = extract_dates(raw_text)
    week_dates = extract_week_based_events(raw_text, semester_start)
    all_events = abs_dates + week_dates

    if not all_events:
        st.warning("❌ No valid deadlines or week references found.")
        st.stop()

    # 3️⃣ Build DataFrame for display / export
    calendar_df = pd.DataFrame(
    {
        "Date": [d.isoformat() for d, line in all_events],
        "Event Description": [line for d, line in all_events],
    }
    ).drop_duplicates().sort_values("Date")


    # 4️⃣ Interactive calendar (FullCalendar)
    st.subheader("🗓️ Interactive Calendar")

    with st.expander("Show raw event table", expanded=False):
        st.dataframe(df, height=300)

    events_json = df_to_fullcalendar(df)

    # Basic FC options; tweak as needed
    cal_options = {
        "initialView": "dayGridMonth",
        "height": "auto",
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,listMonth",
        },
        "firstDay": 1,  # weeks start on Monday
    }

    # Render the calendar component
    _ = calendar(events=events_json, options=cal_options, key="course_calendar")

    # 5️⃣ Downloads
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        if st.button("📥 Create .ics file"):
            ics_cal = create_ics_file(all_events, raw_text)
            st.download_button(
                "⬇️ Download .ics",
                data=ics_cal.serialize().encode(),
                mime="text/calendar",
                file_name="course_calendar.ics",
            )
    with col_dl2:
        if st.button("🖨️ Create PDF list"):
            generate_calendar_pdf(all_events, raw_text)
            with open("calendar.pdf", "rb") as f:
                st.download_button(
                    "⬇️ Download PDF",
                    data=f,
                    mime="application/pdf",
                    file_name="course_calendar.pdf",
                )


        if st.button("📥 Download .ics Calendar File"):
            calendar = create_ics_file(all_events, text)
            with open("my_calendar.ics", "w") as f:
                f.writelines(calendar)
            with open("my_calendar.ics", "rb") as f:
                st.download_button("📆 Download .ics", f, file_name="course_calendar.ics")

        if st.button("📄 Export Calendar as PDF"):
            generate_calendar_pdf(all_events, text)
            with open("calendar.pdf", "rb") as f:
                st.download_button("🖨️ Download PDF", f, file_name="course_calendar.pdf")