# Ultralytics YOLO 🚀, GPL-3.0 license
"""
Ultralytics Results, Boxes and Masks classes for handling inference results

Usage: See https://docs.ultralytics.com/modes/predict/
"""

from copy import deepcopy
from functools import lru_cache

import numpy as np
import torch
import torchvision.transforms.functional as F

from ultralytics.yolo.utils import LOGGER, SimpleClass, ops
from ultralytics.yolo.utils.plotting import Annotator, colors
from ultralytics.yolo.utils.torch_utils import TORCHVISION_0_10


class BaseTensor(SimpleClass):
    """

    Attributes: 
        tensor (torch.Tensor): A tensor.
        orig_shape (tuple): Original image size, in the format (height, width).

    Methods:
        cpu(): Returns a copy of the tensor on CPU memory.
        numpy(): Returns a copy of the tensor as a numpy array.
        cuda(): Returns a copy of the tensor on GPU memory.
        to(): Returns a copy of the tensor with the specified device and dtype.
    """
    def __init__(self, tensor, orig_shape) -> None:
        super().__init__()
        assert isinstance(tensor, torch.Tensor)
        self.tensor = tensor
        self.orig_shape = orig_shape

    @property
    def shape(self):
        return self.data.shape

    @property
    def data(self):
        return self.tensor

    def cpu(self):
        return self.__class__(self.data.cpu(), self.orig_shape)

    def numpy(self):
        return self.__class__(self.data.numpy(), self.orig_shape)

    def cuda(self):
        return self.__class__(self.data.cuda(), self.orig_shape)

    def to(self, *args, **kwargs):
        return self.__class__(self.data.to(*args, **kwargs), self.orig_shape)

    def __len__(self):  # override len(results)
        return len(self.data)

    def __getitem__(self, idx):
        return self.__class__(self.data[idx], self.orig_shape)


