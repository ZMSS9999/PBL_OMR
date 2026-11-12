# pyright: basic
"""v2 verify_sheet 테스트: 가짜 분류기로 read dict 가 어떻게 바뀌는지 확인 (실제 CNN 불필요).

실행 (backend 폴더에서):  python -m unittest discover -s dual_verify_v2/tests -t .
"""
import copy
import unittest
from unittest import mock

import numpy as np

import dual_verify_v2
from dual_verify_v2 import config
from dual_verify_v2.config import BLANK as B, STANDARD as S, TAPED as T, THIN

DPI = 200
N_DIGITS = 2
OK = (B, 0.99)          # CNN: 빈칸, 확신 높음


def make_layout(n_q=3, n_c=3, with_sid=False):
    """문항 n_q개 × 선지 n_c개 (칸 간격 10mm, 반지름 2mm). with_sid면 학번 칸(자리마다 0~9 세로 나열)도 추가."""
    qs = [{"q": qi + 1, "choices": [
        {"q": qi + 1, "choice": ci + 1, "center_mm": [20.0 + 10 * ci, 20.0 + 10 * qi], "radius_mm": 2.0}
        for ci in range(n_c)]} for qi in range(n_q)]
    layout = {"mcq": {"questions": qs}}
    if with_sid:
        layout["student_id"] = {"digits": N_DIGITS, "bubbles": [
            {"digit_index": di, "value": v, "center_mm": [60.0 + 10 * di, 20.0 + 5 * v], "radius_mm": 1.75}
            for di in range(N_DIGITS) for v in range(10)]}
    return layout


def make_read(fills, statuses=None, marked_min=0.60):
    """fills: 문항별 선지 채움 비율 목록. 규칙 기반(read.py)이 만든 것처럼 read dict 를 흉내 냄."""
    mcq = []
    for i, f in enumerate(fills):
        marked = [j + 1 for j, v in enumerate(f) if v >= marked_min]
        st = (statuses or {}).get(i + 1) or ("blank" if not marked else "ok" if len(marked) == 1 else "multi")
        mcq.append({"q": i + 1, "answer": None if st == "ambiguous" else marked, "status": st,
                    "needs_review": st == "ambiguous", "fill": f, "cells": []})
    return {"student_id": {"needs_review": False, "text": "1", "raw": "1"}, "mcq": mcq,
            "summary": {"status_counts": {}, "needs_review": any(q["needs_review"] for q in mcq)},
            "offsets_mm": {"mcq": [[0.0, 0.0]], "student_id": [0.0, 0.0]},
            "read_config": {"marked_min": marked_min, "ambiguous_min": 0.25, "dpi": DPI}}


def add_sid_read(read, picks):
    """picks: 자리별 규칙 기반이 마킹한 숫자 (None이면 무응답). read["student_id"] 를 판독 결과처럼 채움."""
    digits = []
    for di, p in enumerate(picks):
        digits.append({"digit_index": di, "value": [] if p is None else [p],
                       "status": "blank" if p is None else "ok",
                       "fill": [0.9 if v == p else 0.02 for v in range(10)]})
    raw = "".join(str(d["value"][0]) if d["value"] else "?" for d in digits)
    ok = all(d["status"] == "ok" for d in digits)
    read["student_id"] = {"text": raw if ok else None, "raw": raw, "status": "ok" if ok else "review",
                          "needs_review": not ok, "digits": digits}
    return read


def fake(grid):
    """grid: 판정 단위(문항·자리)별 선지 CNN 결과 [(클래스, 신뢰도) / None]. 규칙 기반이 확정한 칸은 None.
    분류기는 None 이 아닌 값만 순서대로 돌려주고, 요청받은 칸 수가 그와 다르면 실패시킴
    (= 60% 이상 칸은 CNN에 보내지 않는다는 것을 함께 검사)."""
    seq = [p for grp in grid for p in grp if p is not None]

    def clf(crops):
        assert len(crops) == len(seq), f"CNN에 {len(crops)}칸을 보냈는데 예상은 {len(seq)}칸"
        return seq
    return clf


