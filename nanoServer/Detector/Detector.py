import cv2
import tensorflow as tf
import numpy as np
import logging as log
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Union, Dict, Optional
from .core.yolov4 import YOLO, decode, filter_boxes
from .core.configer import YOLOConfiger
from .DetectResult import DetectResult
from .DetectorInterface import DetectorInterface


def load_configer(configs_dir: Union[Path, str], config_suffix='*.json') -> Dict[str, YOLOConfiger]:
    if type(configs_dir) is str:
        configs_dir = Path(configs_dir)

    config_group = {}
    for config_file_path in configs_dir.glob(config_suffix):
        try:
            configer = YOLOConfiger(str(config_file_path))
            config_group[configer.name] = configer
        except KeyError:
            log.warning(f'Parse json file {config_file_path} to YOLO-Configer fail')

    return config_group


def build_model(configer: YOLOConfiger):
    size = configer.size
    frame_work = configer.frame_work
    tiny = configer.tiny
    model_type = configer.model_type
    score_threshold = configer.score_threshold
    num_class = configer.num_class
    weight_path = configer.weight_path
    strides = configer.strides
    anchors = configer.anchors
    xyscale = configer.xyscale
    input_layer = tf.keras.layers.Input([size, size, 3])
    feature_maps = YOLO(input_layer, num_class, model_type, tiny)
    bbox_tensors = []
    prob_tensors = []

    if tiny:
        for i, fm in enumerate(feature_maps):
            if i == 0:
                output_tensors = decode(fm, size // 16, num_class, strides, anchors, i, xyscale, frame_work)
            else:
                output_tensors = decode(fm, size // 32, num_class, strides, anchors, i, xyscale, frame_work)
            bbox_tensors.append(output_tensors[0])
            prob_tensors.append(output_tensors[1])
    else:
        for i, fm in enumerate(feature_maps):
            if i == 0:
                output_tensors = decode(fm, size // 8, num_class, strides, anchors, i, xyscale, frame_work)
            elif i == 1:
                output_tensors = decode(fm, size // 16, num_class, strides, anchors, i, xyscale, frame_work)
            else:
                output_tensors = decode(fm, size // 32, num_class, strides, anchors, i, xyscale, frame_work)
            bbox_tensors.append(output_tensors[0])
            prob_tensors.append(output_tensors[1])
    pred_bbox = tf.concat(bbox_tensors, axis=1)
    pred_prob = tf.concat(prob_tensors, axis=1)
    if frame_work == 'tflite':
        pred = (pred_bbox, pred_prob)
    else:
        boxes, pred_conf = filter_boxes(pred_bbox, pred_prob, score_threshold=score_threshold,
                                        input_shape=tf.constant([size, size]))
        pred = tf.concat([boxes, pred_conf], axis=-1)
    model = tf.keras.Model(input_layer, pred)
    model.load_weights(weight_path)

    return model


class Detector(DetectorInterface):
    def __init__(self, config_dir: Union[Path, str], is_show_exc_info=True) -> None:
        self.configer_group: Dict[str, YOLOConfiger] = load_configer(config_dir)
        self.configer: Optional[YOLOConfiger] = None
        self.__lock = Lock()
        self.__model: Optional[tf.keras.Model] = None
        self.__size = 0
        self.__classes = []
        self.__iou_threshold = 0.5
        self.__score_threshold = 0
        self.__max_total_size = 50
        self.__max_output_size_per_class = 20
        self.__timeout = 1
        self.__is_available = False
        self.__is_show_exc_info = is_show_exc_info

    def __str__(self):
        if self.configer is None:
            return '**No Configer Selected**'
        return 'Size: %d, Classes: %s, Score Threshold: %f' % (self.__size, self.__classes, self.__score_threshold)

    def load_model(self, config_name):
        if config_name not in self.configer_group.keys():
            log.info(f'Config not exist {config_name}')
            return
        log.info(f'Loading model {config_name}')
        acquired = self.__lock.acquire(True, self.__timeout)
        if not acquired:
            log.info(f'Loading model timeout, another model is loading')
            return
        try:
            self.__release()
            configer = self.configer_group[config_name]
            self.configer = configer
            self.__model = build_model(configer)
            self.__size = configer.size
            self.__classes = configer.classes
            self.__iou_threshold = configer.iou_threshold
            self.__score_threshold = configer.score_threshold
            self.__max_total_size = configer.max_total_size
            self.__max_output_size_per_class = configer.max_output_size_per_class
            self.__is_available = True
            log.info(f'Loading model {config_name} finish')
        except Exception as E:
            log.error(f'Loading model fail {E.__class__.__name__}', exc_info=self.__is_show_exc_info)
            self.__release()
        finally:
            self.__lock.release()

    def detect(self, image: np.ndarray, is_cv2=True) -> DetectResult:
        acquired = self.__lock.acquire(False)
        if not acquired:
            return DetectResult()
        try:
            detect_result = self.__infer(image, is_cv2=is_cv2)
            detect_result.classes = deepcopy(self.__classes)
            return detect_result
        except Exception as E:
            log.error(f'Detect image fail {E.__class__.__name__}', exc_info=self.__is_show_exc_info)
            return DetectResult()
        finally:
            self.__lock.release()

    def __infer(self, image: np.ndarray, is_cv2=True) -> DetectResult:
        if self.__model is None:
            return DetectResult()
        height, width = image.shape[:2]
        data = self.__normalization(image, is_cv2=is_cv2)
        pred = self.__model(data)
        batch_size, num_boxes = pred.shape[:2]

        nms_boxes, nms_scores, nms_classes, valid_detections = tf.image.combined_non_max_suppression(
            boxes=tf.reshape(pred[:, :, :4], (batch_size, num_boxes, 1, 4)),
            scores=pred[:, :, 4:],
            max_output_size_per_class=self.__max_output_size_per_class,
            max_total_size=self.__max_total_size,
            iou_threshold=self.__iou_threshold,
            score_threshold=self.__score_threshold,
        )
        valid_detections = valid_detections[0]
        nms_boxes = tf.reshape(nms_boxes, (-1, 4))
        nms_classes = tf.reshape(nms_classes, (-1, 1))
        nms_scores = tf.reshape(nms_scores, (-1))[:valid_detections].numpy().tolist()

        valid_data = tf.concat(
            (nms_boxes, nms_classes),
            axis=1
        )[:valid_detections]
        result = np.empty(valid_data.shape, dtype=np.int)
        for index, valid in enumerate(valid_data):
            # pred boxes = [y1, x1, y2, x2]
            # true boxes = [x1, y1, x2, y2]
            result[index][0] = valid[1] * width
            result[index][1] = valid[0] * height
            result[index][2] = valid[3] * width
            result[index][3] = valid[2] * height
            result[index][4] = valid[4]
        return DetectResult(boxes=result.tolist(), scores=nms_scores, classes=None)

    def __normalization(self, image: np.ndarray, is_cv2=True) -> np.ndarray:
        if is_cv2:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        return cv2.resize(image, (self.__size, self.__size))[np.newaxis, :] / 255.

    def reset(self):
        with self.__lock:
            self.__release()

    def close(self):
        self.reset()
        self.configer_group = None

    def __release(self):
        self.__is_available = False
        self.__model = None
        self.configer = None
        self.__size = 0
        self.__classes = []
        self.__score_threshold = 0
        self.__max_total_size = 20
        tf.keras.backend.clear_session()

    def get_configs(self) -> Dict[str, YOLOConfiger]:
        return self.configer_group

    def get_config(self) -> Optional[YOLOConfiger]:
        return self.configer
