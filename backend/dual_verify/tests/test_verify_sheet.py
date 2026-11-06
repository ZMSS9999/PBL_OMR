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


N_DIGITS = 2


def make_sid_layout(layout):
    """학번 칸 추가: 자리 N_DIGITS개, 자리마다 숫자 0~9 동그라미가 세로로 나열 (x 고정, y 증가)."""
    layout["student_id"] = {"digits": N_DIGITS, "bubbles": [
        {"digit_index": di, "value": v, "center_mm": [60.0 + 10 * di, 20.0 + 5 * v], "radius_mm": 1.75}
        for di in range(N_DIGITS) for v in range(10)]}
    return layout


def add_sid_read(read, picks, marked_min=0.60, statuses=None):
    """picks: 자리별로 규칙 기반이 마킹한 숫자 (None이면 무응답). read["student_id"] 를 판독 결과처럼 채움."""
    digits = []
    for di, p in enumerate(picks):
        fill = [0.9 if v == p else 0.02 for v in range(10)]
        ans = [] if p is None else [p]
        st = (statuses or {}).get(di) or ("blank" if p is None else "ok")
        digits.append({"digit_index": di, "value": None if st == "ambiguous" else ans, "status": st, "fill": fill})
    raw = "".join(str(d["value"][0]) if d["status"] == "ok" and d["value"] else "?" for d in digits)
    ok = all(d["status"] == "ok" for d in digits)
    read["student_id"] = {"text": raw if ok else None, "raw": raw, "status": "ok" if ok else "review",
                          "needs_review": not ok, "digits": digits}
    read["offsets_mm"]["student_id"] = [0.0, 0.0]
    return read


def classes_for_sid(picks, extra=None):
    """학번 자리별 CNN 분류: 기본은 picks 숫자만 정석 마킹, 나머지 빈칸. extra={(자리, 숫자): 클래스}로 덮어씀."""
    out = []
    for di, p in enumerate(picks):
        row = [S if v == p else B for v in range(10)]
        for (edi, ev), cls in (extra or {}).items():
            if edi == di:
                row[ev] = cls
        out.append(row)
    return out


class VerifyStudentId(unittest.TestCase):
    def setUp(self):
        self.layout = make_sid_layout(make_layout(n_q=1))
        self.img = np.full((700, 700), 255, np.uint8)

    def run_verify(self, read, mcq_classes, sid_classes):
        return dual_verify.verify_sheet(read, self.img, self.layout, classifier=fake(mcq_classes + sid_classes))

    def base(self, picks, **kw):
        return add_sid_read(make_read([[0.9, 0, 0]]), picks, **kw)

    def test_모두_일치하면_학번_그대로(self):
        out = self.run_verify(self.base([1, 2]), [[S, B, B]], classes_for_sid([1, 2]))
        self.assertEqual(out["student_id"]["text"], "12")
        self.assertEqual(out["student_id"]["status"], "ok")
        self.assertFalse(out["summary"]["needs_review"])
        self.assertEqual(out["summary"]["dual_verify"]["review_digits"], [])

    def test_분류기에_객관식_다음_학번_순서로_칸이_전달됨(self):
        seen = []
        read = self.base([1, 2])
        dual_verify.verify_sheet(read, self.img, self.layout,
                                 classifier=lambda crops: (seen.append(len(crops)) or [B] * len(crops)))
        self.assertEqual(seen, [3 + N_DIGITS * 10])      # 객관식 3칸 + 학번 20칸을 한 번에

    def test_규칙마킹_CNN빈칸이면_그_자리_검수(self):
        sid = classes_for_sid([1, 2], extra={(1, 2): B})        # 둘째 자리: CNN이 2를 빈칸으로 봄
        out = self.run_verify(self.base([1, 2]), [[S, B, B]], sid)
        self.assertIsNone(out["student_id"]["text"])
        self.assertEqual(out["student_id"]["raw"], "1?")
        self.assertTrue(out["student_id"]["needs_review"])
        self.assertEqual(out["student_id"]["digits"][1]["status"], "mismatch")
        self.assertEqual(out["summary"]["dual_verify"]["review_digits"], [1])
        self.assertTrue(out["summary"]["needs_review"])

    def test_규칙미마킹_CNN마킹이면_검수(self):
        sid = classes_for_sid([1, 2], extra={(0, 7): THIN})     # 첫 자리 7번: 규칙은 빈칸인데 CNN은 마킹
        out = self.run_verify(self.base([1, 2]), [[S, B, B]], sid)
        self.assertIsNone(out["student_id"]["text"])
        self.assertEqual(out["student_id"]["raw"], "?2")

    def test_애매였던_자리가_CNN과_일치하면_확정(self):
        read = self.base([1, 2], statuses={0: "ambiguous"})
        read["student_id"]["digits"][0]["fill"][1] = 0.56        # 첫 자리 1번이 56%라 애매였음
        sid = classes_for_sid([None, 2])                         # CNN은 첫 자리를 모두 빈칸으로 봄
        out = self.run_verify(read, [[S, B, B]], sid)
        self.assertEqual(out["student_id"]["digits"][0]["status"], "blank")   # 규칙 기준(60%)으로 확정: 무응답
        self.assertEqual(out["student_id"]["status"], "review")               # 무응답 자리가 있어 학번은 검수

    def test_수정테이프만_있고_마킹없는_자리는_검수(self):
        sid = classes_for_sid([None, 2], extra={(0, 4): T})
        read = self.base([None, 2])
        out = self.run_verify(read, [[S, B, B]], sid)
        self.assertEqual(out["student_id"]["digits"][0]["status"], "mismatch")

    def test_수정테이프_있어도_다른_숫자가_마킹돼있으면_통과(self):
        sid = classes_for_sid([1, 2], extra={(0, 4): T})
        out = self.run_verify(self.base([1, 2]), [[S, B, B]], sid)
        self.assertEqual(out["student_id"]["text"], "12")

    def test_학번_판독결과에_digits가_없으면_객관식만_검증(self):
        read = make_read([[0.9, 0, 0]])                          # student_id 에 digits 없음
        out = dual_verify.verify_sheet(read, self.img, self.layout, classifier=fake([[S, B, B]]))
        self.assertEqual(out["mcq"][0]["answer"], [1])


if __name__ == "__main__":
    unittest.main()
