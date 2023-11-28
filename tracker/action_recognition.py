from itertools import combinations

import cv2
import networkx as nx
import numpy as np

font = cv2.FONT_HERSHEY_SIMPLEX
font_scale = 0.4
font_thickness = 1
text_color = (255, 255, 255)
background_color = (100, 100, 100)
text_padding = 7


class ActionRecognizer:
    def __init__(self, config):
        # Gathering parameters
        self.gathering_enabled = config["gathering"]["enabled"]
        self.distance_threshold = config["gathering"]["distance_threshold"]
        self.area_threshold = config["gathering"]["area_threshold"]
        # Standing still parameters
        self.ss_enabled = config["standing_still"]["enabled"]
        self.ss_frame_window = config["standing_still"]["frame_window"]
        self.ss_speed_threshold = config["standing_still"]["speed_threshold"]
        # Fast approach parameters
        self.fa_enabled = config["fast_approach"]["enabled"]
        self.fa_distance_threshold = config["fast_approach"]["distance_threshold"]
        self.fa_speed_threshold = config["fast_approach"]["speed_threshold"]

    def recognize_frame(self, tracks):
        """
        Recognizes actions in a frame.
        Args:
            tracks (list): list of detections in the frame (sv.STrack objects).
        """
        ar_results = {}

        if self.gathering_enabled:
            ar_results["gathering"] = self.recognize_gathering(tracks)
        if self.ss_enabled:
            ar_results["standing_still"] = self.recognize_standing_still(tracks)
        if self.fa_enabled:
            ar_results["fast_approach"] = self.recognize_fast_approach(tracks)

        return ar_results

    def annotate(self, frame, ar_results):
        """
        Annotates the frame with the results of the action recognition.
        Args:
            frame (np.array): frame to be annotated.
            ar_results (dict): dictionary containing the results of the action recognition.
        Returns:
            frame (np.array): annotated frame.
        """
        if self.gathering_enabled:
            frame = self.annotate_gathering(frame, ar_results["gathering"])

        # TODO: merge individual actions so that we only loop and annotate once
        if self.ss_enabled:
            frame = self.annotate_standing_still(frame, ar_results["standing_still"])

        if self.fa_enabled:
            frame = self.annotate_fast_approach(frame, ar_results["fast_approach"])

        return frame

    def recognize_gathering(self, tracks):
        """
        Recognizes gatherings in a frame by computing the normalized euclidean distance between all pairs of detections
        and conditioning pairs to have similar areas. Then, it finds the independent chains in the graph and computes
        the bounding box of each crowd.
        Args:
            tracks (list): list of detections in the frame (sv.STrack objects).
        Returns:
            results (dict): dictionary containing the results of the action recognition.
        """
        pairs = []
        # Iterate over all pairs of detections
        for i, j in combinations(range(len(tracks)), 2):
            det1 = tracks[int(i)]
            det2 = tracks[int(j)]

            # If both detections are people proceed to computation
            # TODO: maybe constrain on speed?
            if (det1.class_ids == 0) and (det2.class_ids == 0):
                distance, a1, a2 = self.compute_ned(det1, det2)

                # If distance is below threshold and the ratio between the areas is within the threshold add pair
                if distance <= self.distance_threshold and \
                        (self.area_threshold <= a1/a2 <= (1/self.area_threshold)):
                    pairs.append([i, j])

        # Find independent chains in the graph
        crowds = self.get_independent_chains(pairs)

        # For each crowd, compute the bounding box and store it in the results dictionary
        if len(crowds) > 0:
            results = {}
            for k, crowd in enumerate(crowds):
                crowd_box = self.compute_crowd_box(tracks, crowd)
                results[k] = crowd_box
            return results
        else:
            return None

    @staticmethod
    def compute_ned(det1, det2):
        """
        Computes the normalized euclidean distance between two detections using the center of mass of the bounding
        boxes, and the mean area of the bounding boxes as normalization factor.
        Args:
            det1 (sv.STrack): first detection.
            det2 (sv.STrack): second detection.
        Returns:
            normalized_distance (float): normalized euclidean distance between the two detections.
            a1 (float): area of the first bounding box.
            a2 (float): area of the second bounding box.
        """
        # Euclidean distance between center of masses
        distance = np.sqrt(np.sum((det1.mean[0:2] - det2.mean[0:2]) ** 2))
        # Mean area of the bounding boxes
        a1 = det1.tlwh[2] * det1.tlwh[3]
        a2 = det2.tlwh[2] * det2.tlwh[3]
        mean_area = (a1 + a2) / 2
        return distance/np.sqrt(mean_area), a1, a2

    @staticmethod
    def get_independent_chains(pairs):
        """
        Finds the independent chains in a graph, where each node is a detection and each edge is a pair of detections
        linked by the distance threshold.
        Args:
            pairs (list): list of pairs of detections.
        Returns:
            valid_chains (list): list of lists of detections, where each list is an independent chain.
        """
        # Initialize graph
        g = nx.Graph()
        g.add_edges_from(pairs)
        # Find connected components
        independent_chains = list(nx.connected_components(g))
        # Filter out chains having less than 3 elements
        valid_chains = [chain for chain in independent_chains if len(chain) > 2]
        return valid_chains

    @staticmethod
    def compute_crowd_box(tracks, crowd):
        """
        Computes the bounding box of a crowd of people.
        Args:
            tracks (list): list of detections in the frame (sv.STrack objects).
            crowd (list): list of detections that form a crowd.
        Returns:
            crowd_box (list): list containing the coordinates of the bounding box of the crowd.
        """
        # TODO: optimize this
        # Get the coordinates of the bounding boxes of the detections in the crowd
        crowd_boxes = [tracks[i].tlbr for i in crowd]
        # Compute the bounding box of the crowd
        crowd_box = [min([box[0] for box in crowd_boxes]),
                     min([box[1] for box in crowd_boxes]),
                     max([box[2] for box in crowd_boxes]),
                     max([box[3] for box in crowd_boxes])]
        # TODO maybe add more space to the bounding box, not narrow it down to the crowd
        return np.array(crowd_box)

    def recognize_standing_still(self, tracks):
        ss_results = {}
        for track in tracks:
            # TODO: maybe condition on seconds and not frames using average FPS of the video
            if track.tracklet_len > self.ss_frame_window and track.class_ids == 0:
                # TODO compute real speed with homograahy matrix
                #pixel_speed = np.sqrt(np.sum(track.mean[4:6]**2))
                pixel_speed = np.sqrt(np.sum(track.mean[4:]**2))
                # TODO: condition on height and aspect ratio of the bounding box
                if pixel_speed < self.ss_speed_threshold:
                    ss_results[track.track_id] = track.tlbr
        return ss_results if len(ss_results.keys()) > 0 else None

    def recognize_fast_approach(self, tracks):
        fa_results = {}
        valid_classes = [0, 1, 2]   # personnel, car, truck
        interest_point = np.array([1080, 960])   # TODO: not hardcoded
        for track in tracks:
            if track.class_ids in valid_classes:
                # TODO compute real speed with homograahy matrix
                pixel_speed = np.sqrt(np.sum(track.mean[4:]**2))
                if pixel_speed > self.fa_speed_threshold and track.mean[1] > 0:
                    distace_to_interest_point = np.sqrt(np.sum((track.mean[0:2] - interest_point) ** 2))
                    if distace_to_interest_point < self.fa_distance_threshold:
                        fa_results[track.track_id] = track.tlbr
        return fa_results if len(fa_results.keys()) > 0 else None

    @staticmethod
    def annotate_gathering(frame, crowd_results):
        """
        Annotates the frame with the results of the gathering recognition. Draws a bounding box around each crowd. Text
        is placed on the bottom right corner of the bounding box.
        Args:
            frame (np.array): frame to be annotated.
            crowd_results (dict): dictionary containing the results of the gathering recognition.
        Returns:
            frame (np.array): annotated frame.
        """
        if crowd_results is not None:
            for idx, bbox in crowd_results.items():
                x1, y1, x2, y2 = bbox.astype(int)

                cv2.rectangle(
                    img=frame,
                    pt1=(x1, y1),
                    pt2=(x2, y2),
                    color=background_color,
                    thickness=2,
                )

                text = f"G #{idx+1}"
                text_width, text_height = cv2.getTextSize(
                    text=text,
                    fontFace=font,
                    fontScale=font_scale,
                    thickness=font_thickness,
                )[0]

                # Text must be top right corner of the bounding box of the crowd but outside the frame
                text_x = x2 - text_padding - text_width
                text_y = y2 + text_padding + text_height

                text_background_x2 = x2
                text_background_x1 = x2 - 2 * text_padding - text_width

                text_background_y1 = y2
                text_background_y2 = y2 + 2 * text_padding + text_height

                cv2.rectangle(
                    img=frame,
                    pt1=(text_background_x1, text_background_y1),
                    pt2=(text_background_x2, text_background_y2),
                    color=background_color,
                    thickness=cv2.FILLED,
                )
                cv2.putText(
                    img=frame,
                    text=text,
                    org=(text_x, text_y),
                    fontFace=font,
                    fontScale=font_scale,
                    color=text_color,
                    thickness=font_thickness,
                    lineType=cv2.LINE_AA,
                )

        return frame

    @staticmethod
    def annotate_standing_still(frame, ss_results):
        if ss_results is not None:
            for idx, bbox in ss_results.items():
                x1, y1, x2, y2 = bbox.astype(int)

                text = f"SS"
                text_width, text_height = cv2.getTextSize(
                    text=text,
                    fontFace=font,
                    fontScale=font_scale,
                    thickness=font_thickness,
                )[0]

                # Text must be top right corner of the bounding box of the crowd but outside the frame
                text_x = x2 - text_padding - text_width
                text_y = y2 + text_padding + text_height

                text_background_x2 = x2
                text_background_x1 = x2 - 2 * text_padding - text_width

                text_background_y1 = y2
                text_background_y2 = y2 + 2 * text_padding + text_height

                cv2.rectangle(
                    img=frame,
                    pt1=(text_background_x1, text_background_y1),
                    pt2=(text_background_x2, text_background_y2),
                    color=background_color,
                    thickness=cv2.FILLED,
                )
                cv2.putText(
                    img=frame,
                    text=text,
                    org=(text_x, text_y),
                    fontFace=font,
                    fontScale=font_scale,
                    color=text_color,
                    thickness=font_thickness,
                    lineType=cv2.LINE_AA,
                )

        return frame

    @staticmethod
    def annotate_fast_approach(frame, fa_results):
        if fa_results is not None:
            for idx, bbox in fa_results.items():
                x1, y1, x2, y2 = bbox.astype(int)

                text = f"FA"
                text_width, text_height = cv2.getTextSize(
                    text=text,
                    fontFace=font,
                    fontScale=font_scale,
                    thickness=font_thickness,
                )[0]

                # Text must be top right corner of the bounding box of the crowd but outside the frame
                text_x = x2 - text_padding - text_width
                text_y = y2 + text_padding + text_height

                text_background_x2 = x2
                text_background_x1 = x2 - 2 * text_padding - text_width

                text_background_y1 = y2
                text_background_y2 = y2 + 2 * text_padding + text_height

                cv2.rectangle(
                    img=frame,
                    pt1=(text_background_x1, text_background_y1),
                    pt2=(text_background_x2, text_background_y2),
                    color=background_color,
                    thickness=cv2.FILLED,
                )
                cv2.putText(
                    img=frame,
                    text=text,
                    org=(text_x, text_y),
                    fontFace=font,
                    fontScale=font_scale,
                    color=text_color,
                    thickness=font_thickness,
                    lineType=cv2.LINE_AA,
                )

        return frame

