import json
from pathlib import Path

import pytest

from cutoff import branches as br
from cutoff import evaluate, gemma, regex_baseline
from cutoff.emails import load_folder
from cutoff.grounding import ground
from cutoff.rules import CHECK, CLOSED, ELIGIBLE, NOT_A_DRIVE, NOT_ELIGIBLE, Profile, decide
from cutoff.schema import Email, Extraction, Field

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "data" / "fixtures"
ME = Profile(cgpa=7.08, branch="CSBS", batch=2027, active_backlogs=0, twelfth_pct=87.2)


def mail(body, subject="Drive"):
    return Email(id="t", subject=subject, sender="", date="2026-10-22T10:00:00+05:30", body=body)


def xt(**fields):
    x = Extraction(email_id="t")
    for k, (v, ev) in fields.items():
        x.fields[k] = Field(v, ev)
    return x


# ---------- branches ----------
@pytest.mark.parametrize("allowed,expected", [
    (["CSE", "ISE", "CSBS"], "PASS"),
    (["Computer Science and Business Systems"], "PASS"),
    (["All branches"], "PASS"),
    (["All B.E./B.Tech branches"], "PASS"),
    (["CS and allied branches"], "PASS"),
    (["CS/IT and allied"], "PASS"),
    (["ECE", "EEE", "EIE"], "FAIL"),
    (["CSE (AIML)"], "FAIL"),          # AIML, not CSE, and not you
    (["CSE"], "FAIL"),                 # a bare CSE doesn't include CSBS unless you say so
    (["Circuit branches"], "UNKNOWN"),  # colleges disagree: CHECK, don't guess
    (["All circuit branches"], "UNKNOWN"),
    (["Quantum Basket Weaving"], "UNKNOWN"),
])
def test_branch_status(allowed, expected):
    assert br.branch_status(allowed, "CSBS")[0] == expected


def test_cse_includes_me_switch():
    assert br.branch_status(["CSE"], "CSBS", cse_includes_me=True)[0] == "PASS"


def test_allied_switch_off_means_check():
    assert br.branch_status(["CS and allied branches"], "CSBS", allied_includes_me=False)[0] == "UNKNOWN"


def test_canonical_set():
    assert br.canonical_set(["CSE/ISE", "AI&DS"]) == {"CSE", "ISE", "AIDS"}
    assert br.canonical_set(["ALL"]) == {"ALL"}
    assert br.canonical_set(["CS-ALLIED"]) == br.canonical_set(["CS & allied branches"])


# ---------- grounding ----------
def test_percentage_is_not_a_cgpa():
    e = mail("Candidates must have 60% throughout in 10th, 12th and B.E.")
    x = xt(min_cgpa=(6.0, "Candidates must have 60% throughout in 10th, 12th and B.E."))
    assert ground(x, e) == ["min_cgpa"]
    assert "percentage" in x.get("min_cgpa").reason


def test_percentage_value_in_cgpa_field():
    e = mail("Minimum 60% in 12th")
    x = xt(min_cgpa=(60.0, "Minimum 60% in 12th"))
    assert ground(x, e) == ["min_cgpa"]


def test_made_up_evidence_rejected():
    e = mail("Eligible branches: CSE, ISE\nApply soon.")
    x = xt(deadline=("2026-10-27T23:59", "Last date: 27th October 2026"))
    assert ground(x, e) == ["deadline"]
    assert "word-for-word" in x.get("deadline").reason


def test_good_fields_pass():
    body = ("Eligible branches: CSE, ISE, CSBS\nMinimum 7.5 CGPA with no standing backlogs\n"
            "Batch: 2027\nApply before 26/10/2026 6:00 PM")
    x = xt(min_cgpa=(7.5, "Minimum 7.5 CGPA with no standing backlogs"),
           max_active_backlogs=(0, "Minimum 7.5 CGPA with no standing backlogs"),
           branches=(["CSE", "ISE", "CSBS"], "Eligible branches: CSE, ISE, CSBS"),
           batch_years=([2027], "Batch: 2027"),
           deadline=("2026-10-26T18:00", "Apply before 26/10/2026 6:00 PM"))
    assert ground(x, mail(body)) == []


