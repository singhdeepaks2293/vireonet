"""Test package.

Makes ``tests.contract_suite`` importable under both ``pytest`` and
``python -m pytest``. Previously the bare ``pytest`` entry point failed
collection of test_contract / test_servicenow_real / test_netbox_real
because the repo root was not on ``sys.path``.
"""
