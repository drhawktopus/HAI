"""The orchestrator deploy gate's collection pin, read by /opt/arrstack/run-suite.sh. It ships with the tests it counts,
so change it in the same deploy as the test change (Lab\\bin\\bump_pin.py). Not a test module: discovery skips it."""
EXPECT_TESTS = 4131
EXPECT_SKIPS = 2
# Tests in files a head may not carry: the lab's private extensions (they stay home) and the media add-on's (they ship
# in addons/media and come back when the add-on is installed). A head without a file runs that many fewer; tests/
# run_suite.py subtracts them, and tests/test_gate_pin_counts.py keeps these numbers true wherever the files exist.
OPTIONAL_TESTS = {
    "test_stash.py": 40,
    "test_tools_extensions.py": 7,
    "test_tools_ext_figure.py": 16,
    "test_render_options_ext_private.py": 17,
    "test_tools_ext_media.py": 12,
    "test_arrclient.py": 8,
    "test_qbitclient.py": 7,
    "test_qbit_tools.py": 10,
    "test_sonarr_write_tools.py": 15,
    "test_media_read_tools.py": 7,
    "test_phase2_tools.py": 33,
}
