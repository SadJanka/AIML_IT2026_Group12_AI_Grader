# AI Grader

Prototype for grading students' written work with AI and giving personalised feedback
(Fundamentals of AI and Machine Learning, group 12).

The teacher defines the criteria, the AI (NLP / LLM) grades each criterion and explains the mistakes, the teacher
approves or corrects the result, and the student receives feedback with suggested learning materials. All grades are
stored in a database and are used to calculate the final course grade.

## Running

**Windows:** double-click `run.bat`. The first run creates a virtual environment, installs the libraries and opens
http://localhost:8000. Requires Python 3.11+ ([python.org](https://www.python.org/downloads/)).

**Manually:**
```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # Linux/macOS: cp .env.example .env
uvicorn app.main:app --reload
```

**Docker:** `docker compose up --build`

The first start creates a demo course with four made-up submissions (one of them contains a manipulation attempt).
Click **Grade with AI** and open a submission.

## Features

- **Criteria with correct answers.** Each criterion can have a correct answer and each assignment a model answer or
  answer sheet. The AI uses them as a reference; the student does not have to match them word for word.
- **Tests:** every question is one criterion (question, points, correct answer). The demo course has an example.
- **Latvian / English:** the interface language switch is in the top right corner. Each assignment also has its own
  feedback language; the student's page always uses the assignment language.
- **Drag and drop:** files, whole folders and archives can be dropped on the assignment page. With
  **Grade with AI right away** checked, grading starts automatically.
  - Documents: `.docx .odt .rtf .pdf .txt .html` (old `.doc` files must be saved as .docx or .pdf).
  - Archives: `.zip .rar .7z .tar.gz`, including nested archives. RAR needs WinRAR, 7-Zip or the `tar.exe` built
    into Windows; otherwise extract it and drop the folder.
  - Emails: `.eml` (Thunderbird, Gmail "Download message") and `.msg` (Outlook). The attachments become the
    submission and the sender becomes the student; without attachments the email body is the submission.
  - Students are recognised by folder or file name (`Anna Bērziņa/essay.docx`, `S-001_essay.docx`, Moodle export
    `Anna Bērziņa_123_assignsubmission_file_/...`). Unknown students are created. Several files in one student
    folder form one submission. A shared top folder (`Submissions/`, `7.b klase/`) is ignored.
  - System files (`.DS_Store`, `~$...`, `Thumbs.db`) are skipped. Everything is extracted in memory.
- Files can also be dropped on text fields: instructions, model answer, correct answers, student list.
- **Grading progress:** percentage, time remaining and the live status of each submission.
- **Summary and export** (button on the assignment page): class average and distribution, what the class should
  revise (weakest criteria with the most common mistakes and materials), students who need attention, flagged
  submissions, **Approve all except flagged**, **Excel** export (Summary / Scores / Feedback), **print / PDF** with
  one page per student, and student links for copying. Course results can also be exported to Excel.
- **AI settings per assignment** (assignment page):
  - **Learn from teacher corrections** (on by default): approved submissions where the teacher changed the AI points
    are added to the prompt as examples (few-shot), so the AI grades closer to the teacher over time
    (`TEACHER_EXAMPLES`, default 3, texts shortened to 1,500 characters).
  - **Double check** (off by default): every submission is graded twice, the second time with a higher temperature
    (`CHECK_TEMPERATURE`, default 0.7). If any criterion differs by 1 point or more, or the total by 1 point or more,
    the submission is marked **AI unsure**. Takes twice the time and API quota. The **Accuracy** page shows whether the
    unsure submissions really have larger errors.
- **Similarity check** (button on the assignment page): a classic NLP method next to the LLM. Each submission becomes
  a TF-IDF vector of words and word pairs (diacritics folded, words from the task, criteria and model answer ignored)
  and every pair of students is compared with cosine similarity. The teacher opens a pair, sees the shared passages of
  five or more words highlighted and decides: **copying** or **not copying**. Copying flags both submissions; nothing
  is changed automatically. Note: the made-up test data is assembled from a few answer variants, so many of its pairs
  look similar.
- Flagged submissions (possible manipulation, AI unsure, copying) are never included in **Approve all**.
- **Dark mode:** sun and moon switch in the top bar. The choice is remembered; the default follows the system setting.
- Existing databases are upgraded automatically on start (new columns are added, no data is lost).

## Measuring AI accuracy (test data)

