"""
Stage classifier. A direct port of the RULES table from gmail-sync.gs, kept
deliberately in the same shape so tuning knowledge transfers between the two.

Order matters: the first group that matches wins. Rejections are tested before
interviews because rejection emails routinely open with "thank you for
interviewing with us, however...".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Stage rules
# --------------------------------------------------------------------------- #

RULES: list[dict] = [
    {
        "stage": "Offer",
        "strong": [
            # Deliberately NOT "your offer": it is ordinary marketing English
            # ("your offer expires soon") and Offer is a terminal stage, so a
            # false positive here silently closes a live application.
            "pleased to offer", "delighted to offer", "happy to offer",
            "we would like to offer", "offer of employment", "offer letter",
            "formal offer",
        ],
        "weak": ["congratulations", "welcome to the team"],
    },
    {
        "stage": "Rejected/Withdrawn",
        "strong": [
            "regret to inform", "not been successful", "unsuccessful on this occasion",
            "not be progressing", "not progressing your application",
            "will not be moving forward", "decided not to move forward",
            "decided not to proceed", "not selected", "unable to offer you",
            "other candidates", "no longer under consideration",
            "application was not successful",
            # "decided not to progress your application" - the infinitive form
            # is common and matched none of the above, so real rejections were
            # landing on whatever stage the rest of the mail mentioned.
            "decided not to progress", "not to progress your application",
            "unable to progress", "will not be progressing",
            "not be taking your application further",
            "not moving forward with your application",
        ],
        "weak": ["unfortunately", "we are sorry"],
    },
    {
        "stage": "Interview",
        "strong": [
            "invite you to interview", "invitation to interview",
            "schedule your interview", "interview invitation", "assessment centre",
            "assessment center", "superday", "final round", "meet the team",
            "book a time to speak", "schedule a call with",
        ],
        "weak": ["interview", "next round", "speak with our team"],
    },
    {
        "stage": "OA/Assessment",
        "strong": [
            "online assessment", "coding challenge", "coding test",
            "technical assessment", "hackerrank", "codility", "codesignal",
            "hirevue", "shl ", "cut-e", "cappfinity", "numerical reasoning",
            "situational judgement", "psychometric", "aptitude test",
            "video interview", "take-home", "take home task",
        ],
        "weak": ["assessment", "complete the test"],
    },
    {
        "stage": "Applied",
        "strong": [
            "we have received your application", "application received",
            "thank you for applying", "thanks for applying",
            "your application has been submitted", "application confirmation",
        ],
        "weak": ["thank you for your interest"],
    },
]

# Applicant-tracking-system senders: the domain says nothing about the company,
# so company matching must fall back to the display name or subject.
ATS_DOMAINS: list[str] = [
    "greenhouse.io", "myworkday.com", "workday.com", "lever.co",
    "smartrecruiters.com", "icims.com", "taleo.net", "successfactors.com",
    "avature.net", "ashbyhq.com", "jobvite.com", "workable.com",
    "teamtailor.com", "eightfold.ai", "brassring.com", "oraclecloud.com",
    "gr.hs-sites.com", "hire.lever.co", "us.greenhouse-mail.io",
    "tal.net", "csod.com", "oleeo.com", "tribepad.com",
    "oracle.com",
]

# ATS hosts that give each employer its own subdomain, so the leftmost label
# names the company: morganstanley.tal.net, acme.wd3.myworkday.com. Without
# this the registrable domain wins and every Morgan Stanley mail reads as
# being from a company called "Tal".
ATS_TENANT_HOSTS: list[str] = [
    "tal.net", "myworkday.com", "icims.com", "avature.net", "csod.com",
    "taleo.net", "oleeo.com", "tribepad.com",
]


def ats_tenant(domain: str) -> str:
    """The employer's label from a per-tenant ATS host, or "" if not one."""
    domain = (domain or "").lower()
    for host in ATS_TENANT_HOSTS:
        if domain.endswith("." + host):
            label = domain[: -(len(host) + 1)].split(".")[0]
            # Skip generic routing labels that name no one.
            if label and label not in {"mail", "email", "www", "no-reply",
                                       "noreply", "notifications", "smtp"}:
                return label
    return ""

