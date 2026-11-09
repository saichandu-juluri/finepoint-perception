"""The ChArUco board of a project, as OpenCV objects, with the detector settings calib.py proved on screen boards.

    from camrig.board import detector
    board, det = detector(project.board)
    corners, ids, marker_corners, marker_ids = det.detectBoard(gray)

calib.charuco("main") stays for the old steps; it is the same board as the 2 Oct project's [board].
"""
from cv2 import aruco


def detector(b):
    dictionary = aruco.getPredefinedDictionary(getattr(aruco, b.dictionary))
    board = aruco.CharucoBoard(b.squares, b.square_mm, b.marker_mm, dictionary)
    params = aruco.DetectorParameters()
    params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
    params.minMarkerPerimeterRate = 0.015        # default 0.03 rejects 20 px markers
    params.perspectiveRemovePixelPerCell = 8     # read each marker bit from its cell's centre: a bright screen blooms
    params.perspectiveRemoveIgnoredMarginPerCell = 0.35
    return board, aruco.CharucoDetector(board, aruco.CharucoParameters(), params)


def corners_seen(det, gray) -> int:
    """inner corners found in one grey picture, 0 when the board is not there"""
    _, ids, _, _ = det.detectBoard(gray)
    return 0 if ids is None else len(ids)

