import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from murmur.cleanup import rule_based_cleanup


def test_strips_fillers():
    out = rule_based_cleanup("um so uh I think we should uh go to the store")
    assert "um" not in out.lower().split() and "uh" not in out.lower().split()


def test_capitalizes_and_terminates():
    out = rule_based_cleanup("this is a test")
    assert out[0] == "T"
    assert out.endswith(".")


def test_spoken_punctuation():
    out = rule_based_cleanup("hello comma how are you question mark")
    assert "hello," in out.lower() or "Hello," in out
    assert out.strip().endswith("?")


def test_lone_i_capitalized():
    out = rule_based_cleanup("i think i am ready")
    assert " I " in out or out.startswith("I ")
    assert " i " not in out