def test_wrong_day_rejected():
    e = mail("Last date to register: 27th October 2026")
    x = xt(deadline=("2026-10-28T23:59", "Last date to register: 27th October 2026"))
    assert ground(x, e) == ["deadline"]


def test_branch_not_in_evidence_rejected():
    e = mail("Branches: CSE, ISE")
    x = xt(branches=(["CSE", "ISE", "CSBS"], "Branches: CSE, ISE"))
    assert ground(x, e) == ["branches"]


def test_evidence_survives_formatting_noise():
    e = mail("**Eligibility** | CGPA  7 &amp; above".replace("&amp;", "&"))
    x = xt(min_cgpa=(7.0, "Eligibility | CGPA 7 & above"))
    assert ground(x, e) == []


# ---------- rules ----------
def test_unknown_never_means_not_eligible():
    x = xt(min_cgpa=(8.0, "made up"))
    ground(x, mail("CGPA cutoff to be announced"))
    assert decide(x, ME).verdict == CHECK


def test_cgpa_boundary():
    x = xt(min_cgpa=(7.08, "CGPA 7.08 and above"))
    ground(x, mail("CGPA 7.08 and above"))
    assert decide(x, ME).verdict == ELIGIBLE


def test_fail_beats_unknown():
    body = "Minimum 7.5 CGPA\nBranches: Circuit branches"
    x = xt(min_cgpa=(7.5, "Minimum 7.5 CGPA"), branches=(["Circuit branches"], "Branches: Circuit branches"))
    ground(x, mail(body))
    v = decide(x, ME)
    assert v.verdict == NOT_ELIGIBLE and "7.5" in v.reasons[0]


def test_closed_and_not_a_drive():
    x = xt(deadline=("2026-10-20T23:59", "Last date: 20th Oct 2026"))
    ground(x, mail("Last date: 20th Oct 2026"))
    from datetime import datetime
    assert decide(x, ME, now=datetime(2026, 10, 24)).verdict == CLOSED
    x.is_placement_drive = False
    assert decide(x, ME).verdict == NOT_A_DRIVE


def test_percentage_without_profile_marks_is_check():
    x = xt(min_percentage=(60.0, "60% throughout"))
    ground(x, mail("60% throughout"))
    assert decide(x, ME).verdict == CHECK


# ---------- emails ----------
def test_html_table_email_is_readable():
    emails = {e.id: e for e in load_folder(FIX / "emails")}
    t = emails["tamarind"]
    assert "CGPA 7 & above" in t.body and "Tamarind Health" in t.body
    assert t.date.startswith("2026-10-23")


# ---------- regex baseline + eval end to end ----------
def test_regex_eval_on_fixtures_misses_nothing():
    emails = load_folder(FIX / "emails")
    labels = evaluate.load_labels(FIX / "labels.csv")
    r = evaluate.evaluate(emails, labels, regex_baseline.extract, ME, "regex")
    assert r["emails"] == 10
    assert r["missed_eligible"] == 0
    md = evaluate.to_markdown(r)
    assert "| min_cgpa |" in md


# ---------- gemma flow with a fake Ollama ----------
BANYAN = ("Candidates must have 60% throughout in 10th, 12th and B.E.\nAll branches are eligible.\n"
          "Open to the 2027 batch.\nRegistration closes on 28 Oct 2026.")


def _resp(**fields):
    base = {"is_placement_drive": True}
    for name in ["company", "role", "min_cgpa", "min_percentage", "branches", "batch_years",
                 "max_active_backlogs", "deadline"]:
        v = fields.get(name, (None, None))
        base[name] = {"value": v[0], "evidence": v[1]}
    return {"message": {"content": json.dumps(base)}, "eval_count": 120, "prompt_eval_count": 600}