# Job boards, aggregators and newsletters. These talk about roles constantly,
# so they sail through RELEVANCE and then trip the weak rules, but they are
# never the counterparty to an application - they advertise openings rather
# than respond to you. On a real inbox they were over half of all matches.
#
# Note hackerrankmail.com (product marketing) is here while
# hackerrankforwork.com (the actual assessment invitations) is not. Match on
# the specific sending domain, never the brand.
NOISE_DOMAINS: list[str] = [
    "targetjobs.co.uk", "e.targetjobs.co.uk",
    "newsletters.ft.com",
    "theforage.com",
    "brightnetwork.co.uk",
    "simplify.jobs",
    "leetcode.com",
    "hackerrankmail.com",
    "glassdoor.com", "indeed.com", "ziprecruiter.com",
    # A visa application is an application, and says so in those words.
    "usvisa-info.com", "ustraveldocs.com",
    # Student-discount verification: "Congratulations, your Student status
    # is confirmed" reads as an offer to the weak rules.
    "sheerid.com", "studentbeans.com", "myunidays.com", "unidays.com",
]

# A message must look recruiting-ish at all before we bother classifying it.
RELEVANCE: list[str] = [
    "application", "applied", "candidate", "recruit", "interview", "assessment",
    "internship", "graduate programme", "graduate program", "role", "vacancy",
    "position", "hiring", "talent", "offer", "placement", "studentship",
]


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Opportunity type
# --------------------------------------------------------------------------- #
# A separate axis from stage: what kind of thing was applied for. Ordered most
# specific first, because recruiting mail happily mentions several at once
# ("our graduate programmes and summer internships") and the first hit wins.
KIND_RULES: list[tuple[str, list[str]]] = [
    ("Hackathon", [
        "hackathon", "hack day", "datathon", "chessathon", "codeathon",
        "case competition", "coding competition", "programming contest",
    ]),
    ("Industrial Placement", [
        "industrial placement", "placement year", "year in industry",
        "sandwich placement", "sandwich year", "12 month placement",
        "12-month placement", "industrial year", "placement scheme",
    ]),
    ("Insight/Spring Week", [
        "spring week", "spring insight", "spring programme", "spring program",
        "insight programme", "insight program", "insight day", "insight week",
        "discovery day", "first year programme", "first-year programme",
        "open day",
    ]),
    ("Internship", [
        "internship", "intern", "summer analyst", "summer programme",
        "summer program", "summer associate", "vacation scheme",
        "industrial trainee",
    ]),
    ("Graduate Scheme", [
        "graduate programme", "graduate program", "graduate scheme",
        "graduate analyst", "graduate trainee", "new grad", "graduate role",
    ]),
]

# Whole-word matching, so "intern" cannot fire on "internal" or "international".
_KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (kind, re.compile("|".join(rf"\b{re.escape(p)}\b" for p in phrases), re.I))
    for kind, phrases in KIND_RULES
]


def detect_kind(hay: str) -> str:
    """Best guess at the opportunity type, or "" when nothing indicates one."""
    for kind, pattern in _KIND_PATTERNS:
        if pattern.search(hay):
            return kind
    return ""