class VerifySheet(unittest.TestCase):
    def setUp(self):
        self.layout = make_layout()
        self.img = np.full((400, 400), 255, np.uint8)

    def run_verify(self, read, grid):
        return dual_verify_v2.verify_sheet(read, self.img, self.layout, classifier=fake(grid))

    def test_60퍼센트_이상_칸은_CNN에_보내지_않고_답이_확정됨(self):
        read = make_read([[0.9, 0, 0], [0, 0.9, 0], [0, 0, 0]])
        out = self.run_verify(read, [[None, OK, OK], [OK, None, OK], [OK, OK, OK]])
        self.assertEqual([q["answer"] for q in out["mcq"]], [[1], [2], []])
        self.assertFalse(out["summary"]["needs_review"])
        self.assertEqual(out["dual_verify"], {"version": "v2", "marked_min": 0.6, "rule_method": "fill",
                                              "confidence_min": 0.9})

    def test_신뢰도_미만_칸이_있으면_그_문항만_검수(self):
        read = make_read([[0.9, 0, 0], [0, 0.9, 0]])
        self.layout = make_layout(n_q=2)
        out = self.run_verify(read, [[None, OK, OK], [OK, None, (B, 0.85)]])
        self.assertEqual(out["mcq"][0]["answer"], [1])
        self.assertIsNone(out["mcq"][1]["answer"])
        self.assertTrue(out["mcq"][1]["needs_review"])
        self.assertEqual(out["mcq"][1]["status"], "review")
        self.assertEqual(out["summary"]["dual_verify"]["review_questions"], [2])
        self.assertEqual(out["mcq"][1]["dual"]["reasons"][0]["confidence"], 0.85)

    def test_60퍼센트_미만에서_CNN이_마킹으로_보면_답에_포함(self):
        read = make_read([[0.9, 0.1, 0]])
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[None, (THIN, 0.95), OK]])         # 2번 선지: 10%인데 CNN은 얇게 채움
        self.assertEqual(out["mcq"][0]["answer"], [1, 2])
        self.assertEqual(out["mcq"][0]["status"], "multi")

    def test_애매였던_문항이_CNN_빈칸이면_무응답으로_확정(self):
        read = make_read([[0.0, 0.56, 0.0]], statuses={1: "ambiguous"})
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[OK, (B, 0.97), OK]])
        self.assertEqual(out["mcq"][0]["answer"], [])
        self.assertEqual(out["mcq"][0]["status"], "blank")
        self.assertFalse(out["mcq"][0]["needs_review"])

    def test_애매였던_문항이_CNN_마킹이면_그_답으로_확정(self):
        read = make_read([[0.0, 0.56, 0.0]], statuses={1: "ambiguous"})
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[OK, (THIN, 0.97), OK]])
        self.assertEqual(out["mcq"][0]["answer"], [2])
        self.assertEqual(out["mcq"][0]["status"], "ok")

    def test_수정테이프_있고_마킹_없으면_검수(self):
        read = make_read([[0.0, 0.0, 0.0]])
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[OK, (T, 0.98), OK]])
        self.assertTrue(out["mcq"][0]["needs_review"])
        with mock.patch.object(config, "TAPE_WITHOUT_MARK_REVIEW", False):
            out = self.run_verify(read, [[OK, (T, 0.98), OK]])
            self.assertFalse(out["mcq"][0]["needs_review"])

    def test_CONFIDENCE_MIN_설정을_바꾸면_따라감(self):
        read = make_read([[0.0, 0.0, 0.0]])
        self.layout = make_layout(n_q=1)
        grid = [[OK, (B, 0.85), OK]]
        self.assertTrue(self.run_verify(read, grid)["mcq"][0]["needs_review"])      # 기본 0.9: 0.85는 검수
        with mock.patch.object(config, "CONFIDENCE_MIN", 0.80):
            self.assertFalse(self.run_verify(read, grid)["mcq"][0]["needs_review"])  # 0.8: 통과

    def test_기준값은_read_config에서_읽음(self):
        read = make_read([[0.55, 0, 0]], marked_min=0.50)
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[None, OK, OK]])                   # 50% 기준이면 55%는 규칙 기반 마킹 → CNN에 안 보냄
        self.assertEqual(out["mcq"][0]["answer"], [1])

    def test_원본_read는_바뀌지_않음(self):
        read = make_read([[0.9, 0, 0]])
        before = copy.deepcopy(read)
        self.layout = make_layout(n_q=1)
        self.run_verify(read, [[None, (B, 0.5), OK]])
        self.assertEqual(read, before)

    def test_분류_결과_수가_다르면_예외(self):
        read = make_read([[0.0, 0.0, 0.0]])
        self.layout = make_layout(n_q=1)
        with self.assertRaises(ValueError):
            dual_verify_v2.verify_sheet(read, self.img, self.layout, classifier=lambda crops: [OK])


