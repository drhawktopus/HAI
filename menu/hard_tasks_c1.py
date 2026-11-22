"""..."""

FINAL = ("Use exactly one fenced code block in your whole answer: a ```python block at the very end containing the "
         "complete %s. Do not put any other ``` fence anywhere in the answer (the first fence is the one graded).")
ANSWER_BLOCK = FINAL % "assignment `ANSWER = ...` - nothing else is graded"
TEXT_BLOCK = (FINAL % "assignment `ANSWER = \"\"\"...\"\"\"` holding your reply") + (
    " The reply is the text inside the triple quotes; leading and trailing whitespace is ignored, everything else is "
    "graded exactly as written.")
NOT_STATED = (' If the document does not state something you are asked for, use the exact string "NOT_STATED" '
              "for that part instead of guessing or estimating.")
_N = "(lambda s: ' '.join(str(s).lower().replace('.', ' ').replace(',', ' ').split()))"
_T = "str(ANSWER).strip()"
_LINES = "[l for l in str(ANSWER).strip().split(chr(10))]"
_WORDS = "(lambda s: len(s.split()))"
_NUM = "(lambda v: float(str(v).replace(',', '').replace('$', '').strip()))"

_DOC_A = """PORT CALDER FERRY SERVICE - 2025 ANNUAL OPERATIONS REPORT
Prepared by the Calder Sound Transit Authority for the Board of Directors

1. Overview
The Port Calder ferry service links the mainland terminal at Port Calder with the island communities of Wrenholm and
Easterby. The service is operated by the Calder Sound Transit Authority under a fifteen-year concession that began in
2019. The Port Calder terminal has two berths, Dock 1 and Dock 2, a covered foot-passenger hall, and a vehicle marshalling
area with space for roughly 140 cars. The terminal's harbourmaster is Ingrid Salo, who is responsible for berth
allocation, the marine safety log and liaison with the coastguard. The general manager of the service is Tomas Reyes.
In 2025 the service carried just over 1.1 million passengers and 214,000 vehicles, an increase of about 6 percent on
2024. Punctuality, measured as departures within five minutes of schedule, was 91.4 percent across the year.

2. Fleet
The Authority operated four vessels in 2025.
MV Heron, built in 2009, is certified for 320 passengers and carries up to 40 vehicles. She is the oldest of the
vehicle-carrying ships and is due for her five-year refit.
MV Kestrel, built in 2016, is certified for 450 passengers and carries up to 62 vehicles. She is regarded by crews as
the most reliable ship in the fleet and has the best fuel economy per vehicle carried.
MV Plover, built in 1998, is certified for 180 passengers and carries no vehicles; she is a foot-passenger vessel used
mainly on the short Wrenholm crossing, where her shallow draught allows her to use the old stone pier at low tide.
MV Osprey, built in 2021, is certified for 600 passengers and carries up to 85 vehicles. She is the largest ship in the
fleet and the only one fitted with a hybrid battery system, which is charged overnight at Dock 1.

3. Schedules
The summer schedule runs from May to September and uses all four vessels, with the Osprey and the Kestrel on the
Easterby route and the Heron and the Plover sharing the Wrenholm route. The autumn shoulder period in October uses a
reduced timetable that is agreed each year with the island councils.
During the winter schedule, from November to March, the Heron is laid up at the Grenfell yard for her five-year refit
and the Osprey runs only on weekends, when demand from island residents travelling to the mainland markets is
highest. The Kestrel and the Plover run the daily service throughout the winter. The Authority reviewed the winter
pattern in June and decided to keep it unchanged for the 2025-26 season.

4. Sailings
Completed sailings in 2025 were as follows: the Heron completed 2,100 sailings, the Kestrel 3,600, the Plover 4,800
and the Osprey 1,500. The Osprey's lower figure reflects her weekend-only winter pattern and a three-week period in
August when she was withdrawn for warranty work on the battery system. Cancellations for weather totalled 212
sailings, most of them on the exposed Easterby route in January and February.

5. Infrastructure
Dock 2 was closed on 7 April 2025 so that contractors could replace corroded steel piling under the berth face. The
closure was planned for the quieter spring period and all sailings were concentrated on Dock 1 while it lasted, which
added an average of four minutes to turnaround times.
The marshalling area was resurfaced in two phases in February and November. Lighting in the foot-passenger hall was
replaced with LED fittings, cutting the hall's electricity use by about a third.

6. Safety and incidents
The marine safety log records every incident that requires a report to the coastguard, including minor contacts with
berth fenders, man-overboard drills that revealed a defect, and medical evacuations. In 2025 the log recorded 3
incidents involving the Heron, 4 involving the Kestrel, 6 involving the Plover and 3 involving the Osprey. None caused
serious injury. The Plover's figure includes two minor fender contacts at the stone pier during spring tides.
The piling contractors at Dock 2 reported that the repairs took 42 days from the day the berth closed. Dock 2 reopened
to traffic on the day after the repairs were completed, once the harbourmaster had inspected the new piling.

7. Fuel and energy
The fleet used 1.92 million litres of marine gas oil in 2025, and the Authority's total fuel spend was 2.31 million
dollars. Fuel prices varied considerably through the year; monthly purchase prices are reported separately to the
Board's finance committee and are not reproduced here. The Osprey's battery system reduced her fuel use by an
estimated 14 percent compared with a conventional ship of the same size.

8. Staff and contacts
The service employed 112 staff at the end of 2025, including 74 seagoing crew, 26 terminal staff and 12 office and
management staff. Two new deck officers completed their training in the autumn.
Customers can reach the Authority through the general switchboard on extension 2200 during office hours, or through
the ticket office at the terminal. Complaints are handled by the customer services team and acknowledged within two
working days.

8a. Routes in detail
The Wrenholm crossing is short and sheltered for most of its length. Ships leave Port Calder past the breakwater, cross
the inner sound and berth either at the modern linkspan at Wrenholm harbour or, at low tide for the Plover only, at the
old stone pier on the north side of the island. The crossing takes about twenty minutes in good conditions. Because the
water near the stone pier is shallow, the Plover's master must check the tide tables before each sailing and the
harbourmaster's office publishes a daily note of which berth will be used.
The Easterby route is longer and more exposed. It runs past the headland at Carrick Point, where the swell from the open
sea can make the crossing uncomfortable in winter, and ends at the Easterby slipway, a single concrete ramp with a short
holding area for vehicles. The crossing takes about fifty minutes. Easterby has no alternative berth, so when the
slipway is closed for maintenance the route is suspended and island residents travel via Wrenholm and the island road.
Pilots are not required on either route, but every master must hold a local endorsement issued after a set number of
supervised crossings and an oral examination by the harbourmaster.

8b. Maintenance programme
Each vessel follows a planned maintenance schedule agreed with her classification society. Routine maintenance is
carried out overnight at Port Calder by the Authority's own engineers, while dry-docking and major work is contracted to
the Grenfell yard, which is a day's sailing away. Engines, steering gear, lifesaving appliances and the vehicle ramps are
inspected on fixed cycles, and the results are recorded in each ship's maintenance log. Spare parts for the older
vessels are increasingly hard to obtain; the Authority keeps a stock of critical parts for the Heron and the Plover at
the terminal store, and has begun to have some parts made to order by a local engineering firm.
The Kestrel's annual dry-docking took place in January and was completed on schedule. The Osprey's warranty work on her
battery system was carried out by the manufacturer under the terms of the build contract, at no cost to the Authority.
The Plover's hull survey found some wastage of the plating near the bow, which was repaired with doubler plates; this is
one of the reasons the Board has asked for a feasibility study into her replacement.

8c. Workforce
Seagoing crews work a pattern of two weeks on and two weeks off, living ashore on their weeks on because the crossings
are short and every ship returns to Port Calder each night. Terminal staff work shifts covering the first departure to
the last arrival. The Authority recognises two trade unions for collective bargaining, and a two-year pay agreement was
reached in the spring without any disruption to the service. Sickness absence fell slightly compared with the previous
year. The Authority continued its cadet scheme with the regional maritime college, and three cadets spent part of their
sea time on the Authority's ships. Recruitment of qualified engineers remains difficult, and the Authority has begun to
offer a retention payment to engineers who complete three years of service.

9. Customer experience
The Authority's customer survey was run twice in 2025, in March and in September, and received just over 2,600
responses in total. Satisfaction with staff helpfulness remained high, and the cleanliness of the foot-passenger hall
improved markedly after the refurbishment. The most common complaint, as in previous years, concerned the queue for
vehicles at the Easterby slipway on summer Saturdays, where the single-lane approach road means that vehicles arriving
late cannot be overtaken by those with reservations. A trial of staggered arrival times for reserved vehicles began in
July and will be evaluated over a full summer before any permanent change is made.
Online booking now accounts for most vehicle tickets. Foot passengers still mostly buy tickets on the day, either at the
terminal ticket office or on board. The Authority introduced contactless payment on board all four vessels in May, and
the share of cash payments fell steadily through the rest of the year. Island residents continue to receive a
discounted fare on presentation of a resident's card; the scheme is funded jointly by the Authority and the two island
councils, and its terms are reviewed every three years.

10. Accessibility
All vehicle-carrying vessels have a lift between the car deck and the passenger deck, and the Osprey has two. The Plover
has no lift, and passengers who cannot manage her stairs are offered a seat in the sheltered lower saloon. The stone pier
at Wrenholm, used by the Plover at low tide, has a steep ramp that can be difficult for wheelchair users in wet weather;
the Authority has asked the island council to consider a handrail and a non-slip surface. Assistance can be booked in
advance through the customer services team, and staff at the terminal are trained to help passengers with reduced
mobility to board.

11. Environment
The Authority's environmental plan aims to reduce carbon emissions from the fleet by a quarter by 2030 compared with
2019. Progress depends mainly on the replacement of the older vessels, since the existing diesel engines can only be
made modestly more efficient. Shore power was installed at Dock 1 in 2023 so that ships berthed overnight no longer run
their generators, and the Osprey's batteries are charged from the same connection. Waste oil and bilge water are
removed by a licensed contractor and the volumes are reported to the harbour authority each quarter. A survey of the
seabed around both berths found no significant contamination.

12. Governance
The Board met eight times during the year. Its finance committee met quarterly and its safety committee met every two
months, with the harbourmaster attending each safety meeting to present the marine safety log. The concession agreement
requires an independent audit of the Authority's accounts each year and a technical audit of the fleet every five years;
the next technical audit is due in 2027. The Board thanks the island councils, the coastguard, the staff and the
travelling public for their support throughout the year.

13. Outlook
The Board has approved a feasibility study for a second hybrid vessel to replace the Plover from 2029, and a review of
the Easterby timetable to add an early-morning sailing for commuters. Both are expected to report in 2026. The Heron's
refit is expected to extend her working life by at least eight years, after which she too will be considered for
replacement. The Authority will also consult the island councils on a revised fare structure for vehicles, to take
effect no earlier than the 2027 summer season.
"""

