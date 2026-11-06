# pyright: basic
"""compare.judge_question 판정 규칙 테스트 (CNN·이미지 없이 실행).

실행 (backend 폴더에서):  python -m unittest discover -s dual_verify/tests -t .
"""
import unittest

from dual_verify.compare import MISMATCH, TAPE_NO_MARK, judge_question
from dual_verify.config import (BLANK, CHECK, CIRCLE, OVERFLOW, SMUDGE, STANDARD, TAPED, THIN)

B = BLANK
M = 0.60


class JudgeQuestion(unittest.TestCase):
    def test_규칙마킹_CNN마킹이면_통과(self):
        r = judge_question([0.02, 0.01, 0.91, 0.03, 0.0], [B, B, STANDARD, B, B], M)
        self.assertTrue(r["ok"])
        self.assertEqual(r["reasons"], [])

    def test_규칙마킹_CNN빈칸이면_검수(self):
        r = judge_question([0.02, 0.01, 0.91, 0.03, 0.0], [B, B, B, B, B], M)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reasons"], [{"type": MISMATCH, "choice": 3, "rule": True, "cnn": "빈칸"}])

    def test_규칙마킹_CNN수정테이프여도_검수(self):
        r = judge_question([0.02, 0.01, 0.91, 0.03, 0.0], [B, B, TAPED, B, B], M)
        self.assertFalse(r["ok"])

    def test_규칙미마킹_CNN빈칸이면_통과_무응답문항(self):
        r = judge_question([0.02, 0.01, 0.10, 0.03, 0.0], [B] * 5, M)
        self.assertTrue(r["ok"])

    def test_규칙미마킹_CNN마킹6종이면_모두_검수(self):
        for cls in (STANDARD, THIN, CHECK, CIRCLE, SMUDGE, OVERFLOW):
            with self.subTest(cls=cls):
                r = judge_question([0.02, 0.01, 0.91, 0.56, 0.0], [B, B, STANDARD, cls, B], M)
                self.assertFalse(r["ok"])
                self.assertEqual([x["choice"] for x in r["reasons"]], [4])

    def test_56퍼센트_CNN빈칸이면_통과(self):
        r = judge_question([0.02, 0.01, 0.91, 0.56, 0.0], [B, B, STANDARD, B, B], M)
        self.assertTrue(r["ok"])

    def test_기준값_바꾸면_따라감(self):
        fills, cnn = [0.55, 0.0, 0.0], [STANDARD, B, B]
        self.assertFalse(judge_question(fills, cnn, 0.60)["ok"])   # 55% 는 60% 기준에선 미마킹
        self.assertTrue(judge_question(fills, cnn, 0.50)["ok"])    # 50% 기준이면 마킹

    def test_수정테이프_있고_다른_선지에_마킹이_있으면_통과(self):
        r = judge_question([0.0, 0.0, 0.88, 0.0, 0.0], [B, TAPED, STANDARD, B, B], M)
        self.assertTrue(r["ok"])

    def test_수정테이프_있는데_마킹이_없으면_검수(self):
        r = judge_question([0.0, 0.0, 0.05, 0.0, 0.0], [B, TAPED, B, B, B], M)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reasons"], [{"type": TAPE_NO_MARK, "choices": [2]}])

    def test_복수마킹도_둘다_일치하면_통과(self):
        r = judge_question([0.9, 0.0, 0.85, 0.0, 0.0], [STANDARD, B, THIN, B, B], M)
        self.assertTrue(r["ok"])

    def test_선지번호_사용자지정(self):
        r = judge_question([0.9, 0.0], [B, B], M, choice_nos=[2, 3])
        self.assertEqual(r["reasons"][0]["choice"], 2)

    def test_길이가_다르면_예외(self):
        with self.assertRaises(ValueError):
            judge_question([0.1, 0.2], [B], M)


if __name__ == "__main__":
    unittest.main()
