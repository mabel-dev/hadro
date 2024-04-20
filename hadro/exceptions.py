class MaximumRecordsExceeded(Exception):

    def __init__(self, memtable, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.memtable = memtable


class MissingDependencyError(Exception):  # pragma: no cover
    def __init__(self, dependency: str):
        self.dependency = dependency
        message = f"No module named '{dependency}' can be found, please install or include in requirements.txt"
        super().__init__(message)