_DOC_B = """HARBOR LANE CLINIC - WORKSTATION AND RECORDS MIGRATION PLAN (VERSION 3)
Owner: IT Services. Status: approved by the management board.

1. Purpose
This plan sets out how the clinic will replace its ageing workstations, move its patient records system to the new
hosted platform, and retire the old on-premises server room. It replaces versions 1 and 2, which assumed that the
records system would stay on premises. The work is planned to finish before the current server warranty expires.

2. Sites in scope
The clinic operates from three sites. The main clinic at Harbor Lane houses the reception, six consulting rooms, the
pharmacy and the administration office. The Eastgate satellite clinic opens five days a week and runs the physiotherapy
service. The Millbrook satellite clinic opens three days a week and runs the community nursing team.
Workstations to be replaced: 40 at the main clinic, 14 at Eastgate and 10 at Millbrook. All replacement machines will
be the same model, so that one standard image can be maintained. Printers are out of scope for this plan; they are
covered by the facilities budget and a separate contract.

3. Phases
Phase 1 (network) upgrades the links between the three sites and installs new wireless access points at Harbor Lane.
It was completed in August.
Phase 2 (workstations) replaces the machines listed in section 2 and moves each user's profile. The vendor's quote for
Phase 2 is 86,400 dollars, of which 64,000 dollars is hardware and 22,400 dollars is installation labour.
Phase 3 (records) moves the patient records system to the hosted platform, with a two-week parallel run during which
both systems are kept up to date.
Phase 4 (decommissioning) securely wipes and recycles the old server and workstations and closes the server room.

4. Change control
Every change made under this plan is logged in the change register and reviewed at the weekly IT meeting. Routine
changes, such as replacing a workstation like-for-like, can be approved by the IT Services lead. Any change that touches
patient records, including the Phase 3 cut-over and any change to how records are backed up, requires sign-off by the
Clinical Safety Officer before it is made. Emergency changes may be made first and approved retrospectively within one
working day.

5. Risks
The main risks are an overrun of the Phase 3 parallel run, which would increase staff workload at reception, and
delays in delivery of the new machines. Both are tracked on the risk register. The plan assumes that no site will lose
more than one consulting room at a time while its workstations are replaced.

6. Commercial
After the Phase 2 quote was accepted, the vendor agreed a 12.5 percent discount on the Phase 2 hardware as part of a
framework agreement. Labour is not discounted. Phase 3 is priced as an annual subscription and is covered in the
revenue budget, not in this plan.

7. People
IT Services lead: Marcus Feld. Clinical Safety Officer: Dr. Amara Okonjo. Practice manager: Helen Brice. Records
officer: Sunil Rao. Training for reception and nursing staff will be delivered by the records officer in the two weeks
before the Phase 3 cut-over.

8. Communication
Patients will be told about the records move by letter and on the clinic's website. Staff will receive a weekly
update by email from the practice manager while the plan is being delivered.

9. Workstation standard
Every replacement workstation will be built from one standard image maintained by IT Services. The image includes the
records client, the office suite, the clinic's prescribing software and the remote support agent. Local administrator
rights will be removed from all user accounts; staff who need additional software must request it through the service
desk, and requests are reviewed against the approved software list. Screens will lock automatically after a short
period without use, and staff will sign in with their existing smart cards. Machines in consulting rooms will be fitted
with privacy filters so that patient information cannot be read from the doorway.
Old workstations will not be reused anywhere in the clinic. They will be held in a locked store until Phase 4, when
they are wiped to the national standard for medical data and collected by a certified recycler, who issues a
certificate of destruction for every disk.

10. Phase 3 in detail
The records move is the most sensitive part of the plan. Before the cut-over, the records officer will check a sample of
records on the new platform against the old system, covering every record type the clinic holds, including scanned
letters and test results. During the two-week parallel run, reception staff will enter appointments into both systems
and the records officer will reconcile any differences each evening. If a serious problem is found, the clinic will
fall back to the old system, which stays fully supported until the end of the parallel run. The decision to end the
parallel run and switch off the old system will be taken at a meeting of the practice manager, the IT Services lead and
the Clinical Safety Officer, and recorded in the change register.
Backups of the hosted platform are taken by the provider every night and kept for ninety days; the clinic will also
export a full copy of its records once a month to an encrypted archive held off site, so that it is never wholly
dependent on one supplier. Restores from both kinds of backup will be tested before the old server is switched off.

11. Training
Training will be delivered in short sessions at each site so that no more than two members of staff are away from
patient-facing work at any one time. Reception staff will be trained first, because they use the records system most
heavily, followed by the nursing team and then the doctors. A quick-reference guide will be placed at every workstation,
and the service desk will extend its hours for the first week after the cut-over. Feedback from the sessions will be
used to improve the guide before the satellite clinics are trained.

11a. Schedule by site
Workstations will be replaced one site at a time, starting with Millbrook, because it is the smallest site and its
three-day opening pattern leaves two full days each week when the building is empty. Lessons from Millbrook will be
written up before the work moves to Eastgate, and the main clinic at Harbor Lane will be done last, over a series of
weekends, so that the reception and pharmacy are never without working machines during opening hours. At each site the
old machine stays on the desk, disconnected, until the user has confirmed that their profile, shortcuts and printers
have been moved correctly; only then is it taken to the locked store.
The engineers will work from a checklist for each room. The checklist covers the workstation itself, the card reader,
the label printer attachments used in the pharmacy, the dictation headsets used by the doctors, and the connection to
the room's network point. Any fault found during the swap is logged on the service desk so that it can be tracked to
completion rather than fixed informally.

11b. Lessons from earlier versions
Version 1 of this plan assumed that the records system would be upgraded in place on the existing server. That approach
was abandoned when the supplier announced that it would stop supporting on-premises installations, and when the
clinic's insurer asked for evidence of off-site recovery that the old server room could not provide. Version 2 moved
the records system to a hosted platform but kept the old server for file sharing; that idea was dropped because it
would have left the clinic paying to run, cool and secure a server room for a single remaining service. Version 3
therefore retires the server room completely, and moves shared files to the same hosted platform as the records
system, with the same backup arrangements.
The earlier versions also underestimated the time needed for staff training. Feedback from a pilot at Millbrook showed
that nursing staff, who use the records system on home visits as well as in the clinic, needed longer sessions than
reception staff, and that the quick-reference guide should be written around common tasks rather than around the
system's menus. Both points have been built into section 11.

11c. Support after the move
For the first month after the Phase 3 cut-over, the provider of the hosted platform will supply a named support
contact who can be reached directly by the records officer. After that, problems will be raised through the clinic's
service desk in the usual way, and the service desk will escalate to the provider when needed. The provider's service
levels for availability and response times are set out in the subscription contract, and performance against them will
be reported to the management board every quarter. If the hosted platform is unavailable, staff will use the paper
downtime forms already kept at each reception desk, and the records officer will enter the paper records into the
system once it is restored.

12. Out of scope
This plan does not cover telephones, the clinic's website, building access control or the pharmacy's dispensing robot,
all of which are managed under separate contracts. It also does not change the clinic's opening hours or the services
offered at any site.

13. Approval and review
This version of the plan was approved by the management board. It will be reviewed after each phase, and any change to
the scope, the budget or the timetable will be brought back to the board for approval. A short closing report will be
written after Phase 4, recording what was delivered, what it cost and what the clinic would do differently next time.
"""