class Results(SimpleClass):
    """
    A class for storing and manipulating inference results.

    Args:
        orig_img (numpy.ndarray): The original image as a numpy array.
        path (str): The path to the image file.
        names (List[str]): A list of class names.
        boxes (List[List[float]], optional): A list of bounding box coordinates for each detection.
        masks (numpy.ndarray, optional): A 3D numpy array of detection masks, where each mask is a binary image.
        probs (numpy.ndarray, optional): A 2D numpy array of detection probabilities for each class.

    Attributes:
        orig_img (numpy.ndarray): The original image as a numpy array.
        orig_shape (tuple): The original image shape in (height, width) format.
        boxes (Boxes, optional): A Boxes object containing the detection bounding boxes.
        masks (Masks, optional): A Masks object containing the detection masks.
        probs (numpy.ndarray, optional): A 2D numpy array of detection probabilities for each class.
        names (List[str]): A list of class names.
        path (str): The path to the image file.
        _keys (tuple): A tuple of attribute names for non-empty attributes.
    """

    def __init__(self, orig_img, path, names, boxes=None, masks=None, probs=None, keypoints=None) -> None:
        self.orig_img = orig_img
        self.orig_shape = orig_img.shape[:2]
        self.boxes = Boxes(boxes, self.orig_shape) if boxes is not None else None  # native size boxes
        self.masks = Masks(masks, self.orig_shape) if masks is not None else None  # native size or imgsz masks
        self.probs = probs if probs is not None else None
        self.keypoints = keypoints if keypoints is not None else None
        self.names = names
        self.path = path
        self._keys = ('boxes', 'masks', 'probs', 'keypoints')

    def pandas(self):
        pass
        # TODO masks.pandas + boxes.pandas + cls.pandas

    def __getitem__(self, idx):
        r = self.new()
        for k in self.keys:
            setattr(r, k, getattr(self, k)[idx])
        return r

    def update(self, boxes=None, masks=None, probs=None):
        if boxes is not None:
            self.boxes = Boxes(boxes, self.orig_shape)
        if masks is not None:
            self.masks = Masks(masks, self.orig_shape)
        if boxes is not None:
            self.probs = probs

    def cpu(self):
        r = self.new()
        for k in self.keys:
            setattr(r, k, getattr(self, k).cpu())
        return r

    def numpy(self):
        r = self.new()
        for k in self.keys:
            setattr(r, k, getattr(self, k).numpy())
        return r

    def cuda(self):
        r = self.new()
        for k in self.keys:
            setattr(r, k, getattr(self, k).cuda())
        return r

    def to(self, *args, **kwargs):
        r = self.new()
        for k in self.keys:
            setattr(r, k, getattr(self, k).to(*args, **kwargs))
        return r

    def __len__(self):
        for k in self.keys:
            return len(getattr(self, k))

    def new(self):
        return Results(orig_img=self.orig_img, path=self.path, names=self.names)

    @property
    def keys(self):
        return [k for k in self._keys if getattr(self, k) is not None]

    def plot(self,
             show_conf=True,
             line_width=None,
             font_size=None,
             font='Arial.ttf',
             pil=False,
             example='abc',
             kpt_line=True):
        """
        Plots the detection results on an input RGB image. Accepts a numpy array (cv2) or a PIL Image.

        Args:
            show_conf (bool): Whether to show the detection confidence score.
            line_width (float, optional): The line width of the bounding boxes. If None, it is scaled to the image size.
            font_size (float, optional): The font size of the text. If None, it is scaled to the image size.
            font (str): The font to use for the text.
            pil (bool): Whether to return the image as a PIL Image.
            example (str): An example string to display. Useful for indicating the expected format of the output.
            kpt_line (bool): Whether to draw lines connecting keypoints.

        Returns:
            (None) or (PIL.Image): If `pil` is True, a PIL Image is returned. Otherwise, nothing is returned.
        """
        annotator = Annotator(deepcopy(self.orig_img), line_width, font_size, font, pil, example)
        boxes = self.boxes
        masks = self.masks
        probs = self.probs
        names = self.names
        keypoints = self.keypoints
        hide_labels, hide_conf = False, not show_conf
        if boxes is not None:
            for d in reversed(boxes):
                c, conf, id = int(d.cls), float(d.conf), None if d.id is None else int(d.id.item())
                name = ('' if id is None else f'id:{id} ') + names[c]
                label = None if hide_labels else (name if hide_conf else f'{name} {conf:.2f}')
                annotator.box_label(d.xyxy.squeeze(), label, color=colors(c, True))

        if masks is not None:
            im = torch.as_tensor(annotator.im, dtype=torch.float16, device=masks.data.device).permute(2, 0, 1).flip(0)
            if TORCHVISION_0_10:
                im = F.resize(im.contiguous(), masks.data.shape[1:], antialias=True) / 255
            else:
                im = F.resize(im.contiguous(), masks.data.shape[1:]) / 255
            annotator.masks(masks.data, colors=[colors(x, True) for x in boxes.cls], im_gpu=im)

        if probs is not None:
            n5 = min(len(names), 5)
            top5i = probs.argsort(0, descending=True)[:n5].tolist()  # top 5 indices
            text = f"{', '.join(f'{names[j] if names else j} {probs[j]:.2f}' for j in top5i)}, "
            annotator.text((32, 32), text, txt_color=(255, 255, 255))  # TODO: allow setting colors

        if keypoints is not None:
            for k in reversed(keypoints):
                annotator.kpts(k, self.orig_shape, kpt_line=kpt_line)

        return np.asarray(annotator.im) if annotator.pil else annotator.im


