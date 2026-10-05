def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running test (minutes); still part of the default run")