class VerifyStudentId(unittest.TestCase):
    def setUp(self):
        self.layout = make_layout(n_q=1, with_sid=True)
        self.img = np.full((700, 700), 255, np.uint8)

    def base(self, picks):
        return add_sid_read(make_read([[0.9, 0, 0]]), picks)

    def sid_grid(self, picks, override=None):
        """학번 자리별 CNN 결과: 규칙 기반이 마킹한 숫자는 None, 나머지는 빈칸(확신 높음). override={(자리, 숫자): 결과}"""
        grid = []
        for di, p in enumerate(picks):
            row = [None if v == p else OK for v in range(10)]
            for (edi, ev), res in (override or {}).items():
                if edi == di:
                    row[ev] = res
            grid.append(row)
        return grid

    def run_verify(self, read, picks, override=None):
        grid = [[None, OK, OK]] + self.sid_grid(picks, override)
        return dual_verify_v2.verify_sheet(read, self.img, self.layout, classifier=fake(grid))

    def test_모두_통과하면_학번이_읽힘(self):
        out = self.run_verify(self.base([1, 2]), [1, 2])
        self.assertEqual(out["student_id"]["text"], "12")
        self.assertEqual(out["student_id"]["status"], "ok")
        self.assertFalse(out["summary"]["needs_review"])

    def test_학번_칸도_60퍼센트_이상은_CNN에_보내지_않음(self):
        out = self.run_verify(self.base([1, 2]), [1, 2])               # fake 가 칸 수(1 + 9 + 9 + 객관식 2)를 검사함
        self.assertEqual(out["summary"]["dual_verify"]["review_digits"], [])

    def test_신뢰도_미만이면_그_자리만_검수(self):
        out = self.run_verify(self.base([1, 2]), [1, 2], override={(1, 5): (B, 0.8)})
        self.assertIsNone(out["student_id"]["text"])
        self.assertEqual(out["student_id"]["raw"], "1?")
        self.assertTrue(out["student_id"]["needs_review"])
        self.assertEqual(out["student_id"]["digits"][1]["status"], "review")
        self.assertEqual(out["summary"]["dual_verify"]["review_digits"], [1])
        self.assertTrue(out["summary"]["needs_review"])

    def test_60퍼센트_미만_칸을_CNN이_마킹으로_보면_복수_표시로_검수(self):
        out = self.run_verify(self.base([1, 2]), [1, 2], override={(0, 7): (THIN, 0.97)})
        self.assertEqual(out["student_id"]["digits"][0]["value"], [1, 7])
        self.assertEqual(out["student_id"]["digits"][0]["status"], "multi")
        self.assertIsNone(out["student_id"]["text"])

    def test_무응답_자리가_있으면_학번은_검수(self):
        out = self.run_verify(self.base([None, 2]), [None, 2])
        self.assertEqual(out["student_id"]["raw"], "?2")
        self.assertIsNone(out["student_id"]["text"])
        self.assertTrue(out["student_id"]["needs_review"])

    def test_INCLUDE_STUDENT_ID를_끄면_학번은_그대로(self):
        read = self.base([1, 2])
        before = copy.deepcopy(read["student_id"])
        with mock.patch.object(config, "INCLUDE_STUDENT_ID", False):
            out = dual_verify_v2.verify_sheet(read, self.img, self.layout, classifier=fake([[None, OK, OK]]))
        self.assertEqual(out["student_id"], before)

    def test_학번_판독결과에_digits가_없으면_객관식만_판정(self):
        read = make_read([[0.9, 0, 0]])                                  # student_id 에 digits 없음
        out = dual_verify_v2.verify_sheet(read, self.img, self.layout, classifier=fake([[None, OK, OK]]))
        self.assertEqual(out["mcq"][0]["answer"], [1])


def dark_read(fills, covs, overrides=None, solid=0.60):
    """팀원의 새 판독(잉크 농도 방식) 결과를 흉내 냄: read_config 에 method="darkness"/solid_min, 문항에 cov·reason.
    fills = g(평균 잉크 농도) 목록. overrides = {문항 번호: (status, reason)} 로 보류·반사·과노출 같은 상태를 지정."""
    read = make_read(fills, marked_min=solid)
    read["read_config"].update({"method": "darkness", "solid_min": solid})
    for q, c in zip(read["mcq"], covs):
        q["cov"] = c
        q["reason"] = ""
    for qn, (st, why) in (overrides or {}).items():
        q = read["mcq"][qn - 1]
        q.update({"status": st, "reason": why, "needs_review": st == "ambiguous"})
        q["answer"] = None if st == "ambiguous" else q["answer"]
    return read


