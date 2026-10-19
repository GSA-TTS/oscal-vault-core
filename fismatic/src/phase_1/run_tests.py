import pytest
import sys
from importlib import import_module


def run_standalone_tests():
    """Run the standalone version of tests with print statements"""
    print("\n=== Running Standalone Tests ===")
    print("=" * 50)

    # Import the test module
    test_module = import_module("test_parser_functions")

    # Run individual test functions
    test_functions = [
        "test_implementation_status",
        "test_control_origination",
        "test_section_parsing",
        "test_customer_responsibilities",
        "test_combined_parsing",
    ]

    for test_name in test_functions:
        try:
            test_func = getattr(test_module, test_name)
            test_func()
            print(f"\n✓ {test_name} passed")
        except Exception as e:
            print(f"\n✗ {test_name} failed: {str(e)}")
            raise


def run_pytest_suite():
    """Run the pytest version of tests"""
    print("\n=== Running PyTest Suite ===")
    print("=" * 50)
    pytest.main(["-v", "test_parser_functions.py"])


if __name__ == "__main__":
    # Add the project path to system path
    project_path = "/home/john/fedramp-automation/fismatic/src/phase_1"
    if project_path not in sys.path:
        sys.path.append(project_path)

    try:
        # Run both test suites
        run_standalone_tests()
        print("\n")
        run_pytest_suite()

        print("\n✓ All test suites completed successfully!")
    except Exception as e:
        print(f"\n✗ Tests failed: {str(e)}")
        sys.exit(1)
