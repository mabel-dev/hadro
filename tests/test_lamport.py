import os
import sys
import pytest

sys.path.insert(1, os.path.join(sys.path[0], ".."))
sys.path.insert(1, os.path.join(sys.path[0], "../.."))

from hadro.lamport import SimpleLamportProvider


def test_initial_counter_value():
    """Test that the Lamport counter starts at zero."""
    provider = SimpleLamportProvider()
    assert provider.get_and_increment_counter() == 0, "Initial counter value should be zero"


def test_counter_increment():
    """Test that the Lamport counter increments correctly after a single call."""
    provider = SimpleLamportProvider()
    provider.get_and_increment_counter()  # Increment once
    assert provider.get_and_increment_counter() == 1, "Counter should increment to 1 after one call"


def test_counter_multiple_increments():
    """Test that the Lamport counter increments correctly over multiple calls."""
    provider = SimpleLamportProvider()
    for i in range(10):
        assert (
            provider.get_and_increment_counter() == i
        ), f"Counter should be {i} at the {i}-th call"


# Run the pytest tests (usually executed in a terminal or test runner environment)
if __name__ == "__main__":
    pytest.main()
