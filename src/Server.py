import logging as log
from socket import socket, AF_INET, SOCK_STREAM
from typing import Optional
from .ClientHandler import AsyncClientHandler, EventHandler
from .RepeatTimer import RepeatTimer


class ServerBuilder:
    ip = 'localhost'
    port = 0
    is_show_exc_info = False
    server_timeout = 300
    client_timeout = 300
    max_connection = 1

    def set_ip(self, ip):
        self.ip = ip
        return self

    def set_port(self, port):
        self.port = port
        return self

    def set_is_show_exc_info(self, is_show_exc_info):
        self.is_show_exc_info = is_show_exc_info
        return self

    def set_server_timeout(self, server_timeout):
        self.server_timeout = server_timeout
        return self

    def set_client_timeout(self, client_timeout):
        self.client_timeout = client_timeout
        return self

    def set_max_connection(self, max_connection):
        self.max_connection = max_connection
        return self


class Server(RepeatTimer):
    def __init__(self, ip, port, max_connection=1, is_show_exc_info=False):
        RepeatTimer.__init__(self)
        self.server_sock = socket(AF_INET, SOCK_STREAM)
        self.server_sock.bind((ip, port))
        self.ip, self.port = self.server_sock.getsockname()
        self.event_handler = EventHandler()
        self.is_show_exc_info = is_show_exc_info
        self.max_connection = max_connection
        self.client_handler: Optional[AsyncClientHandler] = None

    def __str__(self):
        s = ''
        s += f'Server address => {self.ip}:{self.port}'
        handler = self.client_handler
        if handler is not None:
            s += f'\n{handler}'
        else:
            s += '\nNo Client Connected'
        return s

    def init_phase(self):
        self.server_sock.listen(self.max_connection)
        log.info(f'IP ==> {self.ip} port ==> {self.port}')

    def execute_phase(self):
        try:
            log.info('Waiting Client connect......')
            client, address = self.server_sock.accept()
            with client:
                handler = AsyncClientHandler(client, self.event_handler)
                self.client_handler = handler
                handler.run()
            self.client_handler = None
        except Exception as E:
            log.error('Error!', exc_info=self.is_show_exc_info)

    def close_phase(self):
        self.server_sock.close()

    def routine(self, *args, **kwargs):
        def wrap(func):
            self.event_handler.routine_func_map[func] = (args, kwargs)

        return wrap

    def enter(self, *args, **kwargs):
        def wrap(func):
            self.event_handler.enter_func_map[func] = (args, kwargs)

        return wrap

    def exit(self, *args, **kwargs):
        def wrap(func):
            self.event_handler.exit_func_map[func] = (args, kwargs)

        return wrap

    def response(self, key: str, *args, **kwargs):
        if kwargs is None:
            kwargs = {}

        def wrap(func):
            self.event_handler.response_func_map[key] = (func, args, kwargs)

        return wrap