# --------------------------------------------------------------------------- #
# Role and category
# --------------------------------------------------------------------------- #
# Derived from how real recruiting mail actually phrases it. Ordered most
# specific first; each needs a trailing anchor ("... role", "... at Company")
# because an unanchored "for the X" swallows ordinary prose.
ROLE_PATTERNS: list[re.Pattern[str]] = [re.compile(p, re.I) for p in (
    r"\brole of\s+(.{3,70}?)\s*(?:\band\b|[.,;|]|$)",
    r"\bfor the role\s+(.{3,70}?)\s*[,.;|]",
    r"\bapplication for\s+(?:the\s+)?(.{3,70}?)\s+role\b",
    r"\bfor the\s+(.{3,70}?)\s+role\b",
    r"'s\s+(.{3,70}?)\s+role\b",
    r"\bapplying (?:to|for) the\s+(.{3,70}?)\s+at\s+\w",
    r"\bfor the job\s+(.{3,70}?)\s*(?:[.,;|]|$)",
    r"\bapplying (?:to|for)\s+((?:\d{4}\s+)?[^.,;|]{3,60}?\s+Programme(?:\s*\([^)]{1,30}\))?)",
    r"\beligibility requirements for the\s+(.{3,70}?)\s*(?:[.,;|]|$)",
    # "Application to Internship Programme 2027: Software Engineer (Frontend)"
    # - the scheme is named first and the actual job follows a colon.
    # Stop before the trailing clause: the subject continues past the title
    # ("...: Software Engineer (Frontend) position confirmed").
    r"(?:programme|program|scheme|internship)\s*\d{0,4}\s*:\s*(.{3,60}?)"
    r"\s*(?:\bposition\b|\bconfirmed\b|\brole\b|\bapplication\b|[.|]|$)",
    # "Revolut / Product Data Analyst Invitation" - ATS mail often separates
    # employer and role with a slash.
    r"/\s*(.{3,50}?)\s*(?:Invitation|Assessment|Interview|Application|\||$)",
)]

# An extracted span must contain one of these to be a job title at all. Without
# it, "for the duration of the assessments" and "for the same role" sail
# through. Note "application" is absent on purpose - it appears in prose
# constantly ("your application will be re-opened").
ROLE_WORDS: list[str] = [
    "intern", "internship", "placement", "programme", "program", "scheme",
    "graduate", "analyst", "engineer", "engineering", "developer", "scientist",
    "trader", "trading", "quant", "quantitative", "researcher", "research",
    "associate", "consultant", "manager", "technology", "software", "summer",
    "trainee", "apprentice", "student", "strategist", "actuarial",
]

# Prose giveaways: a real job title contains none of these.
_ROLE_PROSE = re.compile(
    r"\b(you|your|we|our|us|will|have|has|been|is|are|was|that|which|they|"
    r"this|these|those|please|thank|thanks|may|can|should|would)\b", re.I)

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Quant/Finance", [
        "quant", "quantitative", "trading", "trader", "markets", "investment",
        "investment banking", "finance", "financial", "securities", "hedge",
        "portfolio", "asset management", "actuarial", "equities", "derivatives",
        "sales and trading",
    ]),
    # Ahead of Research, or "Data Scientist" is caught by "scientist" and filed
    # as academic research. Ahead of Engineering, or "Data Engineer" is caught
    # by "engineer". Compound terms only - a bare "data" is far too broad.
    ("Data/ML", [
        "data scien", "data analy", "data engineer", "data strateg",
        "analytics", "machine learning", "deep learning",
        "artificial intelligence", "computer vision", "statistician",
        "bioinformatic",
    ]),
    # Ahead of Engineering for the same reason: "Mechanical Engineer" should
    # not land in the same bucket as a backend role.
    ("Hardware/Mechanical", [
        "mechanical", "electrical", "aerospace", "automotive", "hardware",
        "manufacturing", "powertrain", "chassis", "semiconductor", "electronic",
        "structural", "thermal", "civil engineer", "materials",
    ]),
    ("Consulting", [
        # Not a bare "strategy": "Data Strategy & Insights" is a data role.
        "consultant", "consulting", "advisory", "strategy consult",
        "management consult",
    ]),
    ("Product", [
        "product manage", "product owner", "product design", "user experience",
        "user research", "ux", "ui/ux",
    ]),
    ("Research", [
        "research", "scientist", "r&d", "phd", "laboratory",
    ]),
    # Broadest, so it runs last and only claims what nothing above wanted.
    ("Engineering", [
        "software", "engineer", "engineering", "developer", "swe", "backend",
        "front end", "frontend", "full stack", "devops", "infrastructure",
        "systems", "technology", "platform", "robotics", "security",
        "cloud", "network",
    ]),
]