class Boxes(BaseTensor):
    """
    A class for storing and manipulating detection boxes.

    Args:
        boxes (torch.Tensor) or (numpy.ndarray): A tensor or numpy array containing the detection boxes,
            with shape (num_boxes, 6). The last two columns should contain confidence and class values.
        orig_shape (tuple): Original image size, in the format (height, width).

    Attributes:
        boxes (torch.Tensor) or (numpy.ndarray): A tensor or numpy array containing the detection boxes,
            with shape (num_boxes, 6).
        orig_shape (torch.Tensor) or (numpy.ndarray): Original image size, in the format (height, width).
        is_track (bool): True if the boxes also include track IDs, False otherwise.

    Properties:
        xyxy (torch.Tensor) or (numpy.ndarray): The boxes in xyxy format.
        conf (torch.Tensor) or (numpy.ndarray): The confidence values of the boxes.
        cls (torch.Tensor) or (numpy.ndarray): The class values of the boxes.
        id (torch.Tensor) or (numpy.ndarray): The track IDs of the boxes (if available).
        xywh (torch.Tensor) or (numpy.ndarray): The boxes in xywh format.
        xyxyn (torch.Tensor) or (numpy.ndarray): The boxes in xyxy format normalized by original image size.
        xywhn (torch.Tensor) or (numpy.ndarray): The boxes in xywh format normalized by original image size.
        data (torch.Tensor): The raw bboxes tensor

    Methods:
        cpu(): Move the object to CPU memory.
        numpy(): Convert the object to a numpy array.
        cuda(): Move the object to CUDA memory.
        to(*args, **kwargs): Move the object to the specified device.
        pandas(): Convert the object to a pandas DataFrame (not yet implemented).
    """

    def __init__(self, boxes, orig_shape) -> None:
        if boxes.ndim == 1:
            boxes = boxes[None, :]
        n = boxes.shape[-1]
        assert n in (6, 7), f'expected `n` in [6, 7], but got {n}'  # xyxy, (track_id), conf, cls
        # TODO
        self.is_track = n == 7
        self.boxes = boxes
        self.orig_shape = torch.as_tensor(orig_shape, device=boxes.device) if isinstance(boxes, torch.Tensor) \
            else np.asarray(orig_shape)

    @property
    def xyxy(self):
        return self.boxes[:, :4]

    @property
    def conf(self):
        return self.boxes[:, -2]

    @property
    def cls(self):
        return self.boxes[:, -1]

    @property
    def id(self):
        return self.boxes[:, -3] if self.is_track else None

    @property
    @lru_cache(maxsize=2)  # maxsize 1 should suffice
    def xywh(self):
        return ops.xyxy2xywh(self.xyxy)

    @property
    @lru_cache(maxsize=2)
    def xyxyn(self):
        return self.xyxy / self.orig_shape[[1, 0, 1, 0]]

    @property
    @lru_cache(maxsize=2)
    def xywhn(self):
        return self.xywh / self.orig_shape[[1, 0, 1, 0]]

    def pandas(self):
        LOGGER.info('results.pandas() method not yet implemented')

    @property
    def data(self):
        return self.boxes


class Masks(BaseTensor):
    """
    A class for storing and manipulating detection masks.

    Args:
        masks (torch.Tensor): A tensor containing the detection masks, with shape (num_masks, height, width).
        orig_shape (tuple): Original image size, in the format (height, width).

    Attributes:
        masks (torch.Tensor): A tensor containing the detection masks, with shape (num_masks, height, width).
        orig_shape (tuple): Original image size, in the format (height, width).

    Properties:
        xy (list): A list of segments (pixels) which includes x, y segments of each detection.
        xyn (list): A list of segments (normalized) which includes x, y segments of each detection.

    Methods:
        cpu(): Returns a copy of the masks tensor on CPU memory.
        numpy(): Returns a copy of the masks tensor as a numpy array.
        cuda(): Returns a copy of the masks tensor on GPU memory.
        to(): Returns a copy of the masks tensor with the specified device and dtype.
    """

    def __init__(self, masks, orig_shape) -> None:
        self.masks = masks  # N, h, w
        self.orig_shape = orig_shape

    @property
    @lru_cache(maxsize=1)
    def segments(self):
        # Segments-deprecated (normalized)
        LOGGER.warning("WARNING ⚠️ 'Masks.segments' is deprecated. Use 'Masks.xyn' for segments (normalized) and "
                       "'Masks.xy' for segments (pixels) instead.")
        return self.xyn

    @property
    @lru_cache(maxsize=1)
    def xyn(self):
        # Segments (normalized)
        return [
            ops.scale_coords(self.masks.shape[1:], x, self.orig_shape, normalize=True)
            for x in ops.masks2segments(self.masks)]

    @property
    @lru_cache(maxsize=1)
    def xy(self):
        # Segments (pixels)
        return [
            ops.scale_coords(self.masks.shape[1:], x, self.orig_shape, normalize=False)
            for x in ops.masks2segments(self.masks)]

    @property
    def data(self):
        return self.masks
