DynaCalendar — Windows build
============================

Double-click DynaCalendar.exe. There is nothing to install: no Python, no Node,
no internet connection required.

--------------------------------------------------------------------------
First run
--------------------------------------------------------------------------
1. Double-click DynaCalendar.exe.
2. Windows may show "Windows protected your PC" — this is only because the
   executable is not code-signed. Click "More info" -> "Run anyway".
3. A black console window opens and your browser opens automatically at
   http://127.0.0.1:8000 (if not, open that address yourself).
4. To stop the app, close the console window.

The first start takes a few seconds because the single-file build unpacks
itself next to the executable; later starts are the same or faster.

--------------------------------------------------------------------------
Where the data lives
--------------------------------------------------------------------------
Next to the executable, in DynaCalendar-data\ :

  DynaCalendar-data\dynacalendar.db     the SQLite database
  DynaCalendar-data\materials\          uploaded originals and converted text
  DynaCalendar-data\samples\            the sample .ics and materials

Delete that folder to reset the app to an empty database. The folder must be
writable, so keep the app on the Desktop, in Downloads, or in Documents —
not under C:\Program Files.

--------------------------------------------------------------------------
Three-minute demo
--------------------------------------------------------------------------
1. Calendar page -> "Import .ics" -> DynaCalendar-data\samples\sample_calendar.ics
   Expect "Imported 10 events" and the exam banner for MATH2001 Final Exam.
2. Click "Create goal" on the banner — the goal's deadline becomes the exam date.
3. Inbox page -> select that goal -> drop the four .md files from
   DynaCalendar-data\samples\materials\ . Each is converted, tagged and costed.
4. Week page — the study jobs are placed in the free gaps around your classes,
   never overlapping an imported lecture.
5. Tick any job: it turns done and four revision cards appear at +1/+3/+7/+14
   days. The progress bar updates in the same operation.
6. Drag a job to another slot: it is pinned, and "Re-plan" keeps it there.
7. Ask the chat "what's due this week?" — it answers from the calendar even with
   no language model configured (the badge shows "LLM off").

--------------------------------------------------------------------------
Notes
--------------------------------------------------------------------------
- Single-user local app: it only listens on 127.0.0.1 and is not exposed to the
  network.
- The language model is optional. Every AI skill (tagging, clustering, chat) has
  a deterministic fallback, so the app is fully usable offline. To connect one,
  open the gear icon and set an OpenAI-compatible base URL and model.
- All scheduling arithmetic is done by a deterministic Python engine; the model
  only labels, de-duplicates and chats.
- Source: https://github.com/lunnua154-wq/DynamicCalendar
