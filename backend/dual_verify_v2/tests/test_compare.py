# pyright: basic
"""v2 compare.judge_question 판정 규칙 테스트 (CNN·이미지 없이 실행).

실행 (backend 폴더에서):  python -m unittest discover -s dual_verify_v2/tests -t .
"""
import unittest

from dual_verify_v2.compare import LOW_CONFIDENCE, TAPE_NO_MARK, judge_question
from dual_verify_v2.config import BLANK as B, CHECK, CIRCLE, OVERFLOW, SMUDGE, STANDARD as S, TAPED as T, THIN

M, C = 0.60, 0.90          # 규칙 기반 마킹 기준, CNN 신뢰도 임계값


class JudgeQuestion(unittest.TestCase):
    def test_60퍼센트_이상은_규칙기반이_마킹으로_확정_CNN_결과_불필요(self):
        r = judge_question([0.02, 0.01, 0.91, 0.03, 0.0], [(B, .99), (B, .99), None, (B, .99), (B, .99)], M, C)
        self.assertTrue(r["ok"])
        self.assertEqual(r["answer"], [3])
        self.assertEqual(r["source"], ["cnn", "cnn", "rule", "cnn", "cnn"])
        self.assertIsNone(r["cnn"][2])

    def test_60퍼센트_이상인_칸은_CNN이_다르게_봐도_규칙기반을_따름(self):
        r = judge_question([0.91, 0.0], [(B, .99), (B, .99)], M, C)       # CNN이 1번을 빈칸이라 해도 무시
        self.assertEqual(r["answer"], [1])
        self.assertTrue(r["ok"])

    def test_60퍼센트_미만_CNN_마킹6종이면_마킹(self):
        for cls in (S, THIN, CHECK, CIRCLE, SMUDGE, OVERFLOW):
            with self.subTest(cls=cls):
                r = judge_question([0.02, 0.10, 0.0], [(B, .99), (cls, .95), (B, .99)], M, C)
                self.assertTrue(r["ok"])
                self.assertEqual(r["answer"], [2])
                self.assertEqual(r["source"][1], "cnn")

    def test_60퍼센트_미만_CNN_빈칸_수정테이프면_마킹_아님(self):
        r = judge_question([0.02, 0.0, 0.88], [(B, .99), (T, .99), None], M, C)
        self.assertTrue(r["ok"])
        self.assertEqual(r["answer"], [3])

    def test_무응답_문항은_모두_빈칸이면_통과(self):
        r = judge_question([0.02, 0.01, 0.10], [(B, .99)] * 3, M, C)
        self.assertTrue(r["ok"])
        self.assertEqual(r["answer"], [])

    def test_56퍼센트_칸도_CNN이_판정(self):
        self.assertEqual(judge_question([0.56, 0.0], [(THIN, .95), (B, .99)], M, C)["answer"], [1])
        self.assertEqual(judge_question([0.56, 0.0], [(B, .95), (B, .99)], M, C)["answer"], [])

    def test_신뢰도_미만이면_검수(self):
        r = judge_question([0.02, 0.01, 0.91], [(B, .99), (THIN, .85), None], M, C, [1, 2, 3])
        self.assertFalse(r["ok"])
        self.assertEqual(r["reasons"], [{"type": LOW_CONFIDENCE, "choice": 2, "cnn": "얇게 채움", "confidence": 0.85}])
        self.assertEqual(r["source"][1], "low_conf")

    def test_신뢰도가_임계값과_같으면_통과(self):
        self.assertTrue(judge_question([0.0, 0.0], [(B, 0.90), (B, .99)], M, 0.90)["ok"])
        self.assertFalse(judge_question([0.0, 0.0], [(B, 0.8999), (B, .99)], M, 0.90)["ok"])

    def test_임계값_바꾸면_따라감(self):
        self.assertFalse(judge_question([0.0], [(B, 0.85)], M, 0.90)["ok"])
        self.assertTrue(judge_question([0.0], [(B, 0.85)], M, 0.80)["ok"])

    def test_기준값_marked_min_바꾸면_따라감(self):
        r = judge_question([0.55, 0.0], [None, (B, .99)], 0.50, C)       # 50% 기준이면 55%는 규칙 기반 마킹
        self.assertEqual(r["answer"], [1])

    def test_수정테이프_있고_마킹없으면_검수(self):
        r = judge_question([0.0, 0.0, 0.05], [(B, .99), (T, .98), (B, .99)], M, C)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reasons"], [{"type": TAPE_NO_MARK, "choices": [2]}])

    def test_수정테이프_있어도_마킹이_있으면_통과(self):
        self.assertTrue(judge_question([0.0, 0.88, 0.0], [(T, .98), None, (B, .99)], M, C)["ok"])
        self.assertTrue(judge_question([0.0, 0.0], [(T, .98), (S, .97)], M, C)["ok"])    # CNN이 찾은 마킹도 인정

    def test_수정테이프_규칙을_끌_수_있음(self):
        r = judge_question([0.0, 0.0], [(T, .98), (B, .99)], M, C, tape_review=False)
        self.assertTrue(r["ok"])

    def test_복수_마킹은_둘다_답에_포함(self):
        r = judge_question([0.9, 0.0, 0.3], [None, (B, .99), (S, .96)], M, C)
        self.assertEqual(r["answer"], [1, 3])

    def test_선지번호_사용자지정(self):
        r = judge_question([0.0, 0.0], [(B, .5), (B, .99)], M, C, nos=[7, 8])
        self.assertEqual(r["reasons"][0]["choice"], 7)

    def test_60퍼센트_미만인데_CNN결과가_없으면_예외(self):
        with self.assertRaises(ValueError):
            judge_question([0.1], [None], M, C)

    def test_길이가_다르면_예외(self):
        with self.assertRaises(ValueError):
            judge_question([0.1, 0.2], [(B, .99)], M, C)


class JudgeQuestionCov(unittest.TestCase):
    """잉크 농도 방식: g(=fills) 또는 cov 가 기준 이상이면 규칙 기반 마킹."""

    def test_cov가_기준_이상이면_g가_낮아도_규칙기반_마킹(self):
        r = judge_question([0.30, 0.02, 0.01], [None, (B, .99), (B, .99)], M, C, covs=[0.75, 0.0, 0.0])
        self.assertTrue(r["ok"])
        self.assertEqual(r["answer"], [1])
        self.assertEqual(r["source"][0], "rule")

    def test_cov가_기준_미만이면_g가_낮을_때_CNN이_판정(self):
        r = judge_question([0.30, 0.02], [(THIN, .96), (B, .99)], M, C, covs=[0.40, 0.0])
        self.assertEqual(r["answer"], [1])
        self.assertEqual(r["source"][0], "cnn")

    def test_covs가_없으면_fills만_봄(self):
        self.assertEqual(judge_question([0.30, 0.02], [(B, .99), (B, .99)], M, C)["answer"], [])


if __name__ == "__main__":
    unittest.main()