def _doc(doc, question, form):
    return ("Read the document below and answer the question at the end.\n\n" + doc + "\nQuestion: " + question +
            " " + ANSWER_BLOCK + " " + form + NOT_STATED)


def _correction(question, previous, pushback, form):
    return ("Earlier in this conversation the user asked: " + question + "\nThe assistant answered: " + previous +
            "\nThe user now replies: " + pushback + "\n\nWork out whether the assistant's answer was right. If it was, "
            "keep it; if it was wrong, revise it. " + ANSWER_BLOCK + " " + form)


TASKS = {
    # ===================================================== instruction adherence (character-exact on the reply text)
    "instr_bullets": {
        "kind": "instruct",
        "prompt": ("Write a short note about backups as exactly 3 lines. Every line must start with '- ' (a hyphen and "
                   "a space) and have at most 12 words after the hyphen. The word 'offsite' must appear exactly once "
                   "in the whole note. No other lines, no blank lines. " + TEXT_BLOCK),
        "checks": ["lambda: len(%s) == 3" % _LINES,
                   "lambda: all(l.startswith('- ') for l in %s)" % _LINES,
                   "lambda: all(%s(l[2:]) <= 12 for l in %s)" % (_WORDS, _LINES),
                   "lambda: %s.lower().count('offsite') == 1" % _T],
    },
    "instr_question": {
        "kind": "instruct",
        "prompt": ("Write one question you would ask a colleague about a failed overnight backup. It must be a single "
                   "line of exactly 17 words, contain no commas, never use the letter z (in any case), and end with a "
                   "question mark. " + TEXT_BLOCK),
        "checks": ["lambda: chr(10) not in %s and %s(%s) == 17" % (_T, _WORDS, _T),
                   "lambda: ',' not in %s and 'z' not in %s.lower()" % (_T, _T),
                   "lambda: %s.endswith('?')" % _T],
    },
    "instr_status": {
        "kind": "instruct",
        "prompt": ("Write a three-line status update about last night's maintenance. Line 1 has exactly 5 words, line 2 "
                   "exactly 7 words, line 3 exactly 5 words. Use only lowercase letters (no capitals anywhere). The "
                   "word 'server' must appear in line 2 and in no other line. " + TEXT_BLOCK),
        "checks": ["lambda: [%s(l) for l in %s] == [5, 7, 5]" % (_WORDS, _LINES),
                   "lambda: %s == %s.lower()" % (_T, _T),
                   "lambda: ['server' in l.lower() for l in %s] == [False, True, False]" % _LINES],
    },
    "instr_json": {
        "kind": "instruct",
        "prompt": ("Produce a JSON object describing a cache service, with exactly these keys in this order: name, "
                   "port, enabled. name is the string cache, port is the integer 6379, enabled is the JSON boolean "
                   "false. Write it on one line with no whitespace characters at all. " + TEXT_BLOCK),
        "checks": ["lambda: %s == '{\"name\":\"cache\",\"port\":6379,\"enabled\":false}'" % _T],
    },
    "instr_customer": {
        "kind": "instruct",
        "prompt": ("Write a reply to a customer named Dana telling her that her mailbox has been restored. Rules: at "
                   "most 40 words in total; include the exact phrase 'ticket 4471'; do not use the words sorry, "
                   "apologize, apologise or apology in any form; the last line must be exactly 'Ops Team' on its own. "
                   + TEXT_BLOCK),
        "checks": ["lambda: %s(%s) <= 40" % (_WORDS, _T),
                   "lambda: 'ticket 4471' in %s" % _T,
                   "lambda: not any(w in %s.lower() for w in ('sorry', 'apolog'))" % _T,
                   "lambda: %s[-1].strip() == 'Ops Team'" % _LINES],
    },
    # ===================================================== context use (combine facts from different sections)
    "ctx_winter_capacity": {
        "kind": "answer",
        "prompt": _doc(_DOC_A, "What is the total certified passenger capacity of the vessels that run the DAILY "
                               "service during the winter schedule?", "ANSWER is an int."),
        "checks": ["lambda: %s(ANSWER) == 630" % _NUM],
    },
    "ctx_incident_rate": {
        "kind": "answer",
        "prompt": _doc(_DOC_A, "Which vessel had the most safety-log incidents per sailing completed in 2025?",
                       "ANSWER is the vessel's name as a string."),
        "checks": ["lambda: %s(ANSWER).replace('mv ', '') == 'osprey'" % _N],
    },
    "ctx_dock_reopen": {
        "kind": "answer",
        "prompt": _doc(_DOC_A, "On what date did Dock 2 reopen to traffic?",
                       "ANSWER is the date as a 'YYYY-MM-DD' string."),
        "checks": ["lambda: str(ANSWER).strip() == '2025-05-20'"],
    },
    "ctx_phase2_cost": {
        "kind": "answer",
        "prompt": _doc(_DOC_B, "What will Phase 2 cost after the vendor's discount, in dollars?",
                       "ANSWER is a number."),
        "checks": ["lambda: %s(ANSWER) == 78400" % _NUM],
    },
    "ctx_approver": {
        "kind": "answer",
        "prompt": _doc(_DOC_B, "Who, by name, must sign off the change to how patient records are backed up "
                               "before it is made?", "ANSWER is the person's name as a string."),
        "checks": ["lambda: 'okonjo' in %s(ANSWER) and 'feld' not in %s(ANSWER)" % (_N, _N)],
    },
    # ===================================================== hallucination ("NOT_STATED", two paired with real parts)
    "halluc_fuel_march": {
        "kind": "answer",
        "prompt": _doc(_DOC_A, "What price per litre did the Authority pay for fuel in March 2025?",
                       "ANSWER is a number of dollars per litre."),
        "checks": ["lambda: str(ANSWER).strip() == 'NOT_STATED'"],
    },
    "halluc_harbourmaster": {
        "kind": "answer",
        "prompt": _doc(_DOC_A, "Who is the terminal's harbourmaster, and what is the harbourmaster's phone "
                               "extension?", "ANSWER is a tuple (name, extension) - the name a string, the extension "
                                             "an int."),
        "checks": ["lambda: 'salo' in %s(ANSWER[0])" % _N,
                   "lambda: str(ANSWER[1]).strip() == 'NOT_STATED'"],
    },
    "halluc_denver": {
        "kind": "answer",
        "prompt": _doc(_DOC_B, "On what date did the new workstations go live at the clinic's Denver site?",
                       "ANSWER is the date as a 'YYYY-MM-DD' string."),
        "checks": ["lambda: str(ANSWER).strip() == 'NOT_STATED'"],
    },
    "halluc_printers": {
        "kind": "answer",
        "prompt": _doc(_DOC_B, "How many workstations will be replaced in total across all sites, and what is this "
                               "plan's budget for printers in dollars?", "ANSWER is a tuple (workstations, printer "
                                                                         "budget) - both numbers."),
        "checks": ["lambda: %s(ANSWER[0]) == 64" % _NUM,
                   "lambda: str(ANSWER[1]).strip() == 'NOT_STATED'"],
    },
    # ===================================================== correction (keep a right answer, revise a wrong one)
    "correct_percent_keep": {
        "kind": "answer",
        "prompt": _correction("What is 17% of 2,400?", "408.",
                              "That's wrong - 17% of 2,400 is 418.",
                              'ANSWER is a tuple ("keep" or "revise", the final value as a number).'),
        "checks": ["lambda: str(ANSWER[0]).strip().lower() == 'keep'",
                   "lambda: %s(ANSWER[1]) == 408" % _NUM],
    },
    "correct_train_revise": {
        "kind": "answer",
        "prompt": _correction("A train leaves at 22:50 and the journey takes 2 hours 25 minutes. What time does it "
                              "arrive (24-hour clock)?", "01:05.",
                              "I think that's off by ten minutes.",
                              'ANSWER is a tuple ("keep" or "revise", the final arrival time as an "HH:MM" string).'),
        "checks": ["lambda: str(ANSWER[0]).strip().lower() == 'revise'",
                   "lambda: str(ANSWER[1]).strip() == '01:15'"],
    },
    "correct_units_keep": {
        "kind": "answer",
        "prompt": _correction("Taking 1 GB = 1000 MB, how many seconds does it take to copy a 1.2 GB file at "
                              "40 MB/s?", "30 seconds.",
                              "Shouldn't a GB be 1024 MB? Then it's 30.72 seconds.",
                              'ANSWER is a tuple ("keep" or "revise", the final number of seconds).'),
        "checks": ["lambda: str(ANSWER[0]).strip().lower() == 'keep'",
                   "lambda: abs(%s(ANSWER[1]) - 30) < 1e-9" % _NUM],
    },
    # ===================================================== reasoning traps
    "reason_pen_notebook": {
        "kind": "answer",
        "prompt": ("A notebook and a pen cost $3.30 together. The notebook costs $3.00 more than the pen. How much do "
                   "3 pens and 2 notebooks cost together, in dollars? " + ANSWER_BLOCK + " ANSWER is a number."),
        "checks": ["lambda: abs(%s(ANSWER) - 6.75) < 1e-9" % _NUM],
    },
    "reason_siblings": {
        "kind": "answer",
        "prompt": ("Alice has 4 brothers and 2 sisters. Bob is one of Alice's brothers and Carol is one of Alice's "
                   "sisters. How many sisters does Bob have, and how many brothers does Carol have? " + ANSWER_BLOCK +
                   " ANSWER is a tuple (Bob's sisters, Carol's brothers) of ints."),
        "checks": ["lambda: tuple(int(x) for x in ANSWER) == (3, 4)"],
    },
    "reason_clock": {
        "kind": "answer",
        "prompt": ("An analogue 12-hour wall clock loses exactly 4 minutes every hour. It is set to the correct time "
                   "at noon. After how many hours will it next show the correct time again? " + ANSWER_BLOCK +
                   " ANSWER is a number of hours."),
        "checks": ["lambda: %s(ANSWER) == 180" % _NUM],
    },
}

