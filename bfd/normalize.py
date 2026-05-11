import re
import unicodedata

from rapidfuzz import fuzz


_UMLAUT_MAP = str.maketrans({
    "ß": "ss",
})

# Tokens that are too generic to imply two restaurants are the same place
# even when they're the only thing in common. Filters two classes:
#
# (a) "venue type" words: restaurant, cafe, bar — describe what the place IS
#     ("Hindi Restaurant" ↔ "Ni's Restaurant" share only "restaurant")
#
# (b) "cuisine/category" words: pizza, sushi, burger — describe what the place
#     SERVES, not which specific business it is. Without this filter,
#     "Pizza Nostra" matches "Capital Pizza" at 0.64 (share "pizza") even
#     though they're entirely different restaurants.
#
# (c) Geographic words: berlin, deutschland — same city/country isn't an
#     identifier ("Mister Subs Berlin" ↔ "KERB Berlin")
#
# Stoplist members are stripped from the "shared meaningful tokens" set
# during similarity scoring. If shared tokens minus this list is empty,
# the score is 0.0 regardless of character overlap.
_GENERIC_TOKENS = frozenset({
    # venue type
    "restaurant", "restaurants",
    "cafe", "cafes",
    "bar", "bars", "pub", "pubs", "bistro", "deli", "kitchen",
    "imbiss", "snack", "snackbar",
    # geographic
    "berlin", "deutschland", "germany",
    # filler
    "the", "and", "und", "by", "co", "of",
    # cuisine / dish category — these are descriptors, not identifiers
    "pizza", "pizzeria", "pizzas",
    "burger", "burgers",
    "sushi", "ramen", "noodles", "noodle", "pho",
    "kebab", "kebap", "doner", "döner", "dönerladen",
    "grill", "grillhaus",
    "bbq",
    "food", "eats", "eatery",
    "vegan", "veggie", "halal", "kosher",
    "sandwich", "sandwiches", "sub", "subs",
    "wraps", "wrap",
    "salad", "salads", "bowl", "bowls",
    "asian", "italian", "indian", "thai", "japanese", "korean",
    "chinese", "vietnamese", "lebanese", "turkish", "german",
    "french", "mexican",
    "ice", "icecream", "gelato",
    "bakery", "bakeries", "patisserie",
    "coffee", "kaffee",
})


def address(raw: str) -> str:
    """Normalise an address: lowercase, collapse whitespace, trim."""
    return re.sub(r"\s+", " ", raw.strip().lower())


def name(raw: str) -> str:
    """Normalise a restaurant name for fuzzy matching."""
    s = raw.lower().translate(_UMLAUT_MAP)
    # Strip remaining accents
    s = "".join(
        c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)
    )
    # Drop apostrophes silently, replace other punctuation with space
    s = re.sub(r"['’‘]", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_similarity(a: str, b: str) -> float:
    """Return [0.0, 1.0] similarity between two restaurant names.

    Algorithm:
    1. Normalise both names (umlauts, punctuation).
    2. If the normalised forms are identical, return 1.0.
    3. Otherwise require at least one *meaningful* shared token —
       i.e. a token that's not in the `_GENERIC_TOKENS` stoplist.
       Without a meaningful overlap we return 0.0, which is critical
       for correctness: rapidfuzz's `token_set_ratio` falls back to a
       character-level fuzzy ratio when token intersection is empty,
       producing misleadingly high scores (~0.5) for completely
       unrelated names that happen to share some letters
       (e.g. "Trois Minutes Sur Mer" vs "Royals & Rice Berlin" → 0.51).
    4. With a meaningful overlap, use `token_set_ratio` for the score.
    """
    na = name(a)
    nb = name(b)
    if na == nb:
        return 1.0
    ta = set(na.split())
    tb = set(nb.split())
    meaningful_shared = (ta & tb) - _GENERIC_TOKENS
    if not meaningful_shared:
        return 0.0
    return fuzz.token_set_ratio(na, nb) / 100.0
