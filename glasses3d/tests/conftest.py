def pytest_configure(config):
    # Each xdist worker opts itself out. A blanket descendant sweep would race the
    # process-inheritance tests, which deliberately launch neutral QoS children.
    from glasses3d.agent.procs import opt_out_self
    opt_out_self()
    config.addinivalue_line("markers", "slow: long-running test (minutes); still part of the default run")
    config.addinivalue_line("markers", "timing: fixed performance budget or load-sensitive subprocess deadline; run serially")