def _clean_role(raw: str) -> str:
    role = " ".join(raw.split()).strip(" -–—:;,.|")
    role = re.sub(r"^(the|a|an|of|for)\s+", "", role, flags=re.I)
    # "Citadel's Software Engineer - Intern" -> the company already labels the
    # row, so the possessive is noise inside the job title.
    role = re.sub(r"^[A-Z][\w&.-]*'s\s+", "", role)
    return role


def extract_role(subject: str, text: str) -> str:
    """The job title if the mail states one plainly, else ""."""
    hay = f"{subject} || {text}"
    for pattern in ROLE_PATTERNS:
        for match in pattern.finditer(hay):
            role = _clean_role(match.group(1))
            if not 3 <= len(role) <= 70:
                continue
            if _ROLE_PROSE.search(role):
                continue
            if not any(w in role.lower() for w in ROLE_WORDS):
                continue
            return role
    return ""


def detect_category(role: str) -> str:
    """
    Sector, judged strictly from the role title, or "" if there isn't one.

    The title is what the category describes: a software engineering internship
    at a trading firm is Engineering, not Quant/Finance.

    Inferring this from the message body was tried and dropped. Boilerplate,
    disclaimers and signatures dominate the text, so two identical Optiver
    assessment invitations came out Engineering and Quant/Finance, and a
    hackathon thank-you came out Quant/Finance because it name-dropped a
    sponsor. A blank the user fills in once beats a label that contradicts
    itself between messages.
    """
    if not role:
        return ""
    lowered = role.lower()
    for category, words in CATEGORY_RULES:
        if any(re.search(rf"\b{re.escape(w)}", lowered) for w in words):
            return category
    return ""


@dataclass(frozen=True)
class Verdict:
    stage: str
    confidence: str  # "high" | "low"
    phrase: str


def _first_match(hay: str, phrases: list[str]) -> str | None:
    for p in phrases:
        if p in hay:
            return p
    return None


def is_relevant(hay: str) -> bool:
    """Cheap pre-filter: does this look like recruiting mail at all?"""
    return _first_match(hay, RELEVANCE) is not None


def gmail_relevance_clause() -> str:
    """
    RELEVANCE expressed as a Gmail OR-group, so the API stops shipping us mail
    the local filter would only discard. Fetching a message body is by far the
    most expensive thing the agent does, and on a normal inbox this is the
    difference between a few hundred fetches and a few dozen.

    Gmail stems its terms, so "recruit" here also matches "recruiting" and
    "recruitment" - slightly broader than the local check, which still runs
    afterwards and remains the authority.
    """
    terms = [f'"{t}"' if " " in t else t for t in RELEVANCE]
    return "{" + " ".join(terms) + "}"


def classify(hay: str) -> Verdict | None:
    """
    `hay` must already be lowercased subject + body.

    Every strong list is tried before any weak list, so a bare "unfortunately"
    never outranks an explicit "invitation to interview" further down the mail.
    """
    for rule in RULES:
        hit = _first_match(hay, rule["strong"])
        if hit:
            return Verdict(rule["stage"], "high", hit)

    for rule in RULES:
        hit = _first_match(hay, rule["weak"])
        if hit:
            return Verdict(rule["stage"], "low", hit)

    return None


def is_ats(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in ATS_DOMAINS)


def is_noise(domain: str) -> bool:
    """A job board or newsletter rather than someone you applied to."""
    return any(domain == d or domain.endswith("." + d) for d in NOISE_DOMAINS)


