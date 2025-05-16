import streamlit as st
import fitz  # PyMuPDF
from dateutil import parser
import re
from ics import Calendar, Event
from fpdf import FPDF
import pandas as pd
import datetime
from streamlit_calendar import calendar          # pip install streamlit-calendar

# helper functions:
from streamlit_calendar import calendar             # import the component

def df_to_fullcalendar(df):
    """Convert your Pandas DataFrame ➜ list[dict] for FullCalendar."""
    return [
        {
            "title": row["Event Description"][:80],        # trim long text
            "start": row["Date"],                          # YYYY-MM-DD
            # optional extras: "url", "backgroundColor", etc.
        }
        for _, row in df.iterrows()
    ]

# ------------ UTILITY FUNCTIONS ------------

def extract_text_from_pdf(file):
    doc = fitz.open(stream=file.read(), filetype="pdf")
    return "\n".join([page.get_text() for page in doc])

def extract_dates(text):
    date_strings = re.findall(r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2}, \d{4})\b', text)
    unique = set()
    events = []
    for ds in date_strings:
        try:
            dt = parser.parse(ds, fuzzy=True)
            if dt not in unique:
                unique.add(dt)
                events.append((dt, ds))
        except:
            pass
    return events

def find_event_context(text, keyword, window=80):
    index = text.lower().find(keyword.lower())
    if index == -1:
        return "Event"
    start = max(0, index - window)
    end = index + len(keyword) + window
    return text[start:end].replace("\n", " ").strip()

def extract_week_based_events(text, semester_start):
    week_events = []
    matches = re.finditer(r"\bweek\s*(\d{1,2})\b", text, re.IGNORECASE)
    for match in matches:
        week_num = int(match.group(1))
        event_date = semester_start + datetime.timedelta(days=(week_num - 1) * 7)
        keyword = f"week {week_num}"
        context = find_event_context(text, keyword)
        week_events.append((event_date, keyword))
    return week_events

def create_ics_file(events, full_text):
    c = Calendar()
    for date, label in events:
        e = Event()
        e.name = find_event_context(full_text, label)
        e.begin = date
        c.events.add(e)
    return c

def generate_calendar_pdf(events, full_text):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="📅 Course Calendar", ln=True, align="C")

    for date, label in events:
        context = find_event_context(full_text, label)
        event_text = f"{date.strftime('%Y-%m-%d')}: {context}"
        pdf.multi_cell(0, 10, event_text)

    pdf.output("calendar.pdf")

# ------------ STREAMLIT INTERFACE ------------

st.title("📘 Syllabus → Smart Calendar")
st.write("Upload your syllabus PDF and get a personalized calendar with key dates and deadlines.")

uploaded_file = st.file_uploader("📎 Upload syllabus (PDF)", type="pdf")
semester_start = st.date_input("📅 Semester Start Date")
semester_end = st.date_input("📅 Semester End Date")

if uploaded_file and semester_start and semester_end:
    text = extract_text_from_pdf(uploaded_file)

    # Absolute + Week-based Dates
    absolute_dates = extract_dates(text)
    week_dates = extract_week_based_events(text, semester_start)
    all_events = absolute_dates + week_dates

    if not all_events:
        st.warning("❌ No valid deadlines or week references found.")
    else:
        calendar_df = pd.DataFrame({
            "Date": [d.strftime("%Y-%m-%d") for d, label in all_events],
            "Event Description": [find_event_context(text, label) for d, label in all_events]
        }).sort_values("Date")

        # ──── NEW interactive calendar section ────
        st.subheader("🗓️ Interactive Calendar")

        # optional: keep the raw table in a collapsible expander
        with st.expander("Show raw event table", expanded=False):
            st.dataframe(calendar_df, height=300)

        events_json = df_to_fullcalendar(calendar_df)

        cal_options = {
            "initialView": "dayGridMonth",
            "height": "auto",
            "headerToolbar": {
                "left":   "today prev,next",
                "center": "title",
                "right":  "dayGridMonth,timeGridWeek,listMonth",
            },
        }

        _ = calendar(
                events  = events_json,
                options = cal_options,
                key     = "course_calendar"
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