class SimpleLamportProvider:
    """
    A simple Lamport clock provider for managing a logical clock in a single-threaded or isolated
    testing environment. This class simulates the basic behavior of a Lamport clock, which is
    used to order events in a distributed system without relying on synchronized physical clocks.

    Methods:
        get_and_increment_counter: Returns the current Lamport counter value and increments it for
        the next use.
    """

    def __init__(self):
        """Initialize the Lamport clock with a counter starting at zero."""
        self._counter = 0

    def get_and_increment_counter(self):
        """
        Retrieve the current Lamport counter value and increment it.

        Returns:
            int: The current value of the Lamport counter before it is incremented.
        """
        pre_update = self._counter
        self._counter += 1
        return pre_update
