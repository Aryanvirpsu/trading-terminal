import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "network: needs real internet access (a live data vendor)")
