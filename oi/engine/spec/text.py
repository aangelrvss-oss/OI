import re
import unicodedata


def norm(s):
    """minúsculas, sin acentos/ñ->n, espacios colapsados."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def contains_phrase(haystack_norm, phrase_norm):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase_norm) + r"(?![a-z0-9])", haystack_norm) is not None


def numbers_in(s):
    return [int(x) for x in re.findall(r"\d+", str(s))]
