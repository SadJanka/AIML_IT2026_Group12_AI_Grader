# Test data for the AI grader

25 made-up students x 3 assignments of increasing difficulty. The correct points for every submission are known
in advance (`expected-points.csv`), so the accuracy of the AI grading can be measured.

## How to use
1. On the home page of the application, click **"Create a test course"**.
2. Open the assignment **"1. Tests ..."** and drop the folder `1 - Tests (viegls)` (or its ZIP) into it.
   Leave "Grade with AI right away" checked.
3. Do the same with `2 - Eseja (vidējs)` and `3 - Aprēķini (grūts)` in the matching assignments.
4. On the course page, open **"Accuracy"** to see how far the AI grades are from the correct ones.

**Groq free tier:** one submission takes about 3,000 tokens and the daily limit is about 200,000 tokens, so roughly
60 submissions per day. Start with one assignment (25 submissions); grade the rest on another day (submissions that
failed stay in the list and can be graded again).

## Contents
- **1. Test (easy):** 8 questions (11 points). Answers: correct, correct in other words, partial, wrong, blank.
  Special cases: manipulation attempts, mixed order, no numbering, no diacritics, questions only.
- **2. Essay (medium):** argumentative essay (10 points). Varies the thesis, number of arguments and explanations,
  example and conclusion. Special cases: off-topic essay, manipulation, no diacritics; 3 submissions are emails (.eml).
- **3. Calculation (hard):** physics problem (6 points). Typical mistakes: minutes not converted, mean of the two speeds,
  arithmetic error, wrong total time, answer only, no units.
- File formats: .docx, .txt, .odt, .rtf, .eml; some file names use underscores (`Anna_Bērziņa.docx`).

The students' work is in Latvian. All names and submissions are made up.
