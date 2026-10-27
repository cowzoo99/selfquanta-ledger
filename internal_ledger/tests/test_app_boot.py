import unittest


def test_app_metadata_is_importable():
    import internal_ledger

    assert internal_ledger.__version__ == "0.1.0"


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([unittest.FunctionTestCase(test_app_metadata_is_importable)])