class VerifySheetDarkness(unittest.TestCase):
    """팀원의 잉크 농도 방식 판독(solid_min, cov, reason)과 맞물리는 동작."""

    def setUp(self):
        self.layout = make_layout(n_q=1)
        self.img = np.full((400, 400), 255, np.uint8)

    def run_verify(self, read, grid):
        return dual_verify_v2.verify_sheet(read, self.img, self.layout, classifier=fake(grid))

    def test_마킹_기준은_solid_min을_읽음(self):
        read = dark_read([[0.55, 0, 0]], [[0, 0, 0]], solid=0.50)       # marked_min 은 0.6, solid_min 은 0.5
        out = self.run_verify(read, [[None, OK, OK]])                   # 0.55 는 규칙 기반 마킹 → CNN 에 안 보냄
        self.assertEqual(out["mcq"][0]["answer"], [1])
        self.assertEqual(out["dual_verify"]["marked_min"], 0.5)
        self.assertEqual(out["dual_verify"]["rule_method"], "darkness")

    def test_cov가_기준_이상이면_g가_낮아도_규칙기반_마킹(self):
        read = dark_read([[0.30, 0, 0]], [[0.80, 0, 0]])
        out = self.run_verify(read, [[None, OK, OK]])
        self.assertEqual(out["mcq"][0]["answer"], [1])

    def test_얇게채움_보류_문항을_CNN으로_다시_판정하고_reason을_지움(self):
        read = dark_read([[0.20, 0, 0]], [[0.40, 0, 0]], overrides={1: ("ambiguous", "partial")})
        out = self.run_verify(read, [[(THIN, 0.97), OK, OK]])
        self.assertEqual(out["mcq"][0]["answer"], [1])
        self.assertFalse(out["mcq"][0]["needs_review"])
        self.assertEqual(out["mcq"][0]["reason"], "")

    def test_동그라미_보류_문항을_CNN이_빈칸으로_보면_무응답으로_확정(self):
        read = dark_read([[0.02, 0, 0]], [[0, 0, 0]], overrides={1: ("ambiguous", "ring")})
        out = self.run_verify(read, [[(B, 0.96), OK, OK]])
        self.assertEqual(out["mcq"][0]["answer"], [])
        self.assertEqual(out["mcq"][0]["status"], "blank")

    def test_반사_과노출_문항은_CNN에_보내지_않고_검수_그대로(self):
        read = dark_read([[0.9, 0, 0]], [[0.9, 0, 0]], overrides={1: ("ambiguous", "glare")})
        out = self.run_verify(read, [[]])                               # fake 가 CNN 호출이 0칸인지 검사함
        self.assertIsNone(out["mcq"][0]["answer"])
        self.assertTrue(out["mcq"][0]["needs_review"])
        self.assertEqual(out["mcq"][0]["reason"], "glare")
        self.assertEqual(out["mcq"][0]["dual"]["kept"], "glare")
        self.assertEqual(out["summary"]["dual_verify"]["kept_questions"], [1])
        self.assertTrue(out["summary"]["needs_review"])
        read = dark_read([[0, 0, 0]], [[0, 0, 0]], overrides={1: ("ambiguous", "overexposed")})
        self.assertTrue(self.run_verify(read, [[]])["mcq"][0]["needs_review"])

    def test_v2가_검수로_보낸_문항에_reason을_적음(self):
        read = dark_read([[0.9, 0, 0]], [[0.9, 0, 0]])
        out = self.run_verify(read, [[None, (B, 0.5), OK]])
        self.assertEqual(out["mcq"][0]["reason"], "cnn_low_conf")
        read = dark_read([[0, 0, 0]], [[0, 0, 0]])
        out = self.run_verify(read, [[OK, (T, 0.98), OK]])
        self.assertEqual(out["mcq"][0]["reason"], "cnn_tape_no_mark")

    def test_지운흔적_표시가_있던_문항은_통과하면_reason이_비워짐(self):
        read = dark_read([[0.9, 0, 0]], [[0.9, 0, 0]])
        read["mcq"][0]["reason"] = "erasure"
        out = self.run_verify(read, [[None, OK, OK]])
        self.assertEqual(out["mcq"][0]["reason"], "")

    def test_학번_자리의_과노출_보류는_CNN에_보내지_않고_검수_그대로(self):
        self.layout = make_layout(n_q=1, with_sid=True)
        self.img = np.full((700, 700), 255, np.uint8)
        read = add_sid_read(dark_read([[0.9, 0, 0]], [[0.9, 0, 0]]), [1, 2])
        d1 = read["student_id"]["digits"][1]
        d1.update({"status": "ambiguous", "value": None, "reason": "overexposed"})
        d0 = read["student_id"]["digits"][0]
        d0["reason"] = ""
        grid = [[None, OK, OK], [None if v == 1 else OK for v in range(10)], []]   # 둘째 자리는 CNN 호출 없음
        out = self.run_verify(read, grid)
        self.assertEqual(out["student_id"]["raw"], "1?")
        self.assertIsNone(out["student_id"]["text"])
        self.assertEqual(out["summary"]["dual_verify"]["kept_digits"], [1])
        self.assertEqual(out["student_id"]["digits"][1]["dual"]["kept"], "overexposed")


if __name__ == "__main__":
    unittest.main()
