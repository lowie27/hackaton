# CLAUDE.md

Tectonic Hackathon, Leuven, 30 September 2026. SD Worx track. Team of 2 to 4.
Public repo: judges read this file. Source for challenge and rules: the
Participants Guide (PDF, not in the repo). Page numbers below refer to it.

## Challenge

SD Worx, "Unlock the Knowledge Within. Find it. Understand it. Trust it." (p. 5)

- Question: "How might we turn fragmented organisational knowledge into a trusted shared resource?"
- Task: "Build a focused proof of concept that makes organisational knowledge easier to find, trust or share. Choose one meaningful problem. You do not need to solve everything."
- The questions behind it: "What is reliable? What is current? What applies in this context? Where are the gaps? Who has relevant expertise? Which answer should a person trust?"
- Inspiration areas, "not a checklist": Trust (is it relevant and reliable), Capture (knowledge beyond inboxes, documents and siloed teams), Detect (conflicting, duplicated, missing or outdated knowledge), Connect (find the right expertise when documents are not enough).
- Scope: "Focus on one role, one workflow, one knowledge source or one trust signal. Make the moment of doubt tangible." Move someone from "I found something" to "I understand why I can rely on it".
- "Do not start with prescribed technology." Do not "hide complexity behind a black box": make trust "visible, explainable and useful".
- Example friction (p. 5): an AI assistant returns three documents (one recently updated, one without an owner, one for another country) and a Teams thread contradicts them. A payroll consultant inherits a client portfolio with knowledge spread over documents, chats, workflows, applications and experts.
- Users: SD Worx employees working on HR, payroll and workforce services across Europe.
- TODO: whether SD Worx provides data, APIs or sample documents. The guide names none.

## How we are judged

Weights from the BuilderBase track dashboard. The guide (p. 11) lists the same four without weights, and calls the first "Creativity".

- Originality 30%: a clear angle on one moment of doubt, not a generic "chat with your docs" bot.
- Technical Ability 30% ("does it work?", p. 11): the demo flow has to run live, end to end.
- Fit to the case challenge 30%: tie every feature to a quoted challenge question above.
- Security 10%: connect the repo to Aikido early, run the AI Code Audit for a baseline, fix, mark resolved (p. 6 to 7). It checks business logic flaws, IDOR, authentication and authorization, so every data access needs a permission check.

## Deadline and submission

- Deadline: 22:30 CEST per the event page, 23:00 CEST per the BuilderBase dashboard. Treat 22:30 as hard and submit by 22:15.
- TODO: event schedule. The guide has none.
- Submit on BuilderBase (p. 11): demo video under 3 minutes, written description, repo link (`https://github.com/lowie27/hackaton`, already submitted), Aikido screenshots "before and after" (p. 7).
- Rules (p. 12): one project per team, no changes after the final submission, repo stays public until judging ends, include a short README (what it is, how to run it, what is unfinished), no passwords, keys or confidential data.

Last 45 minutes (from 21:30):

- [ ] Code frozen and pushed
- [ ] Repo public, README says what it is, how to run it, what is unfinished
- [ ] Aikido before and after screenshots taken
- [ ] Demo video recorded, under 3 minutes
- [ ] Description written
- [ ] Submitted on BuilderBase by 22:15

## Working rules for agents

- Finish one demo-able flow end to end before adding breadth.
- Say so when a request risks the deadline, and name what it would push out.
- Never commit secrets, tokens or the Google Cloud team code. Keep them in `.env`, which `.gitignore` excludes.
- Follow the `propose-commit` skill for every commit.

## Idea

TODO: not decided yet.

## Stack

TODO: not decided yet.
