from bfd import normalize


def test_normalize_address_lowercases_and_strips():
    assert normalize.address("  Sonnenallee  100, 12045  Berlin  ") == "sonnenallee 100, 12045 berlin"


def test_normalize_address_collapses_whitespace():
    assert normalize.address("Karl\tMarx Str\n10") == "karl marx str 10"


def test_normalize_name_strips_umlauts():
    assert normalize.name("Mustafa's Gemüse Kebap") == "mustafas gemuse kebap"


def test_normalize_name_drops_punctuation():
    assert normalize.name("Pizza-Hut!") == "pizza hut"


def test_name_similarity_identical():
    assert normalize.name_similarity("Cocolo Ramen", "Cocolo Ramen") == 1.0


def test_name_similarity_umlaut_variants():
    sim = normalize.name_similarity("Mustafa's Gemüse Kebap", "Mustafas Gemuse Kebap")
    assert sim >= 0.95


def test_name_similarity_unrelated():
    sim = normalize.name_similarity("Standard Pizza", "Burger King")
    assert sim < 0.5


def test_name_similarity_disjoint_tokens_returns_zero():
    """Regression: completely disjoint tokens must score 0, not ~0.5.

    rapidfuzz's token_set_ratio falls back to character-level fuzzy
    matching when the token intersection is empty, which produced
    misleading scores like 0.51 for clearly different restaurants
    (the original Trois-Minutes-Sur-Mer ↔ Royals-&-Rice false match)."""
    assert normalize.name_similarity("Trois Minutes Sur Mer", "Royals & Rice Berlin") == 0.0


def test_name_similarity_only_generic_token_returns_zero():
    """When the only shared token is a generic word like 'restaurant',
    that's not enough evidence the places are the same."""
    assert normalize.name_similarity("Hindi Restaurant", "Ni's Restaurant") == 0.0
    assert normalize.name_similarity("Mister Subs Berlin", "KERB Berlin") == 0.0


def test_name_similarity_only_cuisine_word_returns_zero():
    """Sharing only a cuisine/category word is not enough to identify
    two restaurants as the same place. Regression: 'Pizza Nostra' matched
    'Capital Pizza' at 0.64 because they share 'pizza'."""
    assert normalize.name_similarity("Pizza Nostra", "Capital Pizza") == 0.0
    assert normalize.name_similarity("Sushi Yana", "Sushi Bar Mori") == 0.0
    assert normalize.name_similarity("Burger Heaven", "Burger Mafia") == 0.0


def test_name_similarity_chain_with_strong_identifier():
    """A non-generic shared token of any reasonable length should match.
    e.g. 'Sushi Yana' vs 'Sushi Yana - Neukölln' share 'yana' (4 chars,
    not in stoplist) — that's a real chain match."""
    assert normalize.name_similarity("Sushi Yana", "Sushi Yana - Neukölln") >= 0.7
    assert normalize.name_similarity("Cocolo Ramen", "Cocolo Ramen X-Berg") >= 0.7


def test_name_similarity_chain_with_location_suffix():
    """A BFS chain entry (e.g. 'Burgermeister') should still match a
    platform listing for one of its branches ('Burgermeister Schlesisches Tor')."""
    assert normalize.name_similarity("Burgermeister", "Burgermeister Schlesisches Tor") >= 0.9