def test_gemma_retry_fixes_percentage_trap(tmp_path, monkeypatch):
    first = _resp(min_cgpa=(6.0, "Candidates must have 60% throughout in 10th, 12th and B.E."),
                  branches=(["All branches"], "All branches are eligible."),
                  batch_years=([2027], "Open to the 2027 batch."),
                  deadline=("2026-10-28T23:59", "Registration closes on 28 Oct 2026."))
    second = _resp(min_percentage=(60, "Candidates must have 60% throughout in 10th, 12th and B.E."),
                   branches=(["All branches"], "All branches are eligible."),
                   batch_years=([2027], "Open to the 2027 batch."),
                   deadline=("2026-10-28T23:59", "Registration closes on 28 Oct 2026."))
    calls = []

    def fake_chat(messages, model, think):
        calls.append(messages)
        return [first, second][len(calls) - 1]

    monkeypatch.setattr(gemma, "_chat", fake_chat)
    e = mail(BANYAN)
    x = gemma.extract(e, model="fake", cache_dir=tmp_path)
    assert x.meta["attempts"] == 2 and x.meta["rejected_first"] == ["min_cgpa"]
    assert "percentage" in calls[1][-1]["content"]          # the retry names the problem
    assert x.get("min_cgpa").value is None and not x.get("min_cgpa").unknown  # email has no CGPA cutoff
    assert decide(x, ME).verdict == CHECK                    # 60% + unknown 10th marks

    # Cached: no new calls the second time.
    x2 = gemma.extract(e, model="fake", cache_dir=tmp_path)
    assert len(calls) == 2 and x2.to_dict()["fields"] == x.to_dict()["fields"]


def test_gemma_gave_up_stays_unknown(tmp_path, monkeypatch):
    body = "CGPA: 7.5 and above (relaxable for exceptional candidates)"
    first = _resp(min_cgpa=(8.0, body))
    second = _resp()
    seq = iter([first, second])
    monkeypatch.setattr(gemma, "_chat", lambda m, model, think: next(seq))
    x = gemma.extract(mail(body), model="fake", cache_dir=tmp_path)
    # The email talks about CGPA, we just couldn't read it: CHECK, never "no restriction".
    assert x.get("min_cgpa").unknown
    assert decide(x, ME).verdict == CHECK


# ---------- real-inbox behaviours ----------
def test_no_criteria_from_portal_is_eligible_else_check():
    p = Profile(cgpa=7.08, branch="CSBS", batch=2027, prefiltered_senders=["joinsuperset.com"])
    x = xt(company=("IBM", None), deadline=("2026-10-24T11:00", "Deadline : Oct 24, 11:00 AM"))
    ground(x, mail("Applications are now being accepted for IBM's Job Profile\nDeadline : Oct 24, 11:00 AM"))
    assert decide(x, p, sender="Superset <notifications@joinsuperset.com>").verdict == ELIGIBLE
    assert decide(x, p, sender="Rajat <someone@college.edu>").verdict == CHECK


def test_forward_headers_and_contacts_are_scrubbed():
    from cutoff.emails import clean_body
    raw = ("---------- Forwarded message ---------\nFrom: BMS PLO <plo@college.edu>\nSubject: Fwd: Drive\n"
           "To: A Student <a.s23@college.edu>, B Student <\nb.s23@college.edu>\n\n*CGPA:* 7.5 and above\n"
           "Call 7337720057. Deadline 30/10/2026.")
    out = clean_body(raw)
    assert "a.s23" not in out and "A Student" not in out and "7337720057" not in out
    assert "*CGPA:* 7.5 and above" in out and "30/10/2026" in out


