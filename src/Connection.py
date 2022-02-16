import logging as log
from socket import socket, AF_INET, SOCK_STREAM, timeout
from queue import Queue, Full, Empty
from threading import Thread, Lock, Event
from json import loads, dumps
from struct import calcsize, pack, unpack
from typing import Union, Tuple


class VerificationError(Exception):
    pass


class Connection(Thread):
    def __init__(self, ip: str = 'localhost', port: int = 0) -> None:
        Thread.__init__(self)
        self.__format = 'utf-8'
        self.__header_format = '>i'
        self.__header_size = calcsize(self.__header_format)
        self.__connect_keyword = ('LOGOUT', 'EXIT', 'SHUTDOWN')
        self.__server_sock = socket(AF_INET, SOCK_STREAM)
        self.__server_sock.bind((ip, port))
        self.__send_lock = Lock()
        self.__input_buffer = Queue()
        self.__output_buffer = Queue()
        self.__server_address = self.__server_sock.getsockname()
        self.__client_address = None
        self.__connect_event = Event()
        self.__client_connect_event = Event()

    def run(self):
        self.__server_sock.settimeout(300)
        self.__server_sock.listen(1)
        log.info('Connection Server Address => %s:%d' % self.__server_address)

        while not self.__connect_event.wait(0):
            try:
                log.info('Waiting Client Connect...')
                client, address = self.__server_sock.accept()
                self.__handle_client(client, address)
            except VerificationError as VE:
                log.warning('Verification Fail %s' % VE.args[0], exc_info=True)
            except (OSError, KeyboardInterrupt, timeout, Exception) as E:
                log.error(E.__class__.__name__, exc_info=True)
            else:
                client.close()
            finally:
                self.__reset()

        self.close()

    def __handle_client(self, client: socket, address: Tuple[str, int]):
        try:
            self.__client_address = client.getpeername()
            log.info('Client Connect Address => %s:%d' % address)
            client.settimeout(30)
            recv_thread = Thread(target=self.__listening, args=(client,))
            send_thread = Thread(target=self.__sending, args=(client,))
            recv_thread.start()
            send_thread.start()
            recv_thread.join()
            send_thread.join()
            client.close()
        except Exception as E:
            raise E

    def __verify(self):
        pass

    def __listening(self, client: socket):
        while not self.__client_connect_event.wait(0):
            try:
                message, address = self.__recv_message(client), client.getpeername()
                if message['CMD'] in self.__connect_keyword:
                    self.__normal_disconnect(client, message)
                else:
                    self.__input_buffer.put((message, address), True, 0.2)
            except Full:
                continue
            except(RuntimeError, OSError, timeout, Exception) as E:
                log.error(f'{E.__class__.__name__} with {E.__args[0]}', exc_info=True)
                self.__non_normal_disconnect(client)

    def __recv_message(self, client: socket) -> dict:
        msg_length = self.__recv_all(client, self.__header_size)
        msg_length = unpack(self.__header_format, msg_length)[0]
        msg_bytes = self.__recv_all(client, msg_length)
        message = loads(msg_bytes)
        return message

    def __recv_all(self, client: socket, buffer_size: int) -> bytearray:
        buffer = bytearray()

        while len(buffer) < buffer_size:
            _bytes = client.recv(buffer_size - len(buffer))
            if not _bytes:
                raise RuntimeError('Pipline close')
            buffer.extend(_bytes)

        return buffer

    def __sending(self, client: socket):
        while not self.__client_connect_event.wait(0):
            try:
                msg_bytes, address = self.__output_buffer.get(True, 0.2)
                if address != client.getpeername():
                    continue
                self.__send_message(client, msg_bytes)
            except Empty:
                continue
            except(RuntimeError, OSError, timeout, Exception) as E:
                log.error(f'{E.__class__.__name__} with {E.__args[0]}', exc_info=True)
                self.__non_normal_disconnect(client)

    def __send_message(self, client: socket, message: dict):
        with self.__send_lock:
            message = dumps(message).encode(self.__format)
            header_bytes = pack(self.__header_format, len(message))
            self.__send_all(client, header_bytes)
            self.__send_all(client, message)

    def __send_all(self, client: socket, _bytes: bytes):
        total_send = 0

        while total_send < len(_bytes):
            sent = client.send(_bytes[total_send:])
            if not sent:
                raise RuntimeError('Pipline close')
            total_send += sent

    def __non_normal_disconnect(self, client: socket):
        self.__client_connect_event.set()
        client.close()

    def __normal_disconnect(self, client: socket, connection_ctrl: dict):
        self.__client_connect_event.set()
        client.close()

    def __reset(self):
        self.__clear_buffer()
        self.__client_connect_event.clear()
        self.__client_address = None

    def __clear_buffer(self):
        with self.__input_buffer.mutex:
            self.__input_buffer.queue.clear()
        with self.__output_buffer.mutex:
            self.__output_buffer.queue.clear()

    def __login(self):
        pass

    def __logout(self):
        pass

    def __exit(self):
        pass

    def close(self):
        self.__reset()
        self.__connect_event.set()
        self.__client_connect_event.set()
        self.__server_sock.close()

    def get(self, time_limit: float = 0.2) -> Tuple[dict, Tuple[str, int]]:
        try:
            message, address = self.__input_buffer.get(True, time_limit)
            return message, address
        except Empty:
            return dict(), self.__client_address

    def put(self, message: dict, address: Tuple[str, int], time_limit: float = 0.2):
        try:
            self.__output_buffer.put((message, address), True, time_limit)
        except Full:
            return

    def get_server_address(self) -> Tuple[str, int]:
        return self.__server_address

    def get_client_address(self) -> Union[Tuple[str, int], None]:
        return self.__client_address

    def is_connect(self) -> bool:
        return not self.__connect_event.is_set()