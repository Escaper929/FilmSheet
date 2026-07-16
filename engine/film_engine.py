# -*- coding: utf-8 -*-

class Strict135FilmEngine:
    def __init__(self, dpi=300):
        self.dpi = dpi
        self.px_per_mm = dpi / 25.4
        # 物理常数 (单位 mm) 依据 SMPTE 标准
        self.FILM_WIDTH_MM = 35.0
        self.FRAME_WIDTH_MM = 36.0
        self.FRAME_HEIGHT_MM = 24.0
        self.EDGE_TO_PERF_MM = 2.01
        self.PERF_DIM_Y_MM = 2.794
        self.PERF_DIM_X_KS_MM = 1.981
        self.PERF_DIM_X_BH_MM = 1.854
        self.PERF_RADIUS_MM = 0.508
        self.BH_CURVE_DEPTH_MM = 0.35
        self.PITCH_KS_MM = 4.750
        self.PITCH_BH_MM = 4.740

    def mm_to_px(self, mm):
        return int(round(mm * self.px_per_mm))

    def determine_perf_type(self, shooting_info, choice):
        if "KS" in choice or "民用" in choice:
            return "KS"
        if "BH" in choice or "电影" in choice:
            return "BH"
        cinema_keywords = ["5207", "5294", "5219", "5203", "vision3", "ektachrome", "motion", "cinema"]
        return "BH" if any(k in shooting_info.lower() for k in cinema_keywords) else "KS"

    def draw_single_perf(self, draw, cx, cy, perf_fill, perf_type="KS"):
        h = self.mm_to_px(self.PERF_DIM_Y_MM)
        if perf_type == "KS":
            w = self.mm_to_px(self.PERF_DIM_X_KS_MM)
            r = self.mm_to_px(self.PERF_RADIUS_MM)
            draw.rounded_rectangle(
                [cx - w//2, cy - h//2, cx + w//2, cy + h//2],
                radius=r, fill=perf_fill, outline=None
            )
        else:
            w = self.mm_to_px(self.PERF_DIM_X_BH_MM)
            cd = self.mm_to_px(self.BH_CURVE_DEPTH_MM)
            draw.rectangle(
                [cx - w//2, cy - h//2 + cd, cx + w//2, cy + h//2 - cd],
                fill=perf_fill, outline=None
            )
            draw.ellipse(
                [cx - w//2, cy - h//2, cx + w//2, cy - h//2 + 2*cd],
                fill=perf_fill, outline=None
            )
            draw.ellipse(
                [cx - w//2, cy + h//2 - 2*cd, cx + w//2, cy + h//2],
                fill=perf_fill, outline=None
            )

    def get_perf_pitch(self, perf_type):
        return self.PITCH_KS_MM if perf_type == "KS" else self.PITCH_BH_MM