def test_apply_link_is_found_and_shown():
    from email.message import EmailMessage
    from cutoff.emails import from_message
    from cutoff import digest
    m = EmailMessage()
    m["Subject"] = "Open for application - IBM's Job Profile : Software Developer"
    m["From"] = "notifications@joinsuperset.com"
    m["Date"] = "Fri, 23 Oct 2026 10:00:00 +0530"
    m.set_content("view in browser")
    m.add_alternative('<p>Applications are now being accepted for <b>IBM</b>.</p><p>Deadline : Oct 24, 11:00 AM</p>'
                      '<a href="https://app.joinsuperset.com/students/jobprofiles?currentJobId=abc">Click to Apply</a>'
                      '<a href="https://linkedin.com/company/joinsuperset">LinkedIn</a>', subtype="html")
    e = from_message(m, "ibm")
    assert e.apply_url == "https://app.joinsuperset.com/students/jobprofiles?currentJobId=abc"
    x = xt(company=("IBM", None), deadline=("2026-10-24T11:00", "Deadline : Oct 24, 11:00 AM"))
    x.email_id = "ibm"
    ground(x, e)
    v = decide(x, ME, sender=e.sender)
    assert v.verdict == CHECK and "Superset" in v.reasons[0]
    out = digest.render([e], {"ibm": x}, [v])
    assert "currentJobId=abc" in out and "linkedin" not in out


def test_plain_text_form_link():
    from cutoff.emails import find_apply_url
    from email.message import EmailMessage
    body = "Interested students can apply through the form below:\nApplication form: https://tinyurl.com/Intrest-Form\nThanks"
    m = EmailMessage(); m.set_content(body)
    assert find_apply_url(m, body) == "https://tinyurl.com/Intrest-Form"


@pytest.mark.parametrize("allowed,cs_me,expected", [
    (["BTECH (Circuit Branches- CS, IT, ECE)"], True, "PASS"),    # KPMG: CS listed inside a circuit phrase
    (["BTECH (Circuit Branches- CS, IT, ECE)"], False, "UNKNOWN"),
    (["Non CS/IT"], True, "FAIL"),                                 # excludes CS, and CSBS counts as CS here
    (["Non CS/IT"], False, "UNKNOWN"),                             # otherwise never guess on "non-"
    (["AI-ML"], False, "FAIL"),                                    # one branch (AIML), not split, not you
    (["B.E.", "CSBS"], False, "PASS"),
    (["Electronics & Communication", "Computer Science & Engineering"], True, "FAIL"),  # Tejas: CSE != CS
    (["Mechanical", "Aeronautical and Aerospace"], True, "FAIL"),  # two branches, neither is you
    (["Biotechnology", "Biomedical Engineering"], True, "FAIL"),
    (["BE/B.Tech"], False, "PASS"),                                # degree only: no branch restriction
    (["Electronics and Communication"], False, "FAIL"),            # one branch, not split on "and"
    (["B.E / B.Tech / M.E / M/Tech - Electronics & Communication", "Computer Science & Engineering"], True, "FAIL"),
    (["B.E. / B.Tech in Biotechnology", "Biomedical Engineering"], True, "FAIL"),  # degree words are glue
    (["ME"], True, "FAIL"),                                        # bare ME is still Mechanical
])
def test_real_world_branch_phrases(allowed, cs_me, expected):
    assert br.branch_status(allowed, "CSBS", cs_includes_me=cs_me)[0] == expected


def test_cse_spelled_out_is_its_own_switch():
    assert br.code_for("Computer Science & Engineering") == "CSE"
    assert br.code_for("CS") == "CS"
    assert br.branch_status(["CSE"], "CSBS", cse_includes_me=True)[0] == "PASS"
    assert br.branch_status(["CSE"], "CSBS", cs_includes_me=True)[0] == "FAIL"


def test_percentage_scope_and_cgpa_alternative():
    from cutoff.rules import pct_scope
    p = Profile(cgpa=7.08, branch="CSBS", batch=2027, twelfth_pct=87.2)
    x = Extraction("ibm", True)
    x.fields["min_cgpa"] = Field(7.0, "MCA/MS -70%/7.0 CGPA")
    x.fields["min_percentage"] = Field(70, "MCA/MS -70%/7.0 CGPA")
    assert pct_scope("MCA/MS -70%/7.0 CGPA") == ["UG"]
    assert decide(x, p).verdict == ELIGIBLE                       # degree-only cutoff, met through CGPA
    x.fields["min_percentage"] = Field(60, "Min 60% throughout in 10th, 12th, Graduation")
    x.fields["min_cgpa"] = Field(None, None)
    v = decide(x, p)
    assert v.verdict == CHECK and "10th, UG" in v.reasons[0]     # 12th is known and fine


