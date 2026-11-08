"""Built-in policies; selection never imports a user-supplied path."""
from importlib import import_module

AI_CHOICES = {"default": "sc2.agent", "a": "sc2.policies.a", "b": "sc2.policies.b", "a2": "sc2.policies.a2", "a3": "sc2.policies.a3", "a4": "sc2.policies.a4"}


def load_policy(name):
    return import_module(AI_CHOICES[name])
