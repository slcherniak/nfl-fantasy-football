"""Display-name rules for the Film Room's short team labels."""

from src.webdash import nicknames


def test_first_meaningful_word():
    names = {
        "1": "BUCKY SHAROOS MID FACTORY",
        "2": "Hiawatha’s Return",
        "3": "The Backdoor Bandits",
        "4": "Johnny \U0001F3B1’s Student Atholetes",
        "5": "Camp Gator",
        "6": "New Henry",
        "7": "The Freak Factory™️",
        "8": "Wayne Enterprises®",
        "9": "Redraft",
    }
    assert nicknames(names) == {
        "1": "Bucky", "2": "Hiawatha", "3": "Backdoor", "4": "Johnny",
        "5": "Gator", "6": "Henry", "7": "Freak", "8": "Wayne", "9": "Redraft",
    }


def test_collisions_fall_back_to_two_words():
    nicks = nicknames({"a": "Russell Stilson", "b": "Russell Wilson Fan Club"})
    assert nicks == {"a": "Russell Stilson", "b": "Russell Wilson"}


def test_overrides_by_name_or_key():
    names = {"1": "Camp Gator", "2": "New Henry"}
    assert nicknames(names, {"Camp Gator": "Gators", "2": "Hank"}) == {"1": "Gators", "2": "Hank"}