**Create a test course** on the home page creates a course with three assignments (easy test, medium essay, hard
calculation). The submissions are in `../test-data/` (25 made-up students per assignment). Every submission is built
from known answer variants, so its correct points are known in advance (`test-data/expected-points.csv`).
After grading, the **Accuracy** page shows the mean error, bias (too lenient / too strict), exact match rate,
correlation, error by category (for example "correct in other words", "minutes not converted", "no diacritics")
and how manipulation attempts were detected. The mock provider serves as a baseline.

Regenerate the data with `python -m app.benchmark ../test-data`.

## Choosing the AI model (`.env`)

| `LLM_PROVIDER` | What it is | When to use |
|---|---|---|
| `mock` | No AI, keyword matching | Demo, tests, interface development |
| `groq` | Free Groq API (default model `openai/gpt-oss-120b`) | Development and demos with **made-up** submissions |
| `ollama` | Local model on this computer | Data never leaves the computer (GDPR); free |
| `anthropic` | Claude API | Usually more accurate than small local models; paid |
| `openai` | OpenAI or a compatible API (LM Studio, Azure) | Alternative |

**Groq:** create a key at https://console.groq.com/keys, paste it after `GROQ_API_KEY=` in `.env` and restart the
application. The free tier also limits tokens per minute, so when many submissions are graded at once the application
waits and retries (`LLM_MAX_RETRIES`). Do not send real student work to a free cloud API; use `ollama` for that.

Ollama:
```bash
ollama pull gemma3:12b     # smaller computers: gemma3:4b
```
then set `LLM_PROVIDER=ollama` in `.env`. Compare several models on the same submissions on the **AI vs teacher**
page.

## How grading works

1. `grading/prompts.py` builds the system prompt and the task, model answer, criteria with correct answers and the
   student's text in a separate block `<student_work>...</student_work>`. Prompt version 1.2: every mistake needs a
   quote from the student's text, no mistakes at maximum points, no new facts.
2. The model replies with JSON: points, justification, mistakes and an improvement tip per criterion, plus a summary.
3. `grading/grader.py` validates the JSON and retries once if it is broken. Points are clamped to 0..maximum and
   rounded to 0.5; a criterion the AI skipped gets 0 points and a warning for the teacher.
4. `grading/injection.py` also looks for manipulation patterns in the text; suspicious submissions are flagged.
5. The result is saved as a **draft**. The student does not see it until the teacher approves it.
6. Learning materials are suggested by the system, not by the AI: topics below 70% of the points are matched with the
   materials the teacher added, so the AI cannot invent links.

## Security and data

- **Teacher control:** no grade reaches a student without approval.
- **Prompt injection:** separate data block that the student cannot close, heuristic detection, clamped points.
- **GDPR:** students are stored under pseudonym codes; only the text of the work is sent to the model; **delete data**
  removes a student with all submissions; with `ollama` the data never leaves the computer.
- **Fairness:** `LLM_TEMPERATURE=0`, the same criteria for everyone, an audit record (model, prompt version, raw
  response) for every submission and AI vs teacher agreement statistics.

## Structure

```
app/
  main.py              application
  config.py            settings from .env
  models.py            database tables
  seed.py              demo data
  benchmark.py         test data with known correct points
  i18n.py, i18n_lv.py  interface languages (source text in English, Latvian translations)
  grading/             prompts, model providers, response validation, injection detection,
                       teacher examples (few-shot), double check
  services/            files, students, results, feedback, accuracy, summary, Excel export,
                       similarity (TF-IDF + cosine)
  routers/pages.py     web interface
  routers/api.py       JSON API (http://localhost:8000/docs)
  templates/, static/  HTML, CSS, JavaScript
tests/                 pytest tests
```

Typical workflow: students email their work, the teacher downloads the attachments (or saves the emails), drops the
ZIP, folder or emails on the assignment page, the AI grades them, and the teacher reviews and approves.

## Tests

```bash
python -m pytest
```

The optional libraries in `requirements-optional.txt` (.7z archives, Outlook .msg) are installed separately; the
application works without them.

## Not yet implemented

- User accounts (teacher / student); the prototype is meant for local use.
- Students submitting their own work (now the teacher or the API adds it).
- Handwritten or scanned work (OCR); a .pdf works only if it contains text.
- Export to the e-klase / Mykoob format.
- Criteria templates for different kinds of work.
