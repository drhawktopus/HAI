"""Uhura's tests run on synthetic private rules (test_private_rules.json), never the lab's own lists."""
import os

os.environ["UHURA_PRIVATE_RULES"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_private_rules.json")