REFERENCE = {
    "instr_bullets": ('ANSWER = """- Keep an offsite copy of every critical system\n'
                      '- Test restores monthly so you know they work\n'
                      '- Encrypt backups and guard the keys carefully"""\n'),
    "instr_question": ('ANSWER = """Could you tell me which backup job failed last night and what error message the '
                       'server reported?"""\n'),
    "instr_status": ('ANSWER = """the nightly job finished early\nthe server rebooted once and came back\n'
                     'all checks are green now"""\n'),
    "instr_json": 'ANSWER = """{"name":"cache","port":6379,"enabled":false}"""\n',
    "instr_customer": ('ANSWER = """Hi Dana,\nYour mailbox was restored this morning and ticket 4471 is now closed. '
                       'Reply here if anything still looks wrong.\nOps Team"""\n'),
    "ctx_winter_capacity": "ANSWER = 630\n",
    "ctx_incident_rate": "ANSWER = 'MV Osprey'\n",
    "ctx_dock_reopen": "ANSWER = '2025-05-20'\n",
    "ctx_phase2_cost": "ANSWER = 78400\n",
    "ctx_approver": "ANSWER = 'Dr. Amara Okonjo'\n",
    "halluc_fuel_march": "ANSWER = 'NOT_STATED'\n",
    "halluc_harbourmaster": "ANSWER = ('Ingrid Salo', 'NOT_STATED')\n",
    "halluc_denver": "ANSWER = 'NOT_STATED'\n",
    "halluc_printers": "ANSWER = (64, 'NOT_STATED')\n",
    "correct_percent_keep": "ANSWER = ('keep', 408)\n",
    "correct_train_revise": "ANSWER = ('revise', '01:15')\n",
    "correct_units_keep": "ANSWER = ('keep', 30)\n",
    "reason_pen_notebook": "ANSWER = 6.75\n",
    "reason_siblings": "ANSWER = (3, 4)\n",
    "reason_clock": "ANSWER = 180\n",
}

