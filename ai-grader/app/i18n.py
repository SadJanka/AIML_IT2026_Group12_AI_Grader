from .i18n_lv import LV

SUPPORTED = ("lv", "en")
DEFAULT = "lv"
LANGUAGE_NAMES = {"lv": "Latviešu", "en": "English"}


def N_(text: str) -> str:
    return text


def normalize(lang: str | None) -> str:
    return lang if lang in SUPPORTED else DEFAULT


def translate(text: str, lang: str, **params) -> str:
    result = LV.get(text, text) if lang == "lv" else text
    return result.format(**params) if params else result
