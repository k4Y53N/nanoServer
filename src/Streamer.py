import logging as log
from pathlib import Path
from .Detector import Detector
from .Camera import Camera
from threading import Thread, Lock
from typing import Tuple
from time import sleep
from concurrent.futures import ThreadPoolExecutor, TimeoutError


class Streamer:
    def __init__(self, yolo_config_dir: Path, interval: float = 0.05, timeout=10):
        self.camera = Camera()
        self.detector = Detector(yolo_config_dir)
        self.thread_pool = ThreadPoolExecutor(5)
        self.__is_infer = False
        self.__is_stream = False
        self.__is_running = False
        self.interval = interval
        self.timeout = timeout
        self.lock = Lock()

    def __str__(self):
        return str(self.detector) + '\n' + str(self.camera)

    def start(self):
        self.camera.start()
        self.__is_running = True

    def join(self):
        self.camera.join()

    def reset(self):
        self.camera.reset()
        self.detector.reset()
        self.__is_infer = False
        self.__is_stream = False

    def close(self):
        self.__is_running = False
        self.camera.close()
        self.detector.close()

    def get(self) -> Tuple[str, list, list]:
        """
        get b64image, boxes, boxes_class
        :return: Tuple(b64image: str, boxes: list, boxes_class: list)
        """
        if not self.is_running():
            raise StopIteration

        with self.lock:
            is_stream = self.is_stream()
            is_infer = self.is_infer()

        try:
            if not is_stream:
                sleep(self.interval)
                return '', [], []

            is_image, image = self.camera.get()

            if not is_image:
                sleep(self.interval)
                return '', [], []

            if not (is_infer and self.detector.is_available()):
                sleep(self.interval)
                return self.camera.encode_image_to_b64(image), [], []

            b64image, boxes, boxes_class = self.infer_and_encode_image(image)
            return b64image, boxes, boxes_class

        except Exception as E:
            log.error(f'Streaming Fail {E.__class__.__name__}', exc_info=True)

        return '', [], []

    def infer_and_encode_image(self, image) -> Tuple[str, list, list]:
        with self.thread_pool as pool:
            encoding = pool.submit(self.camera.encode_image_to_b64, image)
            detecting = pool.submit(self.detector.detect, image)

        try:
            b64image = encoding.result(timeout=self.timeout)
            boxes, boxes_class = detecting.result(timeout=self.timeout)
            return b64image, boxes, boxes_class
        except TimeoutError as TOE:
            log.error('Encode and infer image time out', exc_info=TOE)
            return '', [], []

    def set_stream(self, is_stream: bool):
        with self.lock:
            self.__is_stream = is_stream

    def set_infer(self, is_infer: bool):
        with self.lock:
            self.__is_infer = is_infer

    def set_config(self, config_name):
        thread = Thread(target=self.detector.load_model, args=(config_name,))
        thread.start()

    def set_quality(self, width, height):
        self.camera.set_quality(width, height)

    def get_configs(self):
        return self.detector.get_configs()

    def get_config(self):
        return self.detector.get_config()

    def get_quality(self):
        return self.camera.get_quality()

    def is_stream(self):
        return self.__is_stream

    def is_infer(self):
        return self.__is_infer

    def is_running(self):
        return self.__is_running
