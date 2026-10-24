"""Writes the synthetic test emails in data/fixtures/emails. All companies are made up.
These exist for tests and the demo. The real eval runs on your own labelled inbox."""
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone, timedelta
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
OUT = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "emails"
OUT.mkdir(parents=True, exist_ok=True)

def write(name, subject, day, body, html=None):
    m = EmailMessage()
    m["From"] = "Placement Cell <placements@college.example.edu>"
    m["To"] = "students-2027@college.example.edu"
    m["Subject"] = subject
    m["Date"] = format_datetime(datetime(2026, 10, day, 10, 30, tzinfo=IST))
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    (OUT / f"{name}.eml").write_bytes(bytes(m))

write("nimbus", "Campus Drive: Nimbus Analytics - Associate Software Engineer (2027 Batch)", 20, """Dear Students,

Nimbus Analytics is visiting campus for the role of Associate Software Engineer.

Eligibility Criteria:
- Branches: CSE, ISE, CSBS, AIML
- CGPA: 6.5 and above
- 2027 passouts only
- No active backlogs

CTC: 9 LPA
Last date to register: 27th October 2026, 11:59 PM
Register on the placement portal.

Regards,
Placement Cell""")

write("kestrel", "Kestrel Fintech | SDE Intern | Registration open", 20, """Hello all,

Kestrel Fintech is hiring SDE Interns (6 months, stipend Rs 50,000 per month).

Eligible branches: CSE, ISE, CSBS, ECE
Minimum 7.5 CGPA with no standing backlogs
Batch: 2027
Apply before 26/10/2026 6:00 PM

Placement Cell""")

write("banyan", "Banyan Retail Tech - Graduate Engineer Trainee", 21, """Dear Students,

Banyan Retail Tech is hiring Graduate Engineer Trainees.

Candidates must have 60% throughout in 10th, 12th and B.E.
All branches are eligible.
Open to the 2027 batch.
Registration closes on 28 Oct 2026.

Placement Cell""")

write("voltaire", "Voltaire Semiconductors: Design Verification Intern", 21, """Dear Students,

Voltaire Semiconductors is hiring Design Verification Interns.

Eligible Branches: ECE, EEE, EIE and Medical Electronics only
CGPA 7.0 and above
2027 batch
Last date: 29th October 2026

Placement Cell""")

write("orbit", "Placement Drive: Orbit Logistics - Software Developer", 22, """Dear Students,

Orbit Logistics invites applications for Software Developer.

All B.E./B.Tech branches eligible.
CGPA 7.0 and above.
2026 and 2027 batches.
Maximum 1 active backlog allowed.
Apply by 30th Oct, 5 PM.

Placement Cell""")

write("saffron", "Saffron Cloud - Cloud Support Associate", 22, """Hi all,

Saffron Cloud is hiring for the role of Cloud Support Associate.
The drive is open to CS and allied branches with a minimum CGPA of 7.
Eligible batch: 2027
Deadline: 31-10-2026

Placement Cell""")

write("indigo", "Indigo Motors - Embedded Software Trainee", 22, """Dear Students,

Indigo Motors is hiring Embedded Software Trainees.

Eligible: Circuit branches (B.E.)
CGPA 6.0 and above
2027 batch
Register by 1 Nov 2026.

Placement Cell""")

write("notice", "Dress code for tomorrow's pre-placement talk by Nimbus Analytics", 23, """Dear Students,

Tomorrow's pre-placement talk by Nimbus Analytics starts at 10 AM in the seminar hall.
Formal dress code is mandatory. Carry your ID card.

Placement Cell""")

write("peacock", "Peacock Payments - Backend Engineer", 20, """Dear Students,

Peacock Payments is hiring Backend Engineers.

Eligibility: 2026 passouts only
Branches: CSE / ISE / CSBS
CGPA 6 and above
Last date to apply: 25th October 2026

Placement Cell""")

write("tamarind", "Tamarind Health - Data Analyst Intern", 23,
      "Please view this email in an HTML-capable client.",
      html="""<html><body><p>Dear Students,</p>
<table border="1">
<tr><td>Company</td><td>Tamarind Health</td></tr>
<tr><td>Role</td><td>Data Analyst Intern</td></tr>
<tr><td>Eligibility</td><td>CGPA 7 &amp; above (no active backlogs)</td></tr>
<tr><td>Branches</td><td>CSE, ISE, CSBS, AI&amp;DS</td></tr>
<tr><td>Batch</td><td>2027</td></tr>
<tr><td>Register before</td><td>30/10/2026</td></tr>
</table><p>Placement Cell</p></body></html>""")
print("wrote", len(list(OUT.glob('*.eml'))), "fixtures to", OUT)
