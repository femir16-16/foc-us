"""
Foc&US Core Detection Module
Analyzes posture and gaze from webcam frames using MediaPipe Tasks API.
"""
import cv2
import mediapipe as mp
import numpy as np
import time
from dataclasses import dataclass
from typing import Optional, List
from enum import IntEnum
from pathlib import Path

from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision.drawing_utils import DrawingSpec


# ── Enums ─────────────────────────────────────────────────────────────────────

class PostureLevel(IntEnum):
    GOOD         = 0
    WARNING      = 1
    BAD          = 2
    NO_DETECTION = 3


class AttentionLevel(IntEnum):
    FOCUSED      = 0
    DISTRACTED   = 1
    AWAY         = 2
    NO_DETECTION = 3


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class PostureResult:
    level: PostureLevel
    neck_lean: float       # avg_shoulder_y - nose_y; higher = better
    shoulder_tilt: float   # |left_y - right_y|; lower = better
    message: str


@dataclass
class GazeResult:
    level: AttentionLevel
    h_offset: float   # iris deviation from center (0=centered)
    ear: float        # eye aspect ratio
    message: str
    yaw: float = 0.0    # head left/right rotation, degrees
    pitch: float = 0.0  # head up/down rotation, degrees


@dataclass
class DetectionResult:
    posture: Optional[PostureResult]
    gaze: Optional[GazeResult]
    annotated_frame: np.ndarray
    face_detected: bool
    pose_detected: bool
    brightness: float = 255.0   # mean grey level of the frame, 0–255
    backlight_ratio: Optional[float] = None   # face brightness / background brightness


def backlight_ratio(gray: np.ndarray, bbox: tuple) -> Optional[float]:
    """Mean brightness of the face box divided by the rest of the frame (Should).
    Well below 1 means a bright window or lamp behind the user."""
    x0, y0, x1, y1 = bbox
    face = gray[y0:y1, x0:x1]
    n_face, n_all = face.size, gray.size
    if n_face == 0 or n_face >= n_all:
        return None
    bg_mean = (float(gray.sum()) - float(face.sum())) / (n_all - n_face)
    return float(face.mean()) / bg_mean if bg_mean > 1 else None


# ── Landmark index constants ──────────────────────────────────────────────────

class PoseLM:
    NOSE           = 0
    LEFT_EAR       = 7
    RIGHT_EAR      = 8
    LEFT_SHOULDER  = 11
    RIGHT_SHOULDER = 12


class FaceLM:
    # Right eye (from perspective of face)
    R_OUT, R_IN   = 33, 133
    R_TOP, R_BOT  = 159, 145
    R_TOP2, R_BOT2 = 158, 153
    # Left eye
    L_OUT, L_IN   = 263, 362
    L_TOP, L_BOT  = 386, 374
    L_TOP2, L_BOT2 = 385, 380
    # Iris centers (FaceLandmarker with refine_landmarks)
    R_IRIS = 468
    L_IRIS = 473


# ── Main detector ─────────────────────────────────────────────────────────────

