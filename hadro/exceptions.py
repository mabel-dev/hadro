class MaximumRecordsExceeded(Exception):

    def __init__(self, memtable, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.memtable = memtable
