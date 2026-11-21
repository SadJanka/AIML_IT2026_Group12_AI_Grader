import re
import unicodedata

from ..i18n import N_, translate

DELIMITER_TAG = "student_work"
EXAMPLE_TAG = "example_work"

_HIDDEN_CHARS = re.compile("[​‌‍⁠﻿­]")

_PATTERNS: list[tuple[str, str]] = [
    (r"ignor\w*\s+(\w+\s+){0,3}(iepriekš\w*|norādījum\w*|instrukcij\w*|kritērij\w*|noteikum\w*|previous|instructions?|rules|criteria)",
     N_("request to ignore instructions")),
    (r"(neņem|neievēro)\w*\s+vērā\s+(\w+\s+){0,2}(kritērij\w*|norādījum\w*|instrukcij\w*)",
     N_("request to disregard the criteria")),
    (r"(disregard|override|forget)\s+(all\s+|the\s+|your\s+)*(previous|above|instructions?|rules|criteria)",
     N_("request to ignore instructions")),
    (r"(maksimāl\w*|piln\w*)\s+(\w+\s+)?(vērtējum\w*|punkt\w*|atzīm\w*)", N_("asks for the maximum grade")),
    (r"augstāk\w*\s+(\w+\s+)?(vērtējum\w*|atzīm\w*)", N_("asks for the highest grade")),
    (r"(liec|ieliec|dod|piešķir|uzliec)\s+(man\s+)?(\w+\s+)?(10|desmit\w*)\s*(ball\w*|atzīm\w*)",
     N_("asks for a specific grade")),
    (r"(atzīm\w*|vērtējum\w*)\s+(10|desmit)\b", N_("asks for a specific grade")),
    (r"(give|assign|award)\s+(me\s+)?(full|maximum|max|the\s+highest|10|100)", N_("asks for the maximum grade")),
    (r"\b(system\s*prompt|sistēmas\s+(prompt\w*|uzvedn\w*|norādījum\w*))", N_("refers to the system prompt")),
    (r"\b(tu\s+(tagad\s+)?esi|you\s+are\s+now|act\s+as|pretend\s+to\s+be|jailbreak)\b",
     N_("attempts to change the AI's role")),
    (r"(^|[.!?\n]\s*)(dārg\w*\s+|cien\w*\s+|hey\s+|dear\s+)?(ai\s+)?(ai|vērtētāj\w*|chatgpt|gpt|claude|copilot|gemini)\s*[,:]",
     N_("addresses the AI or the grader")),
    (r"(note|piezīme)\s+(to|for|priekš)\s+(the\s+)?(ai|grader|vērtētāj\w*|mi)", N_("note to the AI grader")),
    (rf"</?\s*({DELIMITER_TAG}|{EXAMPLE_TAG})", N_("attempts to close the data block")),
]

_COMPILED = [(re.compile(p, re.IGNORECASE | re.UNICODE), label) for p, label in _PATTERNS]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    return _HIDDEN_CHARS.sub("", text)


def detect_injection(text: str, language: str = "lv") -> list[str]:
    findings: list[str] = []
    raw = text or ""
    hidden = len(_HIDDEN_CHARS.findall(raw))
    if hidden:
        findings.append(translate("the text contains {n} invisible characters", language, n=hidden))

    clean = normalize(raw)
    for regex, label in _COMPILED:
        match = regex.search(clean)
        if match:
            snippet = clean[max(0, match.start() - 20): match.end() + 20].replace("\n", " ").strip()
            findings.append(f'{translate(label, language)}: "…{snippet}…"')
    return findings


def escape_for_prompt(text: str) -> str:
    text = re.sub(DELIMITER_TAG, "student-work", normalize(text), flags=re.IGNORECASE)
    return re.sub(EXAMPLE_TAG, "example-work", text, flags=re.IGNORECASE)