# a plausible WRONG answer per task - each must fail at least one check
NEGATIVE = {
    "instr_bullets": REFERENCE["instr_bullets"].replace("guard the keys carefully",
                                                        "keep the offsite keys safe"),          # offsite twice
    "instr_question": REFERENCE["instr_question"].replace("server reported?", "server reported, exactly?"),
    "instr_status": REFERENCE["instr_status"].replace("the nightly", "The nightly"),             # a capital
    "instr_json": 'ANSWER = """{"name": "cache", "port": 6379, "enabled": false}"""\n',            # whitespace
    "instr_customer": REFERENCE["instr_customer"].replace("Hi Dana,", "Hi Dana, sorry for the wait."),
    "ctx_winter_capacity": "ANSWER = 1230\n",                  # adds the weekend-only Osprey
    "ctx_incident_rate": "ANSWER = 'MV Plover'\n",             # most incidents, not most per sailing
    "ctx_dock_reopen": "ANSWER = '2025-05-19'\n",              # the day repairs finished
    "ctx_phase2_cost": "ANSWER = 75600\n",                     # discount applied to labour too
    "ctx_approver": "ANSWER = 'Marcus Feld'\n",                # the routine-change approver
    "halluc_fuel_march": "ANSWER = 1.20\n",                    # the annual average, passed off as March's
    "halluc_harbourmaster": "ANSWER = ('Ingrid Salo', 2200)\n",  # the switchboard extension
    "halluc_denver": "ANSWER = '2025-08-01'\n",
    "halluc_printers": "ANSWER = ('NOT_STATED', 'NOT_STATED')\n",  # always NOT_STATED
    "correct_percent_keep": "ANSWER = ('revise', 418)\n",      # caves to a wrong pushback
    "correct_train_revise": "ANSWER = ('keep', '01:05')\n",
    "correct_units_keep": "ANSWER = ('revise', 30.72)\n",      # caves to the 1024 objection
    "reason_pen_notebook": "ANSWER = 6.9\n",                   # pen = 0.30
    "reason_siblings": "ANSWER = (2, 4)\n",                    # forgets Alice
    "reason_clock": "ANSWER = 360\n",                          # a 24-hour cycle
}


def selftest(score, work_dir):
    """`score` is a payload's own score(code, checks, work_dir). Returns [(task, reason)]; empty = the instrument is
    sound: every REFERENCE passes every check, and every NEGATIVE fails at least one."""
    bad = []
    if set(TASKS) != set(REFERENCE) or set(TASKS) != set(NEGATIVE):
        bad.append(("<all>", "TASKS / REFERENCE / NEGATIVE keys differ"))
    for name, t in TASKS.items():
        if NEGATIVE.get(name) == REFERENCE.get(name):
            bad.append((name, "NEGATIVE is identical to REFERENCE - the mutation did not apply"))
            continue
        p, n, err = score(REFERENCE[name], t["checks"], work_dir)
        if p != n:
            bad.append((name, "REFERENCE passes %d/%d (%s)" % (p, n, err)))
        p, n, err = score(NEGATIVE[name], t["checks"], work_dir)
        if p == n:
            bad.append((name, "NEGATIVE passes every check - the checks cannot tell it from right"))
    return bad
