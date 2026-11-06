# pyright: basic
"""verify_sheet 테스트: 가짜 분류기로 read dict 가 어떻게 바뀌는지 확인 (실제 CNN 불필요).

실행 (backend 폴더에서):  python -m unittest discover -s dual_verify/tests -t .
"""
import copy
import unittest

import numpy as np

import dual_verify
from dual_verify.config import BLANK as B, STANDARD as S, TAPED as T, THIN

DPI = 200


def make_layout(n_q=3, n_c=3):
    """문항 n_q개 × 선지 n_c개 짜리 작은 양식 (칸 간격 10mm, 반지름 2mm)."""
    qs = []
    for qi in range(n_q):
        qs.append({"q": qi + 1, "choices": [
            {"q": qi + 1, "choice": ci + 1, "center_mm": [20.0 + 10 * ci, 20.0 + 10 * qi], "radius_mm": 2.0}
            for ci in range(n_c)]})
    return {"mcq": {"questions": qs}}


def make_read(fills, statuses=None, marked_min=0.60):
    """fills: 문항별 선지 채움 비율 목록. 규칙 기반이 만든 것처럼 read dict 를 흉내 냄."""
    mcq = []
    for i, f in enumerate(fills):
        marked = [j + 1 for j, v in enumerate(f) if v >= marked_min]
        st = (statuses or {}).get(i + 1) or ("blank" if not marked else "ok" if len(marked) == 1 else "multi")
        mcq.append({"q": i + 1, "answer": None if st == "ambiguous" else marked, "status": st,
                    "needs_review": st == "ambiguous", "fill": f, "cells": []})
    return {"student_id": {"needs_review": False, "text": "1", "raw": "1"},
            "mcq": mcq,
            "summary": {"status_counts": {}, "needs_review": any(q["needs_review"] for q in mcq)},
            "offsets_mm": {"mcq": [[0.0, 0.0]], "student_id": [0.0, 0.0]},
            "read_config": {"marked_min": marked_min, "ambiguous_min": 0.25, "dpi": DPI}}


def fake(classes):
    """문항·선지 순서대로 정해 둔 클래스 번호를 돌려주는 가짜 분류기."""
    flat = [c for q in classes for c in q]
    return lambda crops: flat[:len(crops)]


class VerifySheet(unittest.TestCase):
    def setUp(self):
        self.layout = make_layout()
        self.img = np.full((400, 400), 255, np.uint8)

    def run_verify(self, read, classes):
        return dual_verify.verify_sheet(read, self.img, self.layout, classifier=fake(classes))

    def test_모두_일치하면_변화없이_통과(self):
        read = make_read([[0.9, 0, 0], [0, 0.9, 0], [0, 0, 0]])
        out = self.run_verify(read, [[S, B, B], [B, S, B], [B, B, B]])
        self.assertEqual([q["answer"] for q in out["mcq"]], [[1], [2], []])
        self.assertFalse(out["summary"]["needs_review"])
        self.assertEqual(out["summary"]["dual_verify"]["review_questions"], [])

    def test_불일치_문항만_검수로_바뀜(self):
        read = make_read([[0.9, 0, 0], [0, 0.9, 0], [0, 0, 0]])
        out = self.run_verify(read, [[S, B, B], [B, B, B], [B, B, B]])   # 2번 문항: CNN이 빈칸
        self.assertEqual(out["mcq"][0]["answer"], [1])
        self.assertIsNone(out["mcq"][1]["answer"])
        self.assertTrue(out["mcq"][1]["needs_review"])
        self.assertTrue(out["summary"]["needs_review"])
        self.assertEqual(out["summary"]["dual_verify"]["review_questions"], [2])
        self.assertEqual(out["mcq"][1]["dual"]["rule_answer"], [2])

    def test_애매였던_문항이_CNN과_일치하면_확정으로_되돌림(self):
        read = make_read([[0.0, 0.56, 0.0]], statuses={1: "ambiguous"})
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[B, B, B]])        # 56%는 미마킹, CNN도 빈칸
        self.assertEqual(out["mcq"][0]["answer"], [])
        self.assertEqual(out["mcq"][0]["status"], "blank")
        self.assertFalse(out["summary"]["needs_review"])

    def test_애매였고_CNN이_마킹이면_검수_유지(self):
        read = make_read([[0.0, 0.56, 0.0]], statuses={1: "ambiguous"})
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[B, THIN, B]])
        self.assertIsNone(out["mcq"][0]["answer"])
        self.assertTrue(out["mcq"][0]["needs_review"])

    def test_수정테이프_있고_마킹_없으면_검수(self):
        read = make_read([[0.0, 0.0, 0.0]])
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[B, T, B]])
        self.assertTrue(out["mcq"][0]["needs_review"])

    def test_원본_read는_바뀌지_않음(self):
        read = make_read([[0.9, 0, 0]])
        before = copy.deepcopy(read)
        self.layout = make_layout(n_q=1)
        self.run_verify(read, [[B, B, B]])
        self.assertEqual(read, before)

    def test_기준값은_read_config에서_읽음(self):
        read = make_read([[0.55, 0, 0]], marked_min=0.50)
        self.layout = make_layout(n_q=1)
        out = self.run_verify(read, [[S, B, B]])        # 50% 기준이면 55%는 마킹 → CNN과 일치
        self.assertEqual(out["mcq"][0]["answer"], [1])
        self.assertEqual(out["dual_verify"]["marked_min"], 0.50)

    def test_분류_결과_수가_다르면_예외(self):
        read = make_read([[0.9, 0, 0]])
        self.layout = make_layout(n_q=1)
        with self.assertRaises(ValueError):
            dual_verify.verify_sheet(read, self.img, self.layout, classifier=lambda crops: [B])


if __name__ == "__main__":
    unittest.main()