class FocUSDetector:
    # Posture thresholds (normalized coordinates, 0–1)
    NECK_LEAN_GOOD = 0.17
    NECK_LEAN_WARN = 0.11
    SHOULDER_ASYM_WARN = 0.04
    SHOULDER_ASYM_BAD  = 0.08

    # Gaze thresholds  (higher = less sensitive)
    GAZE_H_DISTRACTED = 0.20
    GAZE_H_AWAY       = 0.30

    # Posture relative to the calibrated neck lean (FR-05)
    LEAN_RATIO_GOOD = 0.88
    LEAN_RATIO_WARN = 0.75

    # Head pose thresholds, degrees, relative to the calibrated direction
    YAW_DISTRACTED = 15.0
    YAW_AWAY       = 25.0
    PITCH_NEUTRAL  = 17.0   # fallback when no calibration: laptop cam below eye level
    PITCH_AWAY     = 25.0

    # Eye aspect ratio — below this = blink/closed
    EAR_BLINK = 0.20

    def __init__(self):
        models_dir = Path(__file__).parent.parent / "models"

        pose_opts = mp_vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=str(models_dir / "pose_landmarker_lite.task")),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.55,
            min_pose_presence_confidence=0.55,
            min_tracking_confidence=0.50,
        )
        self._pose = mp_vision.PoseLandmarker.create_from_options(pose_opts)

        face_opts = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(
                model_asset_path=str(models_dir / "face_landmarker.task")),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.55,
            min_face_presence_confidence=0.55,
            min_tracking_confidence=0.50,
            output_facial_transformation_matrixes=True,
        )
        self._face = mp_vision.FaceLandmarker.create_from_options(face_opts)

        self._du = mp_vision.drawing_utils
        self._PoseLM = mp_vision.PoseLandmark
        self._FaceCon = mp_vision.FaceLandmarksConnections
        self._PoseCon = mp_vision.PoseLandmarksConnections

        self._last_ts: int = 0   # ensures monotonically increasing timestamps
        self.notes_mode = False  # Should: looking down is not off-screen gaze
        self.clear_baseline()

    # ── Calibration (FR-05) ───────────────────────────────────────────────────

    def set_baseline(self, neck_lean: float, yaw: float, pitch: float):
        """Use the user's own upright posture and screen direction as reference."""
        self.baseline_lean = neck_lean if neck_lean > 0.02 else None
        self.yaw0, self.pitch0 = yaw, pitch

    def clear_baseline(self):
        self.baseline_lean = None
        self.yaw0, self.pitch0 = 0.0, self.PITCH_NEUTRAL

    # ── EAR ──────────────────────────────────────────────────────────────────

    def _ear(self, lms, out_i, in_i, top_i, bot_i, top2_i, bot2_i, w, h) -> float:
        def pt(i):
            l = lms[i]
            return np.array([l.x * w, l.y * h])
        p1, p4 = pt(out_i), pt(in_i)
        p2, p3 = pt(top_i), pt(top2_i)
        p5, p6 = pt(bot2_i), pt(bot_i)
        horiz = np.linalg.norm(p1 - p4)
        if horiz < 1e-6:
            return 0.3
        return (np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)) / (2.0 * horiz)

    # ── Posture analysis ──────────────────────────────────────────────────────

    def _analyze_posture(self, lms: list) -> PostureResult:
        nose    = lms[PoseLM.NOSE]
        l_sh    = lms[PoseLM.LEFT_SHOULDER]
        r_sh    = lms[PoseLM.RIGHT_SHOULDER]

        if (getattr(l_sh, 'visibility', 1.0) < 0.40 or
                getattr(r_sh, 'visibility', 1.0) < 0.40):
            return PostureResult(PostureLevel.NO_DETECTION, 0.0, 0.0,
                                 "Shoulders not visible — back up a bit")

        avg_sh_y       = (l_sh.y + r_sh.y) / 2.0
        neck_lean      = avg_sh_y - nose.y          # higher = better posture
        shoulder_tilt  = abs(l_sh.y - r_sh.y)

        if self.baseline_lean:
            ratio = neck_lean / self.baseline_lean
            lean_good = ratio >= self.LEAN_RATIO_GOOD
            lean_ok = ratio >= self.LEAN_RATIO_WARN
        else:
            lean_good = neck_lean >= self.NECK_LEAN_GOOD
            lean_ok = neck_lean >= self.NECK_LEAN_WARN

        if lean_good and shoulder_tilt < self.SHOULDER_ASYM_WARN:
            return PostureResult(PostureLevel.GOOD, neck_lean, shoulder_tilt,
                                 "Posture is good — keep it up!")

        if not lean_ok:
            return PostureResult(PostureLevel.BAD, neck_lean, shoulder_tilt,
                                 "Head dropping forward — sit up straight!")

        if shoulder_tilt >= self.SHOULDER_ASYM_BAD:
            return PostureResult(PostureLevel.BAD, neck_lean, shoulder_tilt,
                                 "Shoulders uneven — straighten up!")

        msg = "Straighten your back slightly" \
              if shoulder_tilt < self.SHOULDER_ASYM_WARN else "Level your shoulders"
        return PostureResult(PostureLevel.WARNING, neck_lean, shoulder_tilt, msg)

    # ── Gaze analysis ────────────────────────────────────────────────────────

    @staticmethod
    def _head_angles(matrix) -> tuple[float, float]:
        """Yaw and pitch in degrees from the 4x4 facial transformation matrix."""
        if matrix is None:
            return 0.0, 0.0
        R = np.asarray(matrix)[:3, :3]
        pitch = np.degrees(np.arctan2(R[2, 1], R[2, 2]))
        yaw   = np.degrees(np.arctan2(-R[2, 0], np.hypot(R[2, 1], R[2, 2])))
        return float(yaw), float(pitch)

    def _analyze_gaze(self, lms: list, w: int, h: int,
                      yaw: float, pitch: float) -> GazeResult:
        has_iris = len(lms) >= 478

        r_ear = self._ear(lms, FaceLM.R_OUT, FaceLM.R_IN, FaceLM.R_TOP, FaceLM.R_BOT,
                          FaceLM.R_TOP2, FaceLM.R_BOT2, w, h)
        l_ear = self._ear(lms, FaceLM.L_OUT, FaceLM.L_IN, FaceLM.L_TOP, FaceLM.L_BOT,
                          FaceLM.L_TOP2, FaceLM.L_BOT2, w, h)
        ear = (r_ear + l_ear) / 2.0

        def result(level, h_off, msg):
            return GazeResult(level, h_off, ear, msg, yaw, pitch)

        # Head direction is checked first: when the head turns, the eyes turn
        # with it and the iris stays centered, so iris alone can't see this.
        d_yaw = yaw - self.yaw0
        if abs(d_yaw) > self.YAW_AWAY:
            return result(AttentionLevel.AWAY, 0.0, "Head turned away from screen")
        if not self.notes_mode and abs(pitch - self.pitch0) > self.PITCH_AWAY:
            return result(AttentionLevel.AWAY, 0.0, "Head tilted away from screen")

        if ear < self.EAR_BLINK:
            return result(AttentionLevel.FOCUSED, 0.0, "Blinking")

        if not has_iris:
            nose   = lms[4]
            h_off  = abs(nose.x - 0.5)
        else:
            r_iris = lms[FaceLM.R_IRIS]
            l_iris = lms[FaceLM.L_IRIS]
            r_out  = lms[FaceLM.R_OUT];  r_in = lms[FaceLM.R_IN]
            l_out  = lms[FaceLM.L_OUT];  l_in = lms[FaceLM.L_IN]

            # Corner order flips between eyes, so normalize by left/right edge
            def iris_ratio(iris, a, b):
                left, right = min(a.x, b.x), max(a.x, b.x)
                eye_w = right - left
                return (iris.x - left) / eye_w if eye_w > 1e-4 else 0.5

            h_off = (abs(iris_ratio(r_iris, r_out, r_in) - 0.5) +
                     abs(iris_ratio(l_iris, l_out, l_in) - 0.5)) / 2.0

        if h_off > self.GAZE_H_AWAY:
            return result(AttentionLevel.AWAY, h_off, "Eyes looking away from screen")
        if h_off > self.GAZE_H_DISTRACTED or abs(d_yaw) > self.YAW_DISTRACTED:
            return result(AttentionLevel.DISTRACTED, h_off, "Gaze drifting...")
        return result(AttentionLevel.FOCUSED, h_off, "Focused")

    # ── Overlay drawing ───────────────────────────────────────────────────────

    @staticmethod
    def _draw_status_overlay(frame: np.ndarray,
                             posture: Optional[PostureResult],
                             gaze: Optional[GazeResult]) -> np.ndarray:
        COLOR = {
            PostureLevel.GOOD:         (76,  175,  80),
            PostureLevel.WARNING:      (255, 152,   0),
            PostureLevel.BAD:          (60,   67, 244),
            PostureLevel.NO_DETECTION: (120, 120, 120),
            AttentionLevel.FOCUSED:      (76,  175,  80),
            AttentionLevel.DISTRACTED:   (0,  160, 255),
            AttentionLevel.AWAY:         (60,   67, 244),
            AttentionLevel.NO_DETECTION: (120, 120, 120),
        }

        def pill(y, label, val, msg, color):
            cv2.rectangle(frame, (8, y), (300, y + 42), (18, 18, 38), cv2.FILLED)
            cv2.rectangle(frame, (8, y), (300, y + 42), color, 2)
            cv2.putText(frame, f"{label}: {val}",
                        (14, y + 16), cv2.FONT_HERSHEY_SIMPLEX,
                        0.47, color, 1, cv2.LINE_AA)
            cv2.putText(frame, msg,
                        (14, y + 34), cv2.FONT_HERSHEY_SIMPLEX,
                        0.38, (190, 190, 200), 1, cv2.LINE_AA)

        p_level = posture.level if posture else PostureLevel.NO_DETECTION
        g_level = gaze.level   if gaze    else AttentionLevel.NO_DETECTION

        pill(8,  "POSTURE",   p_level.name,
             posture.message if posture else "No detection",
             COLOR[p_level])
        pill(56, "ATTENTION", g_level.name,
             gaze.message if gaze else "No detection",
             COLOR[g_level])

        if gaze:
            h = frame.shape[0]
            cv2.putText(frame,
                        f"yaw {gaze.yaw:+.0f}  pitch {gaze.pitch:+.0f}  "
                        f"iris {gaze.h_offset:.2f}  ear {gaze.ear:.2f}",
                        (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (220, 220, 220), 1, cv2.LINE_AA)

        return frame

    # ── Public API ────────────────────────────────────────────────────────────

    def process_frame(self, frame: np.ndarray) -> DetectionResult:
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        backlight = None
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # Monotonically increasing timestamp
        ts_ms = int(time.monotonic() * 1000)
        if ts_ms <= self._last_ts:
            ts_ms = self._last_ts + 1
        self._last_ts = ts_ms

        pose_res = self._pose.detect_for_video(mp_img, ts_ms)
        face_res = self._face.detect_for_video(mp_img, ts_ms)

        annotated      = frame.copy()
        posture_result = None
        gaze_result    = None

        # Draw pose skeleton
        if pose_res.pose_landmarks:
            pose_lms = pose_res.pose_landmarks[0]
            self._du.draw_landmarks(
                annotated, pose_lms,
                self._PoseCon.POSE_LANDMARKS,
                landmark_drawing_spec=DrawingSpec(color=(0, 230, 200),
                                                  thickness=2, circle_radius=2),
                connection_drawing_spec=DrawingSpec(color=(0, 200, 120),
                                                    thickness=2),
            )
            posture_result = self._analyze_posture(pose_lms)

        # Draw iris / face
        if face_res.face_landmarks:
            face_lms = face_res.face_landmarks[0]
            iris_conns = (list(self._FaceCon.FACE_LANDMARKS_LEFT_IRIS) +
                          list(self._FaceCon.FACE_LANDMARKS_RIGHT_IRIS))
            self._du.draw_landmarks(
                annotated, face_lms,
                iris_conns,
                landmark_drawing_spec=DrawingSpec(color=(0, 160, 255),
                                                  thickness=1, circle_radius=1),
                connection_drawing_spec=DrawingSpec(color=(0, 120, 200),
                                                    thickness=1),
            )
            matrices = face_res.facial_transformation_matrixes
            yaw, pitch = self._head_angles(matrices[0] if matrices else None)
            gaze_result = self._analyze_gaze(face_lms, w, h, yaw, pitch)
            xs = [lm.x for lm in face_lms]
            ys = [lm.y for lm in face_lms]
            clamp = lambda v, hi: max(0, min(hi, int(v)))
            backlight = backlight_ratio(gray, (clamp(min(xs) * w, w), clamp(min(ys) * h, h),
                                               clamp(max(xs) * w, w), clamp(max(ys) * h, h)))
        elif pose_res.pose_landmarks:
            # Body in frame but face not found: head is turned too far
            gaze_result = GazeResult(AttentionLevel.AWAY, 0.0, 0.0,
                                     "Face not visible — looking away")

        self._draw_status_overlay(annotated, posture_result, gaze_result)

        return DetectionResult(
            posture=posture_result,
            gaze=gaze_result,
            annotated_frame=annotated,
            face_detected=bool(face_res.face_landmarks),
            pose_detected=bool(pose_res.pose_landmarks),
            brightness=brightness,
            backlight_ratio=backlight,
        )

    def release(self):
        self._pose.close()
        self._face.close()
