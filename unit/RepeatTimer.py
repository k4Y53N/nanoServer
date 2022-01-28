from threading import Thread, Event

"""
init phase -> wait for period -> execute phase -> close -> close phase
"""


class RepeatTimer(Thread):
    def __init__(self, func=None, args=(), kwargs=None, interval=1.):
        Thread.__init__(self)
        if kwargs is None:
            kwargs = dict()
        self.__func = func
        self.__args = args
        self.__kwargs = kwargs
        self.__interval = interval
        self.__event = Event()

    def run(self) -> None:
        self.init_phase()
        while not self.__event.wait(self.__interval):
            self.execute_phase()
        self.close_phase()

    def init_phase(self):
        pass

    def execute_phase(self):
        if self.__func is not None:
            self.__func(*self.__args, **self.__kwargs)

    def close(self):
        self.__event.set()

    def close_phase(self):
        pass

    def set_interval(self, interval: float):
        if interval < 0:
            raise ValueError('interval must greater than 0')
        self.__interval = interval