# A student's university sends a constant stream of mail about exams,
# accommodation and enrolment that uses the same vocabulary as recruiting:
# "assessment", "unfortunately", "congratulations". Left alone it manufactures
# applications to your own university.
ACADEMIC_HOSTS = (".ac.uk", ".edu", ".edu.au", ".ac.nz", ".ac.jp", ".k12.tr")
ACADEMIC_ADMIN = [
    "academic adjustment", "reasonable adjustment", "exam timetable",
    "summer assessment", "central assessment", "coursework", "module selection",
    "module registration", "enrolment", "enrollment", "tuition fee",
    "student finance", "accommodation", "graduation ceremony", "results day",
    "academic year", "semester", "term dates", "transcript", "study abroad",
]


def is_academic(domain: str) -> bool:
    domain = (domain or "").lower()
    return any(domain == h.lstrip(".") or domain.endswith(h) for h in ACADEMIC_HOSTS)


def academic_admin(hay: str) -> bool:
    """University administration rather than a recruiting pipeline."""
    return _first_match(hay, ACADEMIC_ADMIN) is not None


# "Thank You for Applying to Goldman Sachs" arrives from oracle.com, so the
# domain names the ATS vendor and the employer is only in the sentence.
_NAME = r"[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3}"

# Tried in order. The trailing "... at Dow Jones" wins over the sender name,
# because a shared ATS tenant is registered to the parent company: Dow Jones
# mail arrives from newscorp@myworkday.com, and the display name says News Corp.
_SUBJECT_COMPANY = [re.compile(p) for p in (
    rf"\bat\s+({_NAME})\s*$",
    # The keyword matches case-insensitively ("Applying" in a title-case
    # subject) but the captured name must stay capitalised, or it swallows
    # the rest of the sentence.
        # Deliberately not "for": you apply *to* a company but *for* a role, and
    # "your application for Technical Sales Engineering Intern" was filing the
    # job title as the employer.
    rf"(?:[Aa]pplying|[Aa]pplication|[Aa]pplied)\s+(?:to|with)\s+(?:the\s+)?({_NAME})",
)]

# Never report the tooling as the employer: "...assessment at HackerRank".
_LOOKS_LIKE_ROLE = re.compile(
    r"\b(intern|interns|internship|engineer|engineering|analyst|scientist|"
    r"developer|manager|consultant|trader|trading|placement|placements|"
    r"graduate|programme|program|scheme|associate|apprentice)\b", re.I)

_NOT_EMPLOYERS = {
    "hackerrank", "codility", "codesignal", "hirevue", "workday", "myworkday",
    "greenhouse", "lever", "smartrecruiters", "icims", "taleo", "oracle",
    "successfactors", "avature", "ashby", "workable", "modernhire", "oleeo",
}


def company_from_subject(subject: str, text: str = "") -> str:
    """
    Employer named in the mail, for messages sent through a vendor.

    The subject is tried first because it is terser and less likely to name
    some other company in passing. The body is a genuine fallback: assessment
    vendors send from their own domain with a generic display name, and say
    who they are testing for only in the opening line - "your application to
    the Mercedes-AMG PETRONAS Formula One Team for the ...".
    """
    for pattern in _SUBJECT_COMPANY:
        match = pattern.search(subject or "") or pattern.search((text or "")[:400])
        if not match:
            continue
        name = " ".join(match.group(1).split()).strip(" .,-")
        if not 2 <= len(name) <= 40:
            continue
        # "Your application to Summer 2026 Internship" captures the role, not
        # the employer. A lone season or article is never a company.
        if name.lower() in {"summer", "spring", "winter", "autumn", "fall",
                            "the", "our", "your", "a", "an", "this",
                            "internship", "graduate", "student"}:
            continue
        if name.lower().replace(" ", "") in _NOT_EMPLOYERS:
            continue
        if _LOOKS_LIKE_ROLE.search(name):
            continue
        return name
    return ""