def test_portal_notice_borrows_criteria_from_same_company():
    from cutoff.rules import decide_all
    p = Profile(cgpa=7.08, branch="CSBS", batch=2027)
    fwd = Email("fwd", "Fwd: IBM campus drive", "Placement Office <a@college>", "2026-10-23", "...")
    ss = Email("ss", "Open for application - IBM's Job Profile", "Superset <notifications@joinsuperset.com>", "2026-10-23", "...")
    xf = Extraction("fwd", True); xf.fields["company"] = Field("IBM India", "IBM India")
    xf.fields["batch_years"] = Field([2027], "Graduation Year: 2027")
    xs = Extraction("ss", True); xs.fields["company"] = Field("IBM", "IBM")
    assert decide_all([(fwd, xf), (ss, xs)], p)[1].verdict == ELIGIBLE
    xf.fields["batch_years"] = Field([2026], "Graduation Year: 2026")
    v = decide_all([(fwd, xf), (ss, xs)], p)[1]
    assert v.verdict == CHECK and "says" in v.reasons[0]        # a borrowed 'no' is only ever a note


def test_criteria_in_attachment_blocks_a_clean_eligible():
    from cutoff.rules import criteria_elsewhere
    assert criteria_elsewhere("All the details regarding to *Company, Role requirements, Eligibility\n"
                              "criteria & Interview process* are mentioned in the above attachment.")
    assert criteria_elsewhere("The eligible programmes have been mentioned in each of the Job Descriptions shared")
    # criteria written out in the email, followed by our attachment list: not "elsewhere"
    assert not criteria_elsewhere("*Eligibility criteria*\nBTECH (CS, IT, ECE)\nMin 60% throughout\n\n"
                                  "[Attachments: image005.png, KPMG_Format.xlsx]")
    p = Profile(cgpa=7.08, branch="CSBS", batch=2027)
    x = Extraction("toyota", True)
    x.fields["batch_years"] = Field([2027], "2027 passing out")
    body = "hire 2027 passing out Graduate Engineers. Eligibility criteria are mentioned in the above attachment."
    assert decide(x, p).verdict == ELIGIBLE
    assert decide(x, p, body=body).verdict == CHECK


def test_today_deadline_and_fy_hiring_line():
    from cutoff.grounding import _check, mentions_topic
    e = Email("k", "KPMG reminder", "x", "2026-10-23T10:00:00+05:30", "register on the below link by today 7 PM without fail")
    assert _check("deadline", "2026-10-23T19:00", "register on the below link by today 7 PM without fail", e) is None
    assert _check("deadline", "2026-10-24T19:00", "register on the below link by today 7 PM without fail", e)
    assert not mentions_topic("batch_years", "FY27 Campus hiring")


def test_branch_list_scoring_normalisation():
    assert br.canonical_set(["Circuit Branches- CS, IT, ECE"]) == br.canonical_set(["CS", "IT", "ECE"])
    assert br.canonical_set(["Computer Science", "EEE", "and related fields"]) == \
        br.canonical_set(["Computer Science", "EEE", "related fields"])


def test_competition_subject_is_not_a_drive():
    p = Profile(cgpa=7.08, branch="CSBS", batch=2027)
    x = Extraction("v", True)
    assert decide(x, p, subject="REMINDER : Visionet Techthon 2026 | Submit Your Ideas!").verdict == NOT_A_DRIVE
    assert decide(x, p, subject="Open for application - IBM's Job Profile").verdict != NOT_A_DRIVE


def test_non_cs_it_does_not_list_it():
    assert br.branch_status(["Non CS/IT"], "IT")[0] == "FAIL"
