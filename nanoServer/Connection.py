import logging as log
from socket import socket, AF_INET, SOCK_STREAM, timeout, SHUT_RDWR
from queue import Queue, Full, Empty
from threading import Thread, Lock
from json import loads, dumps
from struct import calcsize, pack, unpack
from typing import Union, Tuple
from .RepeatTimer import RepeatTimer
from nanoServer.API import SYS_LOGOUT, SYS_EXIT, SYS_SHUTDOWN

_LOGOUT = 'LOGOUT'
_SYS_CTRL_EXIT = 'EXIT'
_SYS_CTRL_SHUTDOWN = 'SHUTDOWN'


class ConnectionBuilder:
    def __init__(self):
        self.ip = 'localhost'
        self.port = 0
        self.server_timeout = 300
        self.client_timeout = 30
        self.is_show_exc_info = False


class VerificationError(Exception):
    pass


class Connection(RepeatTimer):
    def __init__(self, builder: ConnectionBuilder):
        RepeatTimer.__init__(self, interval=0, name='Connection')
        self.__format = 'utf-8'
        self.__header_format = '>i'
        self.__header_size = calcsize(self.__header_format)
        self.__connect_keyword = (_LOGOUT, _SYS_CTRL_EXIT, _SYS_CTRL_SHUTDOWN)
        self.__server_sock = socket(AF_INET, SOCK_STREAM)
        self.__server_sock.bind((builder.ip, builder.port))
        self.__send_lock = Lock()
        self.__input_buffer = Queue(50)
        self.__output_buffer = Queue(50)
        self.__server_address = self.__server_sock.getsockname()
        self.is_show_exc_info = builder.is_show_exc_info
        self.server_timeout = builder.server_timeout
        self.client_timeout = builder.client_timeout
        self.__client_address = None
        self.__is_client_connect = True
        self.__sys_final_ctrl = _SYS_CTRL_EXIT

    def __str__(self):
        return 'Server address: %s | Client address: %s' % (self.__server_address, self.__client_address)

    def init_phase(self):
        self.__server_sock.settimeout(self.server_timeout)
        self.__server_sock.listen(1)
        log.info('Connection Server Address => %s:%d' % self.__server_address)

    def execute_phase(self):
        try:
            log.info('Waiting Client Connect...')
            client, address = self.__server_sock.accept()
            client.settimeout(self.client_timeout)
            self.__handle_client(client, address)
            client.close()
        except VerificationError as VE:
            log.warning(f'Verification fail {VE.__class__.__name__}', exc_info=self.is_show_exc_info)
        except (OSError, KeyboardInterrupt, timeout, Exception) as E:
            self.close()
            log.error(f'Server socket accept fail {E.__class__.__name__}', exc_info=self.is_show_exc_info)
        finally:
            self.__reset()

    def close(self):
        super().close()
        self.__server_sock.shutdown(SHUT_RDWR)
        self.__server_sock.close()

    def close_phase(self):
        self.__reset()
        self.__is_client_connect = False
        self.__server_sock.close()

    def __handle_client(self, client: socket, address: Tuple[str, int]):
        try:
            self.__client_address = client.getpeername()
            log.info('Client Connect Address => %s:%d' % address)
            recv_thread = Thread(target=self.__listening, args=(client,), name='ClientRecv')
            send_thread = Thread(target=self.__sending, args=(client,), name='ClientSend')
            recv_thread.start()
            send_thread.start()
            recv_thread.join()
            send_thread.join()
            client.close()
        except Exception as E:
            raise E

    def __listening(self, client: socket):
        while self.__is_client_connect and self.is_running():
            try:
                message, address = self.__recv_message(client), client.getpeername()
                if type(message) is not dict:
                    log.warning(f'Connection receive message is not JSON Dictionary format from {address}')
                    raise RuntimeError('Wrong message format')
                cmd = message.get('CMD', None)
                if cmd is None:
                    log.warning('Message didnt define CMD')
                    raise RuntimeError('Cant parse "CMD" key')
                if cmd in self.__connect_keyword:
                    self.__normal_disconnect(client, message)
                else:
                    self.__input_buffer.put((message, address), True, 0.2)
            except Full:
                continue
            except Exception as E:
                log.error(f'Client socket listening fail {E.__class__.__name__}', exc_info=self.is_show_exc_info)
                self.__non_normal_disconnect()

    def __recv_message(self, client: socket):
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
        while self.__is_client_connect and self.is_running():
            try:
                message, address = self.__output_buffer.get(True, 0.2)
                if address != client.getpeername():
                    continue
                self.__send_message(client, message, block=False)
            except Empty:
                continue
            except Exception as E:
                log.error(f'Client socket sending fail {E.__class__.__name__}', exc_info=self.is_show_exc_info)
                self.__non_normal_disconnect()

    def __send_message(self, client: socket, message: dict, block=False):
        """
        send message to client socket
        :param client: client socket
        :param message: dictionary can be parsed to json format
        :param block: if block = false will skip when didn't get the lock
        :return:
        """
        acquired = self.__send_lock.acquire(block)
        if not acquired:
            return
        try:
            message = dumps(message).encode(self.__format)
            header_bytes = pack(self.__header_format, len(message))
            self.__send_all(client, header_bytes)
            self.__send_all(client, message)
        except Exception as E:
            raise E
        finally:
            self.__send_lock.release()

    def __send_all(self, client: socket, _bytes: bytes):
        total_send = 0

        while total_send < len(_bytes):
            sent = client.send(_bytes[total_send:])
            if not sent:
                raise RuntimeError('Pipline close')
            total_send += sent

    def __non_normal_disconnect(self):
        self.__is_client_connect = False

    def __normal_disconnect(self, client: socket, connection_ctrl: dict):
        self.__is_client_connect = False
        ctrl = connection_ctrl.get('CMD')
        if ctrl == _LOGOUT:
            self.__logout(client)
        elif ctrl == _SYS_CTRL_EXIT:
            self.__exit(client)
        elif ctrl == _SYS_CTRL_SHUTDOWN:
            self.__shutdown(client)

    def __reset(self):
        self.__clear_buffer()
        self.__is_client_connect = True
        self.__client_address = None

    def __clear_buffer(self):
        with self.__input_buffer.mutex:
            self.__input_buffer.queue.clear()
        with self.__output_buffer.mutex:
            self.__output_buffer.queue.clear()

    def __login(self):
        pass

    def __logout(self, client: socket):
        try:
            self.__send_message(client, SYS_LOGOUT.copy(), block=True)
        except Exception as E:
            log.error(f'client send logout message fail {E.__class__.__name__}', exc_info=self.is_show_exc_info)

    def __exit(self, client: socket):
        try:
            self.__send_message(client, SYS_EXIT.copy(), block=True)
        except Exception as E:
            log.error(f'client send exit message fail {E.__class__.__name__}', exc_info=self.is_show_exc_info)
        finally:
            self.__sys_final_ctrl = _SYS_CTRL_EXIT
            self.close()

    def __shutdown(self, client: socket):
        try:
            self.__send_message(client, SYS_SHUTDOWN.copy(), block=True)
        except Exception as E:
            log.error(f'client send shutdown message fail {E.__class__.__name__}', exc_info=self.is_show_exc_info)
        finally:
            self.__sys_final_ctrl = _SYS_CTRL_SHUTDOWN
            self.close()

    def get(self, time_limit: float = 0.2) -> Tuple[dict, Union[Tuple[str, int], None]]:
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
        return self.is_running()
